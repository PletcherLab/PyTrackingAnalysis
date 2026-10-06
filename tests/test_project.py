"""Tests for the Project layer (ADR-0005): identity, replicate discovery,
design-match validation, the Combined Analysis, pooled + mixed statistics,
and the Project Report model."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
import yaml

from pytrackinganalysis import project as prj


def _exp_config(levels=("chr", "control"), min_movement=140):
    return {
        "global": {
            "experiment_type": "Valence",
            "tracking_rig": "arena_max",
            "facet_cutoffs": [10, 70],
            "experimental_design_factors": {"genotype": list(levels)},
            "min_transitions": 5,
            "min_movement": min_movement,
        },
        "counting_regions": {"Light": {"alias": "L"},
                             "NoLight": {"alias": "N"}},
        "tracking_regions": {"T_0": {"experimental_factors": "chr"}},
    }


def _summary_frame(seed=0, n_per=6):
    rng = np.random.default_rng(seed)
    treat = ["chr"] * n_per + ["control"] * n_per
    n = len(treat)
    return pd.DataFrame({
        "Treatment": treat,
        "Name": [f"T_{i}_0" for i in range(n)],
        "FinalPI": np.where(np.array(treat) == "chr",
                            rng.normal(0.5, 0.15, n),
                            rng.normal(0.0, 0.15, n)),
        "FinalPercentage": rng.uniform(0.3, 0.8, n),
        "TotalDistancePerMin": rng.uniform(80, 300, n),
        "TransitionsPerMin": rng.uniform(0.2, 3.0, n),
        "LowMovementFlag": [False] * n,
    })


def _make_project(tmp_path, names=("Rep1", "Rep2"), with_analysis=True,
                  config_for=None):
    prj.create_project_file(str(tmp_path), "Proj", notes="notes here")
    for i, name in enumerate(names):
        d = tmp_path / name
        (d / "analysis").mkdir(parents=True)
        # A replicate holds a recording: data/ with at least one xlsx.
        (d / "data").mkdir()
        (d / "data" / "Rec.xlsx").write_bytes(b"")
        cfg = (config_for or (lambda _n: _exp_config()))(name)
        (d / "tracking_config.yaml").write_text(
            yaml.safe_dump(cfg), encoding="utf-8")
        if with_analysis:
            summary = _summary_frame(seed=i)
            summary.to_csv(d / "analysis" / "Rec_Summary.csv", index=False)
            frames = []
            for win in ["(0, 10)", "(10, 70)", "(70, inf)"]:
                f = _summary_frame(seed=i + 10)
                f["FacetRange"] = win
                frames.append(f)
            pd.concat(frames).to_csv(d / "analysis" / "Rec_Summary_Facet.csv",
                                     index=False)
            pd.DataFrame({"Name": [f"x{i}"], "TrackingRegion": ["T_9"],
                          "Treatment": ["chr"], "Transitions": [1]}).to_csv(
                d / "analysis" / "Rec_Excluded.csv", index=False)
    return prj.Project(str(tmp_path))


# ---- identity & validation -------------------------------------------------

def test_marker_and_discovery(tmp_path):
    assert not prj.is_project_dir(str(tmp_path))
    p = _make_project(tmp_path)
    assert prj.is_project_dir(str(tmp_path))
    assert p.name == "Proj" and p.notes == "notes here"
    assert p.experiment_names == ["Rep1", "Rep2"]
    assert p.design_factors == {"genotype": ["chr", "control"]}
    assert p.experiment_type.name == "Valence"


def test_no_experiments_is_an_error(tmp_path):
    prj.create_project_file(str(tmp_path))
    with pytest.raises(ValueError, match="no experiments"):
        prj.Project(str(tmp_path))


def test_design_mismatch_hard_fails(tmp_path):
    def config_for(name):
        if name == "Rep2":
            return _exp_config(levels=("chr", "ctrl"))   # near-miss level
        return _exp_config()

    with pytest.raises(ValueError, match="Rep2.*genotype"):
        _make_project(tmp_path, config_for=config_for)


def test_criteria_differences_warn_not_fail(tmp_path):
    def config_for(name):
        return _exp_config(min_movement=100 if name == "Rep2" else 140)

    p = _make_project(tmp_path, config_for=config_for)
    assert any("min_movement" in w for w in p.warnings)


# ---- combined analysis -----------------------------------------------------

def test_combined_frames_and_build(tmp_path):
    p = _make_project(tmp_path)
    summary, facet, excluded, missing = p.combined_frames()
    assert missing == []
    assert list(summary.columns)[0] == "Experiment"
    assert len(summary) == 24 and summary["Experiment"].nunique() == 2
    assert len(excluded) == 2

    result = p.build_combined_analysis()
    names = sorted(os.path.basename(w) for w in result["written"])
    assert names == ["Proj_Excluded.csv", "Proj_Stats.txt",
                     "Proj_Summary.csv", "Proj_Summary_Facet.csv"]
    text = open(os.path.join(p.analysis_path, "Proj_Stats.txt"),
                encoding="utf-8").read()
    assert "mixed" in text and "Welch" in text


def test_missing_replicate_reported_not_analyzed(tmp_path):
    p = _make_project(tmp_path, names=("Rep1", "Rep2", "Rep3"),
                      with_analysis=False)
    with pytest.raises(ValueError, match="Rep1, Rep2, Rep3"):
        p.build_combined_analysis()


# ---- statistics ------------------------------------------------------------

def test_comparison_rows_pooled_and_mixed(tmp_path):
    p = _make_project(tmp_path)
    summary, facet, _, _ = p.combined_frames()
    rows = p.comparison_rows(summary, facet)
    pi_rows = [r for r in rows if r["metric"] == "FinalPI"]
    assert {r["phase"] for r in pi_rows} == \
        {"Acclimation", "Experiment", "Cooldown"}
    r = pi_rows[0]
    assert r["a"] == "chr" and r["b"] == "control"
    assert r["n_a"] == 12 and r["n_b"] == 12          # pooled across 2 reps
    # The synthetic effect (chr ≈ +0.5 vs control ≈ 0) is detected by both.
    assert r["p_pooled"] < 0.01
    assert r["p_mixed"] is not None and r["p_mixed"] < 0.05


def test_two_treatments_are_not_tested_against_indifference(tmp_path):
    p = _make_project(tmp_path)
    summary, facet, _, _ = p.combined_frames()
    assert p.indifference_rows(summary, facet) == []
    assert "Tests against indifference" not in p.stats_text(summary, facet)


def _single_treatment_project(tmp_path):
    p = _make_project(tmp_path,
                      config_for=lambda _n: _exp_config(levels=("chr",)))
    for i, name in enumerate(p.experiment_names):
        analysis = tmp_path / name / "analysis"
        s = _summary_frame(seed=i)
        s["Treatment"] = "chr"
        s["FinalPI"] = np.random.default_rng(i).normal(0.5, 0.15, len(s))
        s.to_csv(analysis / "Rec_Summary.csv", index=False)
        frames = []
        for win in ["(0, 10)", "(10, 70)", "(70, inf)"]:
            f = s.copy()
            f["FacetRange"] = win
            frames.append(f)
        pd.concat(frames).to_csv(analysis / "Rec_Summary_Facet.csv", index=False)
    return prj.Project(str(tmp_path))


def test_single_treatment_is_tested_against_indifference(tmp_path):
    from pytrackinganalysis.report import model as m

    p = _single_treatment_project(tmp_path)
    summary, facet, _, _ = p.combined_frames()
    assert p.comparison_rows(summary, facet) == []      # no pair to compare
    rows = p.indifference_rows(summary, facet)
    # PI and Percentage per phase; movement has no natural null.
    assert {r["metric"] for r in rows} == {"FinalPI", "FinalPercentage"}
    assert len(rows) == 6
    pi = [r for r in rows if r["metric"] == "FinalPI"][0]
    assert pi["treatment"] == "chr" and pi["n"] == 24   # 12 per rep, pooled
    assert pi["null"] == 0.0 and pi["p"] < 0.001 and pi["significant"]
    # Two replicates: the mixed model's verdict rides beside the pooled one.
    assert pi["p_mixed"] is not None and pi["p_mixed"] < 0.05
    pct = [r for r in rows if r["metric"] == "FinalPercentage"][0]
    assert pct["null"] == 0.5

    text = p.stats_text(summary, facet)
    assert "Tests against indifference" in text and "chr (n=24)" in text

    model = p.build_report_model(include_ai_summary=False)
    tables = {b.title: b for b in model.blocks if isinstance(b, m.Table)}
    table = tables["Tests against indifference"]
    assert "Pooled p" in table.columns and "Mixed p" in table.columns
    assert len(table.rows) == 6


def test_parse_window_handles_inf():
    assert prj.Project.parse_window("(10, 70)") == (10.0, 70.0)
    assert prj.Project.parse_window("(70, inf)") == (70.0, float("inf"))


# ---- report ----------------------------------------------------------------

def test_project_report_model_and_pdf(tmp_path):
    from pytrackinganalysis.report import model as m
    from pytrackinganalysis.report import render

    p = _make_project(tmp_path)
    model = p.build_report_model()
    kinds = [type(b).__name__ for b in model.blocks]
    assert kinds[0] == "Cover"
    tables = [b for b in model.blocks if isinstance(b, m.Table)]
    titles = [t.title for t in tables]
    assert "Statistical comparisons (pooled + mixed model)" in titles
    assert "Per-replicate summary" in titles
    stats = [t for t in tables if "pooled" in (t.title or "")][0]
    assert "Mixed p" in stats.columns
    reps = [t for t in tables if t.title == "Per-replicate summary"][0]
    assert [r[0] for r in reps.rows] == ["Rep1", "Rep2"]
    figures = [b for b in model.blocks if isinstance(b, m.Figure)]
    assert len(figures) >= 3                       # pooled pub figures

    out = p.create_report()
    assert os.path.basename(out) == "Proj_report.pdf"
    assert open(out, "rb").read(5) == b"%PDF-"


def test_ai_narrative_round_trip_and_embed(tmp_path):
    p = _make_project(tmp_path)
    assert p.read_ai_summary() is None
    p.write_ai_summary("Pooled results agree across replicates.",
                       "TestAI", "model-x")
    provenance, body = p.read_ai_summary()
    assert "TestAI model-x" in provenance and "agree" in body
    model = p.build_report_model()
    paragraphs = [b.text for b in model.blocks
                  if type(b).__name__ == "Paragraph"]
    assert any("agree across replicates" in t for t in paragraphs)
    # Rebuilding the Combined Analysis deletes the stale narrative.
    p.build_combined_analysis()
    assert p.read_ai_summary() is None


# ---- UI: hub project view & plot-editor project mode -----------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception:  # pragma: no cover
            pytest.skip("Qt unavailable")
    return app


class _FakeLoadedExperiment:
    """Stands in for a loaded Experiment: the Hub routes experiment-level
    actions by its directory and names it by its arena (ADR-0008)."""

    def __init__(self, directory):
        from pathlib import Path
        from types import SimpleNamespace

        self.project_directory = str(directory)
        self.arena = SimpleNamespace(experiment_name=Path(directory).name)


def test_hub_project_view_populates_and_marks_the_loaded_replicate(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    card = win._cards["projectview"]
    assert not card.isHidden()
    assert win._exp_table.rowCount() == 2
    assert "Proj" in win._projectview_summary.text()
    assert win._exp_table.item(0, 0).text() == "Rep1"
    assert win._exp_table.item(0, 2).text() == "12"      # flies

    # project.yaml is not a tracking config; the Project card no longer
    # offers a tracking-config picker at all.
    assert win._config_path() is None

    # The LOADED experiment marks its row — the selection stays on the
    # Project, so it can no longer be what the highlight follows.
    win._exp = _FakeLoadedExperiment(tmp_path / "Rep1")
    win._refresh_project_view()
    selected = win._exp_table.selectedItems()
    assert selected and selected[0].text() == "Rep1"
    win._exp = None
    win.close()


def test_editor_opens_project_dir_with_pooled_data(qapp, tmp_path):
    from pytrackinganalysis.apps.plot_editor import PlotEditorWindow

    _make_project(tmp_path)
    win = PlotEditorWindow()
    win.open_project(str(tmp_path))
    assert "Project: Proj (2 replicates)" in win._project_label.text()
    win._render_preview()
    assert win.preview.pixmap() is not None
    assert not win.preview.pixmap().isNull()
    data = win._current_data()
    assert "Experiment" in data.columns and len(data) == 24 * 3

    win.mark_check.setChecked(True)
    win._read_controls()
    assert win._current_spec().mark_experiments is True
    win._experiment = None
    win._project = None
    win.close()


def test_create_project_file_merges_and_preserves_unknown_keys(tmp_path):
    (tmp_path / "project.yaml").write_text(
        "name: Old\nnotes: keep me?\ncustom_key: survives\n", encoding="utf-8")
    prj.create_project_file(str(tmp_path), "NewName", notes="new notes")
    meta = yaml.safe_load((tmp_path / "project.yaml").read_text())
    assert meta["name"] == "NewName"
    assert meta["notes"] == "new notes"
    assert meta["custom_key"] == "survives"     # unknown keys preserved
    # Clearing notes removes the key rather than writing an empty string.
    prj.create_project_file(str(tmp_path), "NewName", notes="")
    meta = yaml.safe_load((tmp_path / "project.yaml").read_text())
    assert "notes" not in meta


def test_a_default_name_is_not_stored_so_a_copied_project_yaml_follows_its_folder(
        tmp_path):
    """A stored default pinned the name: project.yaml copied into a sibling
    folder wrote that sibling's report as '<original>_report.pdf'."""
    original, sibling = tmp_path / "LineA", tmp_path / "LineB"
    original.mkdir()
    sibling.mkdir()
    prj.create_project_file(str(original), "LineA")       # typed == folder
    meta = yaml.safe_load((original / "project.yaml").read_text())
    assert "name" not in meta
    (sibling / "project.yaml").write_text(
        (original / "project.yaml").read_text(), encoding="utf-8")
    for d in (original, sibling):
        (d / "Rep1" / "data").mkdir(parents=True)
        (d / "Rep1" / "data" / "Rec.xlsx").write_bytes(b"")
        (d / "Rep1" / "tracking_config.yaml").write_text(
            "global: {tracking_type: TRACKER}\ntracking_regions: {}\n")
    assert prj.Project(str(sibling)).name == "LineB"

    # "." names the folder it stands for, not ".".
    import os
    cwd = os.getcwd()
    try:
        os.chdir(sibling)
        prj.create_project_file(".", "LineB")
    finally:
        os.chdir(cwd)
    assert "name" not in yaml.safe_load((sibling / "project.yaml").read_text())

    # A name that differs from the folder is a choice, and is kept — first.
    prj.create_project_file(str(original), "Pretty name", notes="n")
    text = (original / "project.yaml").read_text()
    assert text.startswith("name: Pretty name")
    assert prj.Project(str(original)).name == "Pretty name"


def test_project_info_dialog_creates_and_edits(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import ProjectInfoDialog

    target = tmp_path / "NewProj"
    dlg = ProjectInfoDialog(None, start_dir=str(target))
    assert dlg.windowTitle() == "Create project"
    dlg.name_edit.setText("My Study")
    dlg.notes_edit.setPlainText("Three replicates planned.")
    dlg._save()
    assert dlg.saved_dir == str(target)
    meta = yaml.safe_load((target / "project.yaml").read_text())
    assert meta["name"] == "My Study"
    assert meta["notes"] == "Three replicates planned."
    # The dialog writes the shared design too (type defaults seeded).
    g = meta["design"]["global"]
    assert g["experiment_type"] == "Valence"
    assert g["facet_cutoffs"] == [10, 70]
    assert g["min_transitions"] == 5 and g["min_movement"] == 140
    assert meta["design"]["counting_regions"] == ["Light", "NoLight"]

    # Reopening on the same directory switches to edit mode, prefilled.
    dlg2 = ProjectInfoDialog(None, start_dir=str(target))
    assert dlg2.windowTitle() == "Edit project"
    assert dlg2.name_edit.text() == "My Study"
    assert "replicates" in dlg2.notes_edit.toPlainText()
    dlg2.close()
    dlg.close()


def test_project_card_has_both_creation_flows(qapp, tmp_path):
    from PyQt6.QtWidgets import QPushButton

    from pytrackinganalysis.apps.hub import HubWindow

    win = HubWindow()
    ## Both flows live on the Project tile, one section apart: the project
    ## itself in Create/Load, the experiment in Experiments.
    project_texts = [b.text() for b in
                     win._cards["project"].findChildren(QPushButton)]
    experiment_texts = [b.text() for b in
                        win._cards["experiments"].findChildren(QPushButton)]
    assert any("Create project" in t for t in project_texts)
    assert any("Create experiment" in t for t in experiment_texts)
    assert not any("Create experiment" in t for t in project_texts)
    win.close()


def test_the_selection_never_leaves_the_project(qapp, tmp_path, monkeypatch):
    """The Project is the selection; the loaded experiment is the other,
    separate context. So there is no drilling in and nothing to come back
    from — no 'Up to project' button exists."""
    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path)
    loaded: list = []
    def _fake_load(self, directory=None, *, run_qc=True, reveal=None):
        loaded.append(str(directory))

    monkeypatch.setattr(HubWindow, "_load_experiment", _fake_load)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    card = win._cards["projectview"]
    assert not card.isHidden()
    assert not hasattr(win, "_up_btn")

    # Choosing a replicate directory selects the Project that contains it.
    win._set_project_dir(str(tmp_path / "Rep1"))
    assert str(win._project_dir) == str(tmp_path)
    assert win._exp_table.rowCount() == 2
    project = win._current_project()
    assert project is not None and project.name == "Proj"

    # Double-clicking a row loads THAT replicate and leaves the selection
    # where it was: the project-level actions never retarget.
    win._open_selected_replicate(win._exp_table.item(1, 0))
    assert loaded == [str(tmp_path / "Rep2")]
    assert str(win._project_dir) == str(tmp_path)
    assert not card.isHidden()
    win.close()


def test_experiment_actions_follow_the_loaded_experiment(qapp, tmp_path):
    """Experiment-level actions take their directory from the loaded
    experiment, not from the selected directory (which is the Project)."""
    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()

    # Nothing loaded: no experiment context at a Project root.
    assert win._experiment_dir() is None
    assert win._config_path() is None

    win._exp = _FakeLoadedExperiment(tmp_path / "Rep1")
    win._refresh_scripts()
    assert win._experiment_dir() == tmp_path / "Rep1"
    assert win._config_path() == tmp_path / "Rep1" / "tracking_config.yaml"
    # …while the project-level context is untouched.
    assert win._project_root() == tmp_path
    win._exp = None
    win.close()


def test_project_report_refresh_leaves_nothing_loaded(qapp, tmp_path, monkeypatch):
    """Report refresh re-analyzes every replicate underneath the loaded one, so
    the in-memory experiment is dropped before the run rather than left stale."""
    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    win._exp = _FakeLoadedExperiment(tmp_path / "Rep1")
    win._btn_run_analysis.setEnabled(True)      # as a real load leaves it

    spawned: list = []
    monkeypatch.setattr(HubWindow, "_spawn_task",
                        lambda self, label, fn: spawned.append(label))
    win._project_report()

    assert spawned == ["Create report"]
    assert win._exp is None
    assert not win._btn_run_analysis.isEnabled()   # no subject to analyze
    win.close()


def test_project_table_refreshes_when_a_task_finishes(qapp, tmp_path):
    # Running an analysis on a loaded replicate must update its row from
    # "not analyzed" — every task completion refreshes the project view.
    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1", "Rep2"), with_analysis=False)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    assert win._exp_table.item(0, 2).text() == "not analyzed"

    # Simulate what a finished Run Analysis leaves behind for Rep1…
    analysis = tmp_path / "Rep1" / "analysis"
    _summary_frame().to_csv(analysis / "Rec_Summary.csv", index=False)
    # …and the task-finished signal landing on the GUI thread.
    win._on_task_finished(object())
    assert win._exp_table.item(0, 2).text() == "12"      # flies now counted
    assert win._exp_table.item(1, 2).text() == "not analyzed"
    win.close()


def test_plot_editor_is_project_level_only(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps.plot_editor import PlotEditorWindow

    ## Project and the standalone experiment must be SIBLINGS: a stray
    ## experiment dir inside the project would be discovered as a replicate.
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    win = PlotEditorWindow()

    # A replicate inside a Project redirects up to the Project.
    win.open_project(str(proj / "Rep1"))
    assert "Project: Proj" in win._project_label.text()

    # A standalone experiment (no enclosing Project) is refused with guidance.
    alone = tmp_path / "alone"
    alone.mkdir()
    (alone / "tracking_config.yaml").write_text(
        yaml.safe_dump(_exp_config()), encoding="utf-8")
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: messages.append(a)))
    before = win._project_dir
    win.open_project(str(alone))
    assert messages and "Project level" in messages[0][2]
    assert win._project_dir == before          # nothing was opened
    win._experiment = None
    win._project = None
    win.close()


def test_hub_plot_editor_launches_with_project_root(qapp, tmp_path, monkeypatch):
    import subprocess

    from PyQt6.QtWidgets import QPushButton

    from pytrackinganalysis.apps import hub as hub_mod

    _make_project(tmp_path)
    launched = []

    class _Proc:
        pid = 12345

        def poll(self):
            return 0

    monkeypatch.setattr(hub_mod.subprocess, "Popen",
                        lambda args, **k: launched.append(args) or _Proc())
    win = hub_mod.HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    # Choosing a replicate directory selects its Project, so the Analysis
    # card's Plot editor button opens on the Project either way.
    win._set_project_dir(str(tmp_path / "Rep1"))
    card = win._cards["projectview"]
    btn = [b for b in card.findChildren(QPushButton)
           if b.text() == "Plot editor…"][0]
    btn.click()
    assert launched and launched[0][-1] == str(tmp_path)   # project root, not Rep1

    # The top directory card no longer offers an experiment-scoped editor.
    top = win._cards["project"]
    assert not [b for b in top.findChildren(QPushButton)
                if "Plot editor" in b.text()]
    win.close()


# ---- design authority in project.yaml (ADR-0005 amendment) -----------------

_DESIGN = {
    "global": {
        "experiment_type": "Valence",
        "facet_cutoffs": [10, 70],
        "facet_labels": ["Acclimation", "Experiment", "Cooldown"],
        "experimental_design_factors": {"genotype": ["chr", "control"]},
        "min_transitions": 5,
        "min_movement": 140,
    },
    "counting_regions": ["Light", "NoLight"],
}


def _design_project(tmp_path, config_for=None, names=("Rep1", "Rep2")):
    import copy
    prj.create_project_file(str(tmp_path), "DProj", design=copy.deepcopy(_DESIGN))
    for name in names:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        cfg = (config_for or (lambda _n: _exp_config()))(name)
        (d / "tracking_config.yaml").write_text(
            yaml.safe_dump(cfg), encoding="utf-8")
    return prj.Project(str(tmp_path))


def test_design_authority_accepts_conforming_and_resolved_defaults(tmp_path):
    def config_for(name):
        cfg = _exp_config()
        if name == "Rep2":
            # Omitting keys the type defaults still matches the design:
            # min_transitions resolves to 5, cutoffs to [10, 70].
            del cfg["global"]["min_transitions"]
            del cfg["global"]["facet_cutoffs"]
            # Aliases are experiment-specific — different ones are fine.
            cfg["counting_regions"]["Light"]["alias"] = "Lux, L1"
        return cfg

    p = _design_project(tmp_path, config_for)
    assert p.design_factors == {"genotype": ["chr", "control"]}
    assert p.experiment_type.name == "Valence"


def test_design_authority_rejects_mismatches(tmp_path):
    def bad_levels(name):
        cfg = _exp_config()
        if name == "Rep2":
            cfg["global"]["experimental_design_factors"] = {
                "genotype": ["chr", "ctrl"]}
        return cfg

    with pytest.raises(ValueError, match="Rep2.*genotype"):
        _design_project(tmp_path, bad_levels)


def test_design_authority_rejects_changed_global_value(tmp_path):
    def bad_cutoffs(name):
        cfg = _exp_config()
        if name == "Rep1":
            cfg["global"]["min_movement"] = 90     # differs from design's 140
        return cfg

    with pytest.raises(ValueError, match="Rep1.*min_movement"):
        _design_project(tmp_path, bad_cutoffs)


def test_design_authority_enforces_counting_region_names(tmp_path):
    def wrong_regions(name):
        cfg = _exp_config()
        if name == "Rep2":
            cfg["counting_regions"] = {"Bright": {"alias": "L"},
                                       "NoLight": {"alias": "N"}}
        return cfg

    with pytest.raises(ValueError, match="Rep2.*counting_regions"):
        _design_project(tmp_path, wrong_regions)


def test_empty_project_with_design_is_legal_and_scaffolds(tmp_path):
    import copy
    prj.create_project_file(str(tmp_path), "Empty", design=copy.deepcopy(_DESIGN))
    p = prj.Project(str(tmp_path))
    assert p.experiment_names == []
    assert p.experiment_type.name == "Valence"
    assert p.design_factors == {"genotype": ["chr", "control"]}

    cfg = p.scaffold_replicate_config()
    g = cfg["global"]
    assert g["experiment_type"] == "Valence"
    assert g["facet_cutoffs"] == [10, 70]
    assert g["min_transitions"] == 5 and g["min_movement"] == 140
    assert g["experimental_design_factors"] == {"genotype": ["chr", "control"]}
    assert list(cfg["counting_regions"]) == ["Light", "NoLight"]

    # A replicate written from the scaffold validates against the design.
    d = tmp_path / "Rep1"
    d.mkdir()
    (d / "tracking_config.yaml").write_text(yaml.safe_dump(cfg),
                                            encoding="utf-8")
    p2 = prj.Project(str(tmp_path))
    assert p2.experiment_names == ["Rep1"]


def test_legacy_projects_without_design_still_agree_based(tmp_path):
    # No design section -> the original agreement validation still applies.
    def config_for(name):
        return _exp_config(levels=("chr", "ctrl") if name == "Rep2"
                           else ("chr", "control"))

    with pytest.raises(ValueError, match="Rep2"):
        _make_project(tmp_path, config_for=config_for)


def test_dialog_design_round_trip_and_inference(qapp, tmp_path):
    from PyQt6.QtWidgets import QTableWidgetItem

    from pytrackinganalysis.apps.hub import ProjectInfoDialog

    # Creating with explicit factors writes them into the design…
    target = tmp_path / "P1"
    dlg = ProjectInfoDialog(None, start_dir=str(target))
    dlg.factors_table.insertRow(0)
    dlg.factors_table.setItem(0, 0, QTableWidgetItem("genotype"))
    dlg.factors_table.setItem(0, 1, QTableWidgetItem("chr, control"))
    dlg._save()
    meta = yaml.safe_load((target / "project.yaml").read_text())
    assert meta["design"]["global"]["experimental_design_factors"] == \
        {"genotype": ["chr", "control"]}

    # …and the written project is loadable + scaffolds a valid replicate.
    p = prj.Project(str(target))
    cfg = p.scaffold_replicate_config()
    assert cfg["global"]["experimental_design_factors"] == \
        {"genotype": ["chr", "control"]}

    # Wrapping existing experiments: the dialog infers the design from the
    # first replicate's config (migration path).
    wrap = tmp_path / "wrap"
    (wrap / "Old1").mkdir(parents=True)
    (wrap / "Old1" / "tracking_config.yaml").write_text(
        yaml.safe_dump(_exp_config()), encoding="utf-8")
    dlg2 = ProjectInfoDialog(None, start_dir=str(wrap))
    dlg2._prefill_from_dir()
    assert dlg2.factors_table.rowCount() == 1
    assert dlg2.factors_table.item(0, 0).text() == "genotype"
    assert dlg2.counting_edit.text() == "Light, NoLight"
    dlg2._save()
    p2 = prj.Project(str(wrap))       # validates Old1 against inferred design
    assert p2.experiment_names == ["Old1"]
    dlg.close()
    dlg2.close()


# ---- per-experiment configs inside a Project -------------------------------

def _project_with_bare_dirs(tmp_path, names=("FirstOne", "SecondOne")):
    """The common starting point: a Project created around folders that have
    no tracking_config.yaml yet."""
    import copy
    prj.create_project_file(str(tmp_path), "Bare",
                            design=copy.deepcopy(_DESIGN))
    for name in names:
        data = tmp_path / name / "data"
        data.mkdir(parents=True)
        # An experiment directory is one with data/ + at least one xlsx.
        (data / f"{name}.xlsx").write_bytes(b"")
    (tmp_path / "analysis").mkdir()          # a project output, not a replicate
    return prj.Project(str(tmp_path))


def test_unconfigured_dirs_lists_only_experiment_shaped_folders(tmp_path):
    p = _project_with_bare_dirs(tmp_path)
    assert p.experiment_names == []          # membership is by config file
    assert p.unconfigured_dirs() == ["FirstOne", "SecondOne"]

    # A subdirectory is a candidate iff data/ holds at least one .xlsx —
    # everything else (outputs, caches, unrelated folders) is ignored.
    (tmp_path / "figures").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "random_notes").mkdir()                    # no data/ at all
    (tmp_path / "EmptyData" / "data").mkdir(parents=True)  # data/, no xlsx
    (tmp_path / "EmptyData" / "data" / "readme.txt").write_text("x")
    assert prj.Project(str(tmp_path)).unconfigured_dirs() == \
        ["FirstOne", "SecondOne"]

    # Case-insensitive on the workbook extension.
    (tmp_path / "EmptyData" / "data" / "run.XLSX").write_bytes(b"")
    assert "EmptyData" in prj.Project(str(tmp_path)).unconfigured_dirs()


def test_scaffold_replicate_adopts_an_existing_folder(tmp_path):
    p = _project_with_bare_dirs(tmp_path)
    path = p.scaffold_replicate("FirstOne")
    assert path == str(tmp_path / "FirstOne" / "tracking_config.yaml")
    cfg = yaml.safe_load((tmp_path / "FirstOne" /
                          "tracking_config.yaml").read_text())
    assert cfg["global"]["experiment_type"] == "Valence"
    assert cfg["global"]["experimental_design_factors"] == \
        {"genotype": ["chr", "control"]}

    # The folder is now a replicate, and it validates against the design.
    p2 = prj.Project(str(tmp_path))
    assert p2.experiment_names == ["FirstOne"]
    assert p2.unconfigured_dirs() == ["SecondOne"]

    # A second scaffold never overwrites hand-made region assignments.
    with pytest.raises(FileExistsError):
        p2.scaffold_replicate("FirstOne")

    # A name that doesn't exist yet gets the directory and its data/ folder.
    p2.scaffold_replicate("Third")
    assert (tmp_path / "Third" / "data").is_dir()


def test_hub_project_dir_has_no_tracking_config_of_its_own(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import HubWindow

    _project_with_bare_dirs(tmp_path)
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()

    # A Project root has no tracking_config.yaml — Validate / Scripts / Load
    # must not invent one. The Project card edits project.yaml instead.
    assert win._config_path() is None
    assert win._btn_edit_cfg.isEnabled()
    assert win._btn_edit_cfg.text().startswith("Edit")

    # The config-less folders are listed (discovery would hide them) and the
    # summary says how to fix them.
    names = [win._exp_table.item(r, 0).text()
             for r in range(win._exp_table.rowCount())]
    assert names == ["FirstOne", "SecondOne"]
    assert win._exp_table.item(0, 1).text() == "missing"
    assert "without a config" in win._projectview_summary.text()
    win.close()


def test_experiment_configs_dialog_creates_and_edits(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps import hub as hub_mod

    _project_with_bare_dirs(tmp_path)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k:
                                     QMessageBox.StandardButton.Yes))
    launched: list = []

    class _Proc:
        pid = 4321

        def poll(self):
            return 0

    monkeypatch.setattr(hub_mod.subprocess, "Popen",
                        lambda args, **k: launched.append(args) or _Proc())

    win = hub_mod.HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    dlg = hub_mod.ExperimentConfigsDialog(win, win._current_project())
    assert [dlg._table.item(r, 1).text() for r in range(dlg._table.rowCount())] \
        == ["missing", "missing"]

    dlg._create_all_missing()
    for name in ("FirstOne", "SecondOne"):
        assert (tmp_path / name / "tracking_config.yaml").is_file()
    # Reloaded in place: both rows now carry a config, and the Project sees
    # them as replicates (they validate against the design).
    assert [dlg._table.item(r, 1).text() for r in range(dlg._table.rowCount())] \
        == ["yes", "yes"]
    assert prj.Project(str(tmp_path)).experiment_names == \
        ["FirstOne", "SecondOne"]

    # Editing opens the Config Editor on that experiment directory — not the
    # project root, which has no config.
    dlg._table.selectRow(0)
    dlg._edit_selected()
    assert launched and launched[0][-1] == str(tmp_path / "FirstOne")
    dlg.close()
    win.close()


def test_hub_double_click_creates_and_opens_the_config(qapp, tmp_path,
                                                       monkeypatch):
    # The "Create config" prompt says the new config will be created AND
    # opened; it used to only write the file and select the directory.
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    # An experiment-shaped folder (data/ + xlsx) without a config.
    fresh_data = tmp_path / "Fresh" / "data"
    fresh_data.mkdir(parents=True)
    (fresh_data / "Fresh.xlsx").write_bytes(b"")
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    launched = []
    monkeypatch.setattr(
        HubWindow, "_launch_subapp",
        lambda self, which, directory=None: launched.append((which, directory)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()

    rows = {win._exp_table.item(r, 0).text(): r
            for r in range(win._exp_table.rowCount())}
    win._open_selected_replicate(win._exp_table.item(rows["Fresh"], 0))
    assert (tmp_path / "Fresh" / "tracking_config.yaml").is_file()
    assert launched == [("config", str(tmp_path / "Fresh"))]
    win.close()


def test_hub_create_experiment_anchors_on_the_project(qapp, tmp_path,
                                                     monkeypatch):
    from PyQt6.QtWidgets import QInputDialog

    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Rep2", True)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    _stub_finish_dialog(monkeypatch, win)

    # With a replicate open, the new replicate belongs to the Project — it
    # used to be created inside the currently selected directory.
    win._set_project_dir(str(tmp_path / "Rep1"))
    win._create_experiment()
    assert (tmp_path / "Rep2" / "tracking_config.yaml").is_file()
    assert not (tmp_path / "Rep1" / "Rep2").exists()
    win.close()


def test_hub_create_experiment_inherits_the_project_design(qapp, tmp_path,
                                                           monkeypatch):
    """Nothing but the name is asked for: the config comes from project.yaml,
    so the new replicate conforms to the shared design by construction."""
    import yaml as _yaml
    from PyQt6.QtWidgets import QInputDialog

    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Rep2", True)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    _stub_finish_dialog(monkeypatch, win)
    win._create_experiment()

    made = tmp_path / "Rep2"
    assert (made / "data").is_dir()
    cfg = _yaml.safe_load((made / "tracking_config.yaml").read_text())
    sibling = _yaml.safe_load((tmp_path / "Rep1" /
                               "tracking_config.yaml").read_text())
    # Design-conformant by construction: the same type and the same factors
    # as the replicate it will be pooled with.
    assert cfg["global"]["experiment_type"] == \
        sibling["global"]["experiment_type"]
    assert cfg["global"].get("experimental_design_factors") == \
        sibling["global"].get("experimental_design_factors")
    # And the Project loads it as a conforming replicate.
    assert "Rep2" in prj.Project(str(tmp_path)).experiment_names
    # The row shows up as a configured replicate straight away.
    names = [win._exp_table.item(r, 0).text()
             for r in range(win._exp_table.rowCount())]
    assert "Rep2" in names
    win.close()


def _stub_finish_dialog(monkeypatch, win):
    """Silence the edit-or-copy offer that follows Create experiment.

    It is a modal, so a test that reaches it without this hangs rather than
    fails — and its own behaviour is covered by the tests below."""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", lambda _box: 0)
    monkeypatch.setattr(type(win), "_launch_subapp",
                        lambda self, *a, **k: None)


def test_new_experiment_offers_edit_or_copy_and_edits_by_default(
        qapp, tmp_path, monkeypatch):
    """After Create experiment the scaffold still needs a rig and region
    treatments, so both ways of finishing it are offered."""
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Rep2", True)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()

    seen = {}

    def _exec(box):
        seen["text"] = box.text()
        seen["labels"] = [b.text() for b in box.buttons()]
        seen["default"] = box.defaultButton().text()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    launched = []
    monkeypatch.setattr(type(win), "_launch_subapp",
                        lambda self, *a, **k: launched.append((a, k)))
    win._create_experiment()

    assert seen["labels"] == ["Edit config…", "Copy config from…"]
    assert seen["default"] == "Edit config…"
    # clickedButton() is None when exec is stubbed, so this is the edit path.
    assert launched[-1][0][0] == "config"
    assert launched[-1][1]["directory"] == str(tmp_path / "Rep2")
    win.close()


def _create_then_copy(qapp, tmp_path, monkeypatch, chosen):
    """Run Create experiment and take the 'Copy config from…' branch, with
    the file chooser answering *chosen*. Returns (hub, warnings, launched,
    information messages)."""
    from PyQt6.QtWidgets import (QFileDialog, QInputDialog, QMessageBox)

    from pytrackinganalysis.apps.hub import HubWindow

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Rep2", True)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()

    def _exec(box):
        for button in box.buttons():
            if "Copy" in button.text():
                box.setDefaultButton(button)
                ## QMessageBox.clickedButton() reads the button the box was
                ## closed with; done() through the button's own click is the
                ## only way to set it without a real user.
                button.click()
                return 0
        raise AssertionError("no Copy button")

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (chosen, "")))
    told = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: told.append(a[2])))
    warned = []
    monkeypatch.setattr(type(win), "_warn",
                        lambda self, message: warned.append(message))
    launched = []
    monkeypatch.setattr(type(win), "_launch_subapp",
                        lambda self, *a, **k: launched.append((a, k)))
    win._create_experiment()
    return win, warned, launched, told


def test_new_experiment_can_copy_a_conforming_config(qapp, tmp_path,
                                                     monkeypatch):
    """The common case for replicate two onwards: the same rig and the same
    region treatments as one that already works."""
    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    source = tmp_path / "Rep1" / "tracking_config.yaml"
    # Something only the copy can carry, so the assert cannot pass by luck.
    original = yaml.safe_load(source.read_text())
    original["global"]["mm_per_pixel_marker"] = "copied"
    source.write_text(yaml.safe_dump(original), encoding="utf-8")

    win, warned, launched, _told = _create_then_copy(
        qapp, tmp_path, monkeypatch, str(source))
    copied = yaml.safe_load(
        (tmp_path / "Rep2" / "tracking_config.yaml").read_text())
    assert copied["global"]["mm_per_pixel_marker"] == "copied"
    assert not warned
    # A copied config is finished, so the editor is NOT forced open.
    assert not launched
    assert "Rep2" in prj.Project(str(tmp_path)).experiment_names
    win.close()


def test_a_copy_that_breaks_the_design_is_refused_and_the_editor_opens(
        qapp, tmp_path, monkeypatch):
    """Checked before it is written: a non-conforming replicate makes the
    whole Project refuse to load, and the user would have to undo it by
    hand."""
    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    scaffolded = yaml.safe_load(
        (tmp_path / "Rep1" / "tracking_config.yaml").read_text())

    ## OUTSIDE the Project: a config dropped in a subdirectory of it would
    ## make that subdirectory a non-conforming replicate, and the Project
    ## would stop loading before Create experiment ever ran.
    foreign = tmp_path.parent / f"{tmp_path.name}_elsewhere" / \
        "tracking_config.yaml"
    foreign.parent.mkdir(exist_ok=True)
    wrong = yaml.safe_load(yaml.safe_dump(scaffolded))
    wrong["global"]["experimental_design_factors"] = {"genotype": ["w1118"]}
    foreign.write_text(yaml.safe_dump(wrong), encoding="utf-8")

    win, warned, launched, _told = _create_then_copy(
        qapp, tmp_path, monkeypatch, str(foreign))
    # Nothing was written: the scaffold is untouched and still conforming.
    kept = yaml.safe_load(
        (tmp_path / "Rep2" / "tracking_config.yaml").read_text())
    assert kept["global"].get("experimental_design_factors") == \
        scaffolded["global"].get("experimental_design_factors")
    assert warned and "does not fit this Project's design" in warned[-1]
    assert "genotype" in warned[-1]
    # And the Project still loads, which is the point of checking first.
    assert "Rep2" in prj.Project(str(tmp_path)).experiment_names
    # Flagged, then handed to the editor.
    assert launched[-1][0][0] == "config"
    win.close()


def test_a_cancelled_copy_flags_the_user_and_opens_the_editor(
        qapp, tmp_path, monkeypatch):
    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    win, _warned, launched, told = _create_then_copy(qapp, tmp_path,
                                                    monkeypatch, "")
    assert told and "No file chosen" in told[-1]
    assert launched[-1][0][0] == "config"
    assert (tmp_path / "Rep2" / "tracking_config.yaml").is_file()
    win.close()


def test_hub_create_experiment_defers_to_initialize_for_existing_dirs(
        qapp, tmp_path, monkeypatch):
    """The two cases stay disjoint: Create makes the directory, so a folder
    that is already there is the other button's job."""
    from PyQt6.QtWidgets import QInputDialog

    from pytrackinganalysis.apps.hub import HubWindow

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    (tmp_path / "Loose").mkdir()
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Loose", True)))
    win = HubWindow(initial_project=str(tmp_path))
    qapp.processEvents()
    warned = []
    monkeypatch.setattr(type(win), "_warn",
                        lambda self, message: warned.append(message))
    win._create_experiment()
    assert not (tmp_path / "Loose" / "tracking_config.yaml").exists()
    assert "Initialize existing directory" in warned[-1]
    win.close()
