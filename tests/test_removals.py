"""Experimenter-declared region removal (ADR-0010).

Covers the three files the feature owns — the per-experiment
``removed_regions.yaml`` sidecar, the Removal Sheet that writes into it, and
the merged exclusion list that reaches Arena, the CSVs and both reports.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from pytrackinganalysis import Experiment as ExperimentMod
from pytrackinganalysis import batch as batch_mod
from pytrackinganalysis import project as prj
from pytrackinganalysis import removals

from test_experiment_integration import write_project  # noqa: F401
from test_project import _make_project  # noqa: F401


# ---- the sidecar ----------------------------------------------------------

def test_sidecar_round_trip_and_default_reason(tmp_path):
    removals.write_removals(tmp_path, {"T_14": "dead at ~20 min", "T_22": ""})
    assert removals.read_removals(tmp_path) == {
        "T_14": "dead at ~20 min",
        # A declaration with no reason still carries something into the audit.
        "T_22": removals.DEFAULT_REASON,
    }
    # The file is the declaration: emptying it removes the file, so a sidecar
    # never outlives what it declared.
    assert removals.write_removals(tmp_path, {}) is None
    assert not os.path.exists(removals.removals_path(tmp_path))
    assert removals.read_removals(tmp_path) == {}


def test_a_broken_sidecar_never_blocks_a_run(tmp_path):
    (tmp_path / removals.REMOVALS_FILENAME).write_text(
        "removed_regions: [this is a list, not a mapping\n", encoding="utf-8")
    assert removals.read_removals(tmp_path) == {}


def test_region_match_respects_the_underscore_boundary():
    """A bare ``startswith`` makes T_1 swallow the well next door."""
    assert removals.name_in_region("T_1_0", "T_1")
    assert removals.name_in_region("T_1", "T_1")          # a counter
    assert not removals.name_in_region("T_10_0", "T_1")
    assert removals.expand_regions(["T_1"], ["T_1_0", "T_10_0"]) == {
        "T_1": ["T_1_0"]}


# ---- the Removal Sheet ----------------------------------------------------

def _batch(tmp_path, projects=("P1", "P2")):
    for name in projects:
        (tmp_path / name).mkdir()
        _make_project(tmp_path / name)
    return tmp_path


def _sheet(root, rows, name="removed_regions.csv"):
    pd.DataFrame(rows).to_csv(root / name, index=False)
    return root / name


def test_sheet_headers_are_flexible_and_reason_defaults(tmp_path):
    path = _sheet(tmp_path, [{"Project": "P1", "Replicate": "Rep1",
                              "Tracking Region": "T_1", "Note": ""}])
    rows = removals.read_sheet(path)
    assert len(rows) == 1
    row = rows[0]
    assert (row.project, row.experiment, row.region) == ("P1", "Rep1", "T_1")
    assert row.reason == removals.DEFAULT_REASON


def test_a_sheet_without_the_required_columns_is_rejected(tmp_path):
    path = _sheet(tmp_path, [{"fly": "T_1", "why": "dead"}])
    with pytest.raises(ValueError, match="missing the experiment, region"):
        removals.read_sheet(path)


def test_apply_writes_sidecars_and_reports_every_row(tmp_path):
    root = _batch(tmp_path)
    _sheet(root, [
        {"project": "P1", "experiment": "Rep1", "region": "T_0",
         "reason": "dead at ~20 min"},
        {"project": "P1", "experiment": "Rep2", "region": "T_0", "reason": ""},
        {"project": "Nope", "experiment": "Rep1", "region": "T_0",
         "reason": "x"},
        {"project": "P1", "experiment": "Ghost", "region": "T_0", "reason": "x"},
        {"project": "P1", "experiment": "Rep1", "region": "T_99", "reason": "x"},
    ])
    result = removals.apply_sheet(root)
    counts = result["counts"]
    assert counts["applied"] == 2
    assert counts["unknown project"] == 1
    assert counts["unknown experiment"] == 1
    # A region the config never declared is a typo, not a removal.
    assert counts["unknown region"] == 1

    assert removals.read_removals(root / "P1" / "Rep1") == {
        "T_0": "dead at ~20 min"}
    assert removals.read_removals(root / "P1" / "Rep2") == {
        "T_0": removals.DEFAULT_REASON}
    assert len(result["written"]) == 2


def test_reapplying_keeps_the_standing_reason_and_flags_a_conflict(tmp_path):
    root = _batch(tmp_path, projects=("P1",))
    removals.write_removals(root / "P1" / "Rep1", {"T_0": "dead, tube leaked"})
    _sheet(root, [{"project": "P1", "experiment": "Rep1", "region": "T_0",
                   "reason": "dead"}])
    logged: list[str] = []
    result = removals.apply_sheet(root, log=logged.append)

    # The sheet is re-applied on every Batch Run; letting it win would keep
    # resetting a reason refined in the removals window.
    assert result["counts"]["conflict"] == 1
    assert removals.read_removals(root / "P1" / "Rep1") == {
        "T_0": "dead, tube leaked"}
    assert not result["written"]
    assert any("conflict" in line for line in logged)

    # Re-applying an identical row is quiet, and still writes nothing.
    _sheet(root, [{"project": "P1", "experiment": "Rep1", "region": "T_0",
                   "reason": "dead, tube leaked"}])
    again = removals.apply_sheet(root)
    assert again["counts"] == {"already declared": 1}


def test_a_project_root_sheet_needs_no_project_column(tmp_path):
    _make_project(tmp_path)
    _sheet(tmp_path, [{"experiment": "Rep1", "region": "T_0", "reason": "dead"}])
    result = prj.Project(str(tmp_path)).apply_removal_sheet(log=lambda _m: None)
    assert result["counts"] == {"applied": 1}
    assert removals.read_removals(tmp_path / "Rep1") == {"T_0": "dead"}


def test_batch_without_a_sheet_is_a_no_op(tmp_path):
    root = _batch(tmp_path, projects=("P1",))
    assert batch_mod.apply_removal_sheet(root)["sheet"] is None


def test_an_unreadable_sheet_never_aborts_a_batch(tmp_path):
    root = _batch(tmp_path, projects=("P1",))
    _sheet(root, [{"nonsense": 1}])
    logged: list[str] = []
    result = batch_mod.apply_removal_sheet(root, log=logged.append)
    assert "error" in result
    assert any("could not be applied" in line for line in logged)


# ---- the merged exclusion list ---------------------------------------------

@pytest.fixture
def experiment(tmp_path):
    write_project(tmp_path / "proj")
    return tmp_path / "proj"


def test_a_removed_region_leaves_the_analysis_population(experiment):
    exp = ExperimentMod.Experiment(str(experiment))
    assert len(exp.arena.summarize()) == 2

    removals.write_removals(experiment, {"T_2": "dead at ~20 min"})
    exp = ExperimentMod.Experiment(str(experiment))
    summary = exp.arena.summarize()
    assert list(summary["Name"]) == ["T_1_0"]

    # A Custom Experiment has no transition criterion, so no column for one —
    # the audit is narrower, never falsely empty (ADR-0010).
    excluded = exp.excluded_flies
    assert list(excluded.columns) == ["Name", "TrackingRegion", "Treatment",
                                      "Reason"]
    row = excluded.iloc[0]
    assert row["Name"] == "T_2_0" and row["TrackingRegion"] == "T_2"
    assert row["Reason"] == "Removed: dead at ~20 min"
    assert row["Treatment"] == "Treated"
    assert excluded.attrs["n_removed"] == 1


def test_removals_apply_with_no_type_criterion_at_all(experiment):
    """ADR-0003 said Custom Experiments are never filtered; ADR-0010 amends
    that — never by *policy*, but a declared removal filters anything."""
    exp = ExperimentMod.Experiment(str(experiment))
    assert exp.experiment_type.is_custom
    assert exp.excluded_flies is None          # untouched projects unchanged

    removals.write_removals(experiment, {"T_1": "escaped"})
    exp = ExperimentMod.Experiment(str(experiment))
    assert exp.arena.excluded_names == {"T_1_0"}


def test_one_row_per_fly_naming_both_causes(experiment, monkeypatch):
    """A fly the experimenter removed that also failed the automatic rule is
    one row, its reason naming both, the observation first."""
    removals.write_removals(experiment, {"T_2": "dead at ~20 min"})
    exp = ExperimentMod.Experiment(str(experiment))

    def fake_exclusions(_experiment):
        frame = pd.DataFrame([["T_2_0", "T_2", "Treated", 1.0]],
                             columns=["Name", "TrackingRegion", "Treatment",
                                      "Transitions"])
        frame.attrs.update({"min_transitions": 5, "window": (2, 4),
                            "phase_label": "Experiment"})
        return frame

    monkeypatch.setattr(type(exp.experiment_type), "compute_exclusions",
                        staticmethod(fake_exclusions))
    exp.refresh_exclusions()

    excluded = exp.excluded_flies
    assert len(excluded) == 1
    assert excluded.iloc[0]["Reason"] == \
        "Removed: dead at ~20 min; Low transitions"
    # Counting stays one-per-fly everywhere it already is.
    assert excluded.attrs["n_removed"] == 1
    assert excluded.attrs["n_low_transitions"] == 0
    assert "4 fly(ies)" not in exp.exclusion_summary()
    assert "1 fly(ies)" in exp.exclusion_summary()


def test_writing_removals_refilters_the_arena_immediately(experiment):
    """Without this the removals window appears to do nothing until the next
    full analysis, because Arena's excluded set was computed at load."""
    exp = ExperimentMod.Experiment(str(experiment))
    assert len(exp.arena.summarize()) == 2

    exp.write_removed_regions({"T_1": "escaped"})
    assert exp.arena.excluded_names == {"T_1_0"}
    assert list(exp.arena.summarize()["Name"]) == ["T_2_0"]

    # Un-removing is an explicit act, and it takes effect just as directly.
    exp.write_removed_regions({})
    assert exp.arena.excluded_names == set()
    assert len(exp.arena.summarize()) == 2


def test_a_declaration_matching_nothing_warns_but_never_raises(experiment):
    removals.write_removals(experiment, {"T_99": "dead"})
    exp = ExperimentMod.Experiment(str(experiment))
    assert exp.excluded_flies.attrs["unmatched_regions"] == ["T_99"]
    assert len(exp.arena.summarize()) == 2          # nothing was excluded
    assert any("T_99" in note and "no tracker" in note
               for note in exp.exclusion_notes())


def test_the_excluded_csv_carries_the_reason(experiment):
    removals.write_removals(experiment, {"T_2": "dead at ~20 min"})
    exp = ExperimentMod.Experiment(str(experiment))
    exp.run_analysis()
    written = os.path.join(str(experiment), "analysis", "Synthetic_Excluded.csv")
    frame = pd.read_csv(written)
    assert list(frame["Reason"]) == ["Removed: dead at ~20 min"]


# ---- reports ---------------------------------------------------------------

def test_the_experiment_report_names_the_removed_flies(experiment):
    from pytrackinganalysis import report_figures
    from pytrackinganalysis.report import model as m

    removals.write_removals(experiment, {"T_2": "dead at ~20 min",
                                         "T_99": "typo"})
    exp = ExperimentMod.Experiment(str(experiment))
    blocks = report_figures.build_exclusion_blocks(exp)

    table = next(b for b in blocks if isinstance(b, m.Table))
    assert table.columns[-1] == "Reason"
    assert table.rows[0][-1] == "Removed: dead at ~20 min"
    assert "1 fly(ies)" in blocks[0].text
    assert "removed by the experimenter" in blocks[0].text
    # The unmatched declaration reaches the report, not just the run log.
    assert any("T_99" in b.text for b in blocks if isinstance(b, m.Paragraph))


def test_the_project_report_lists_every_excluded_fly(tmp_path):
    project = _make_project(tmp_path)
    excluded = pd.DataFrame({
        "Experiment": ["Rep1", "Rep2"],
        "Name": ["T_1_0", "T_2_0"],
        "TrackingRegion": ["T_1", "T_2"],
        "Treatment": ["chr", "control"],
        "Reason": ["Removed: dead", None],
    })
    from pytrackinganalysis.report import model as _m

    blocks = project._exclusion_blocks(excluded, _m)
    table = next(b for b in blocks if isinstance(b, _m.Table))
    assert table.columns == ["Experiment", "Fly", "Region", "Treatment",
                            "Reason"]
    assert table.rows[0] == ["Rep1", "T_1_0", "T_1", "chr", "Removed: dead"]
    # A file written before the Reason column existed says so, rather than
    # implying a cause it never recorded.
    assert table.rows[1][-1] == "(not recorded)"
    assert "1 of them removed by the experimenter" in blocks[0].text

    empty = project._exclusion_blocks(pd.DataFrame(), _m)
    assert len(empty) == 1 and "No flies were excluded" in empty[0].text


# ---- staleness -------------------------------------------------------------

def test_a_replicate_is_stale_until_its_removals_reach_the_analysis(tmp_path):
    project = _make_project(tmp_path)
    assert project.experiment_status("Rep1")["stale"] is False

    removals.write_removals(tmp_path / "Rep1", {"T_0": "dead"})
    status = project.experiment_status("Rep1")
    assert status["stale"] is True
    assert status["declared"] == 1

    # Once the saved analysis records the removal, it is current again.
    pd.DataFrame({"Name": ["T_0_0"], "TrackingRegion": ["T_0"],
                  "Treatment": ["chr"], "Transitions": [1],
                  "Reason": ["Removed: dead"]}).to_csv(
        tmp_path / "Rep1" / "analysis" / "Rec_Excluded.csv", index=False)
    status = project.experiment_status("Rep1")
    assert status["stale"] is False
    assert status["removed"] == 1


def test_skip_analyzed_still_reruns_a_stale_replicate(tmp_path, monkeypatch):
    """An unattended run must not pool data the experimenter threw out."""
    project = _make_project(tmp_path)
    removals.write_removals(tmp_path / "Rep1", {"T_0": "dead"})
    ran: list[str] = []

    class _FakeExp:
        def run_analysis(self, **_kwargs):
            pass

        def create_report(self, **_kwargs):
            pass

    def fake_load(name):
        ran.append(name)
        return _FakeExp()

    monkeypatch.setattr(project, "load_experiment", fake_load)
    project.run_all(skip_analyzed=True, log=lambda _m: None)
    assert ran == ["Rep1"]          # Rep2 is analyzed and unchanged


# ---- the removals window ---------------------------------------------------

from test_project import qapp  # noqa: E402, F401  (fixture reuse)


def test_the_window_lists_only_regions_the_config_declares(tmp_path, qapp):  # noqa: F811
    """The design's plate is the addressable set — and reading it costs no
    raw-data parsing, so a Project of eighty replicates opens instantly."""
    from pytrackinganalysis.apps.removals_dialog import RemovalsDialog

    _make_project(tmp_path)                      # two replicates, region T_0
    removals.write_removals(tmp_path / "Rep1", {"T_0": "dead at ~20 min"})

    dialog = RemovalsDialog(str(tmp_path))
    try:
        rows = [(dialog.table.item(r, 1).text(), dialog.table.item(r, 2).text())
                for r in range(dialog.table.rowCount())]
        assert rows == [("Rep1", "T_0"), ("Rep2", "T_0")]
        # The standing declaration comes back ticked, with its reason.
        assert dialog.table.item(0, 0).checkState().value == 2   # Checked
        assert dialog.table.item(0, 5).text() == "dead at ~20 min"
        assert dialog.table.item(1, 0).checkState().value == 0   # Unchecked
    finally:
        dialog.deleteLater()


def test_saving_the_window_writes_each_replicates_sidecar(tmp_path, qapp):  # noqa: F811
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTableWidgetItem

    from pytrackinganalysis.apps.removals_dialog import RemovalsDialog

    _make_project(tmp_path)
    dialog = RemovalsDialog(str(tmp_path))
    try:
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        dialog.table.setItem(1, 5, QTableWidgetItem("escaped during transfer"))
        dialog._save()
        assert dialog.changed_experiments == ["Rep2"]
        assert removals.read_removals(tmp_path / "Rep2") == {
            "T_0": "escaped during transfer"}
        # Rep1 was untouched, so it has no sidecar at all.
        assert removals.read_removals(tmp_path / "Rep1") == {}

        # A ticked region with no reason typed still carries one.
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog._save()
        assert removals.read_removals(tmp_path / "Rep1") == {
            "T_0": removals.DEFAULT_REASON}
    finally:
        dialog.deleteLater()


def test_the_batch_table_opens_removals_on_right_click_not_double_click(tmp_path, qapp):  # noqa: F811
    """Double-click is taken: it selects that Project (ADR-0009)."""
    from PyQt6.QtCore import Qt

    from pytrackinganalysis.apps.hub import HubWindow

    win = HubWindow()
    try:
        assert win._batch_table.contextMenuPolicy() == \
            Qt.ContextMenuPolicy.CustomContextMenu
        assert win._batch_table.receivers(
            win._batch_table.customContextMenuRequested) == 1
        # The apply-sheet button stays disabled until a Batch with a sheet is
        # selected: browsing must never write.
        assert not win._btn_batch_removals.isEnabled()
    finally:
        win.close()


def test_a_batch_run_applies_the_sheet_before_running(tmp_path):
    """Unattended runs honour the experimenter's notes: the sheet is stamped
    into the experiments before the first Project script starts, so even a run
    whose Projects fail leaves the declarations in place."""
    root = _batch(tmp_path, projects=("P1",))
    _sheet(root, [{"project": "P1", "experiment": "Rep1", "region": "T_0",
                   "reason": "dead at ~20 min"}])
    logged: list[str] = []
    batch_mod.run_batch(root, log=logged.append)

    assert removals.read_removals(root / "P1" / "Rep1") == {
        "T_0": "dead at ~20 min"}
    assert any("removed_regions.csv" in line for line in logged)


def test_no_data_means_an_empty_well_not_an_excluded_one(tmp_path, qapp):  # noqa: F811
    """The saved summary is the *filtered* one, so a region excluded by an
    earlier run is absent from it. Reading only that file would label a fly
    you removed last week "no data", as if its well had been empty."""
    from pytrackinganalysis.apps.removals_dialog import project_regions

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    analysis = tmp_path / "Rep1" / "analysis"
    analysis.mkdir(exist_ok=True)

    # Not analyzed yet: nothing is claimed about any region.
    assert project_regions(str(tmp_path)) == [("Rep1", "T_0", "chr", None)]

    # Analyzed, and T_0 is absent from the summary only because it was removed.
    pd.DataFrame({"Name": ["T_1_0"], "TrackingRegion": ["T_1"]}).to_csv(
        analysis / "Rec_Summary.csv", index=False)
    pd.DataFrame({"Name": ["T_0_0"], "TrackingRegion": ["T_0"],
                  "Reason": ["Removed: dead"]}).to_csv(
        analysis / "Rec_Excluded.csv", index=False)
    assert project_regions(str(tmp_path)) == [("Rep1", "T_0", "chr", True)]

    # A well that produced nothing at all is the real "no data".
    pd.DataFrame({"Name": ["T_1_0"], "TrackingRegion": ["T_1"],
                  "Reason": ["Low transitions"]}).to_csv(
        analysis / "Rec_Excluded.csv", index=False)
    assert project_regions(str(tmp_path)) == [("Rep1", "T_0", "chr", False)]


def test_apply_sheet_sits_small_and_right_of_the_folder_picker(tmp_path, qapp):  # noqa: F811
    """A secondary action on the folder you just chose, not a peer of Run
    batch: same row, right-justified, visibly smaller."""
    from PyQt6.QtWidgets import QPushButton

    from pytrackinganalysis.apps.hub import HubWindow

    win = HubWindow()
    win.resize(1400, 900)
    win.show()
    qapp.processEvents()
    try:
        win._open_panel("batch")
        qapp.processEvents()
        card = win._cards["batch"]
        pick = next(b for b in card.findChildren(QPushButton)
                    if b.text().startswith("Choose batch"))
        apply_btn = win._btn_batch_removals

        # One row: vertically centred on each other, apply to the right.
        assert pick.y() + pick.height() / 2 == \
            apply_btn.y() + apply_btn.height() / 2
        assert apply_btn.x() > pick.x() + pick.width()
        # Right-justified against the card's inner edge.
        assert apply_btn.x() + apply_btn.width() >= card.width() - 20
        # Smaller than the primary picker beside it.
        assert apply_btn.height() < pick.height()
        assert apply_btn.font().pointSizeF() < pick.font().pointSizeF()
    finally:
        win.close()


def test_the_window_has_its_own_help_topic(tmp_path, qapp):  # noqa: F811
    """Removals have enough behaviour of their own — effect on the analysis,
    re-run rules, sheet merge rules — to warrant a page, not a paragraph on
    the Project-actions one."""
    from pytrackinganalysis.help import get_topic, topic_ids
    from pytrackinganalysis.help.render import load_topic_markdown
    from pytrackinganalysis.apps.removals_dialog import RemovalsDialog

    assert "removed_regions" in topic_ids()
    text = load_topic_markdown("removed_regions")
    for expected in ("all-or-nothing", "removed_regions.yaml",
                     "removed_regions.csv", "conflict", "re-run needed",
                     "Not filtered", "_Excluded.csv"):
        assert expected in text, expected
    assert get_topic("removed_regions").title == "Removed regions"

    _make_project(tmp_path)
    dialog = RemovalsDialog(str(tmp_path))
    try:
        from pytrackinganalysis.help import HelpButton

        button = dialog.findChild(HelpButton)
        assert button is not None and button._topic_id == "removed_regions"
    finally:
        dialog.deleteLater()


def test_a_sheet_row_names_a_project_by_its_path_under_the_batch(tmp_path):
    """A Batch names its Members by path relative to its root, so a sheet
    written for a Batch with grouping folders does the same."""
    nested = tmp_path / "Sept2026" / "ProjA"
    nested.mkdir(parents=True)
    _make_project(nested)
    _sheet(tmp_path, [{"project": "Sept2026/ProjA", "experiment": "Rep1",
                       "region": "T_0", "reason": "dead"}])

    assert removals.apply_sheet(tmp_path)["counts"] == {"applied": 1}
    assert removals.read_removals(nested / "Rep1") == {"T_0": "dead"}


def test_a_sheet_excel_saved_on_windows_is_read(tmp_path):
    """Excel's "CSV (Comma delimited)" on Windows writes cp1252, not UTF-8.
    One accented reason made the whole sheet fail to read, so none of its
    removals reached the experiments."""
    path = tmp_path / "removed_regions.csv"
    path.write_bytes("experiment,region,reason\nRep1,T_1,mort\xe9 \x96 20 min\n"
                     .encode("latin-1"))
    [row] = removals.read_sheet(path)
    assert (row.experiment, row.region) == ("Rep1", "T_1")
    assert row.reason == "mort\xe9 – 20 min"
