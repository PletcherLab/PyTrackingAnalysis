"""Removing regions of one replicate in the Config Editor and the QC Viewer
(ADR-0021).

Both windows write the replicate's own ``removed_regions.yaml`` (ADR-0010),
never the config, and only what was changed in them, so a removal another
window declared meanwhile is kept. Neither writes a labdata run folder's file
or one that cannot be read.
"""

from __future__ import annotations

import os

import pytest
import yaml

from pytrackinganalysis import Experiment as ExperimentMod
from pytrackinganalysis import removals

from test_about_section import read_config, recording
from test_experiment_integration import write_project
from test_member_names_config import analysis_dir, pointer
from test_referenced_data import RUN1, make_run, managed_root, run_dir
from test_project import _make_project

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as err:  # noqa: BLE001
            pytest.skip(f"Qt is unavailable: {err}")
    return app


@pytest.fixture
def dialogs(monkeypatch):
    """Record every message box; ``answer["question"]`` is what one asking
    returns."""
    seen = {"warning": [], "critical": [], "information": [], "question": []}
    answer = {"question": QMessageBox.StandardButton.Cancel}
    for kind in seen:
        monkeypatch.setattr(
            QMessageBox, kind,
            staticmethod(lambda *args, _kind=kind, **kwargs: (
                seen[_kind].append(args) or answer.get(_kind,
                                                       QMessageBox.StandardButton.Ok))))
    seen["answer"] = answer
    return seen


@pytest.fixture
def open_editor(qapp, dialogs, monkeypatch, tmp_path):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    windows = []

    def _open(target):
        window = ConfigEditorWindow(str(target))
        windows.append(window)
        return window

    yield _open
    for window in windows:
        window.close()


def declare(directory, declared) -> None:
    removals.write_removals(directory, declared)


def on_disk(directory) -> dict:
    return removals.read_removals(directory)


def is_dirty(window) -> bool:
    window._refresh_dirty()
    return bool(window._dirty_label.text())


# ---- the grid's cells ------------------------------------------------------

def _row(tab, region) -> int:
    return tab.region_names().index(region)


def tick(tab, region, reason=None) -> None:
    row = _row(tab, region)
    tab.table.item(row, tab._removed_col()).setCheckState(Qt.CheckState.Checked)
    if reason is not None:
        tab.table.item(row, tab._reason_col()).setText(reason)


def untick(tab, region) -> None:
    tab.table.item(_row(tab, region), tab._removed_col()).setCheckState(
        Qt.CheckState.Unchecked)


def shown(tab, region) -> tuple[bool, str]:
    """(ticked, reason) as the row shows them."""
    row = _row(tab, region)
    check = tab.table.item(row, tab._removed_col())
    return (check.checkState() == Qt.CheckState.Checked,
            tab.table.item(row, tab._reason_col()).text())


def enabled(item) -> bool:
    return bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)


# ---- the shared rule -------------------------------------------------------

def test_a_save_writes_only_what_the_window_changed(tmp_path):
    declare(tmp_path, {"T_1": "dead", "T_3": "declared meanwhile"})
    merged = removals.update_removals(
        tmp_path, before={"T_1": "dead"}, after={"T_1": "dead", "T_2": ""})
    assert merged == on_disk(tmp_path) == {
        "T_1": "dead", "T_3": "declared meanwhile", "T_2": removals.DEFAULT_REASON}

    ## Unticking is this window's explicit act, so it is written.
    assert removals.update_removals(tmp_path, before=merged,
                                    after={"T_3": "declared meanwhile"}) == \
        {"T_3": "declared meanwhile"}


def test_a_save_that_changes_nothing_writes_nothing(tmp_path):
    assert removals.update_removals(tmp_path, {}, {}) == {}
    assert not os.path.exists(removals.removals_path(tmp_path))
    ## A blank reason and Undefined are the same declaration.
    assert removals.update_removals(tmp_path, {"T_1": ""},
                                    {"T_1": removals.DEFAULT_REASON}) == {}


def test_a_run_folder_and_an_unreadable_file_are_refused(tmp_path):
    assert removals.edit_refusal(tmp_path) == ""
    assert "Save the config first" in removals.edit_refusal(None)

    run = tmp_path / "run"
    run.mkdir()
    (run / removals.LABSTORE_RUN_MARKER).write_text("run_id: R-1\n")
    assert "run folder" in removals.edit_refusal(run)
    with pytest.raises(ValueError, match="run folder"):
        removals.update_removals(run, {}, {"T_1": "dead"})
    assert not os.path.exists(removals.removals_path(run))

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / removals.REMOVALS_FILENAME).write_text("- a list, not a mapping\n")
    assert "cannot be read" in removals.edit_refusal(broken)
    with pytest.raises(ValueError):
        removals.update_removals(broken, {}, {"T_1": "dead"})
    assert (broken / removals.REMOVALS_FILENAME).read_text() == \
        "- a list, not a mapping\n"


# ---- the Config Editor's grid ----------------------------------------------

def test_the_grid_shows_what_the_replicate_declares(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    declare(member, {"T_1": "dead at ~20 min"})
    window = open_editor(member)
    tab = window._tracking_tab

    assert shown(tab, "T_1") == (True, "dead at ~20 min")
    assert shown(tab, "T_2") == (False, "")
    row = _row(tab, "T_2")
    assert not enabled(tab.table.item(row, tab._reason_col()))  # tick first
    assert enabled(tab.table.item(row, tab._removed_col()))
    assert str(member / removals.REMOVALS_FILENAME) in tab.removals_note.text()
    assert not is_dirty(window)
    ## The columns are never the config's.
    assert all(set(entry) <= {"experimental_factors", "x_location_multiplier",
                              "y_location_multiplier"}
               for entry in window._dump_config()["tracking_regions"].values())


def test_ticking_a_region_is_saved_to_the_replicate_not_the_config(
        tmp_path, open_editor, dialogs):
    member = recording(tmp_path / "Rep1")
    config_before = read_config(member)
    window = open_editor(member)
    tab = window._tracking_tab

    tick(tab, "T_2", "escaped during transfer")
    assert is_dirty(window)
    window._save()

    assert on_disk(member) == {"T_2": "escaped during transfer"}
    assert read_config(member) == config_before
    assert not is_dirty(window)
    [saved] = dialogs["information"]
    assert "removed_regions.yaml" in saved[2]


def test_ticked_with_no_reason_is_undefined_and_unticking_all_deletes_the_file(
        tmp_path, open_editor, dialogs):
    member = recording(tmp_path / "Rep1")
    window = open_editor(member)
    tab = window._tracking_tab

    tick(tab, "T_1")
    window._save()
    assert on_disk(member) == {"T_1": removals.DEFAULT_REASON}

    untick(tab, "T_1")
    assert is_dirty(window)
    window._save()
    assert not os.path.exists(removals.removals_path(member))
    assert "was deleted" in dialogs["information"][-1][2]


def test_ticking_again_brings_back_the_reason_it_was_read_with(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    declare(member, {"T_1": "dead"})
    tab = open_editor(member)._tracking_tab

    untick(tab, "T_1")
    assert shown(tab, "T_1") == (False, "")
    tick(tab, "T_1")
    assert shown(tab, "T_1") == (True, "dead")
    assert not tab.removals_changed()


def test_a_save_keeps_what_another_window_declared_meanwhile(
        tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    declare(member, {"T_1": "dead"})
    window = open_editor(member)
    tab = window._tracking_tab

    declare(member, {"T_1": "dead", "T_2": "from the Hub's window"})
    tick(tab, "T_1", "dead at 20 min")
    window._save()

    assert on_disk(member) == {"T_1": "dead at 20 min", "T_2": "from the Hub's window"}
    assert shown(tab, "T_2") == (True, "from the Hub's window")   # shown once saved
    assert not is_dirty(window)


def test_a_declared_region_with_no_row_is_kept_and_listed(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    declare(member, {"T_9": "never loaded", "T_2": "dead"})
    window = open_editor(member)
    tab = window._tracking_tab
    assert "T_9 (never loaded)" in tab.removals_note.text()

    ## Deleting a removed region's row does not un-remove it, and neither
    ## does renaming one.
    tab.table.selectRow(_row(tab, "T_2"))
    tab._remove_row()
    tab.table.item(_row(tab, "T_1"), 0).setText("T_5")
    assert "T_2 (dead)" in tab.removals_note.text()
    assert shown(tab, "T_5") == (False, "")
    tick(tab, "T_5", "escaped")
    window._save()
    assert on_disk(member) == {"T_9": "never loaded", "T_2": "dead", "T_5": "escaped"}


def test_ticks_outlive_every_rebuild_of_the_rows(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    tab = open_editor(member)._tracking_tab
    tick(tab, "T_2", "escaped")

    tab._on_factors_changed({"Genotype": ["Ctrl", "Treated"], "Sex": ["F", "M"]})
    assert shown(tab, "T_2") == (True, "escaped")
    tab.merge_region_names(["T_2", "T_1"])
    assert shown(tab, "T_2") == (True, "escaped")
    assert tab.removed_regions() == {"T_2": "escaped"}


def test_a_member_folder_keeps_its_removals_in_the_member(tmp_path, open_editor):
    store = managed_root(tmp_path)
    make_run(store, RUN1, regions=("T_1", "T_2"))
    member = pointer(analysis_dir(store) / RUN1, RUN1)
    window = open_editor(member)
    tab = window._tracking_tab
    assert window._current_path.parent == run_dir(store, RUN1)   # the run's config

    tick(tab, "T_1", "dead")
    window._save()
    assert on_disk(member) == {"T_1": "dead"}
    assert not os.path.exists(removals.removals_path(run_dir(store, RUN1)))


def test_a_labstore_run_folder_shows_its_removals_but_never_writes_them(
        tmp_path, open_editor):
    store = managed_root(tmp_path)
    run = make_run(store, RUN1, regions=("T_1", "T_2"))
    (run / removals.LABSTORE_RUN_MARKER).write_text(f"run_id: {RUN1}\n")
    declare(run, {"T_1": "dead"})
    before = (run / removals.REMOVALS_FILENAME).read_text()
    window = open_editor(run / "tracking_config.yaml")
    tab = window._tracking_tab

    assert shown(tab, "T_1") == (True, "dead")
    assert not enabled(tab.table.item(_row(tab, "T_2"), tab._removed_col()))
    assert "run folder" in tab.removals_note.text()
    tab.table.item(_row(tab, "T_2"), tab._removed_col()).setCheckState(
        Qt.CheckState.Checked)                                # never by a click
    assert not tab.removals_changed()
    window._save()
    assert (run / removals.REMOVALS_FILENAME).read_text() == before


def test_an_unreadable_file_is_never_overwritten(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    (member / removals.REMOVALS_FILENAME).write_text("- not a mapping\n")
    window = open_editor(member)
    tab = window._tracking_tab
    assert "cannot be read" in tab.removals_note.text()
    window._save()
    assert (member / removals.REMOVALS_FILENAME).read_text() == "- not a mapping\n"


def test_a_folder_with_no_config_yet_keeps_removals_beside_it(tmp_path, open_editor):
    member = recording(tmp_path / "Rep1")
    (member / "tracking_config.yaml").unlink()
    window = open_editor(member)
    tab = window._tracking_tab
    assert not tab._removals_refusal
    assert str(member) in tab.removals_note.text()


# ---- the QC Viewer ---------------------------------------------------------

@pytest.fixture
def replicate(tmp_path):
    """A replicate whose config declares T_1..T_3 and whose recording has
    flies in T_1 and T_2 only: T_3 is an empty well."""
    root = write_project(tmp_path / "Rep1", regions=("T_1", "T_2"))
    config = yaml.safe_load((root / "tracking_config.yaml").read_text())
    config["tracking_regions"]["T_3"] = {"experimental_factors": "Ctrl"}
    (root / "tracking_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    return root


def panel_rows(panel) -> list[tuple]:
    table = panel.table
    return [(table.item(r, 1).text(), table.item(r, 2).text(),
             table.item(r, 0).checkState() == Qt.CheckState.Checked,
             table.item(r, 3).text()) for r in range(table.rowCount())]


def panel_row(panel, region) -> int:
    return [row[0] for row in panel_rows(panel)].index(region)


def test_the_panel_lists_every_region_with_its_flies(qapp, replicate):
    from pytrackinganalysis.apps._removed_regions_panel import (
        RemovedRegionsPanel, config_region_names)

    declare(replicate, {"T_9": "never loaded"})
    panel = RemovedRegionsPanel()
    panel.load(replicate, config_region_names(replicate), ["T_1_0", "T_2_0", "T_2_1"])

    assert panel_rows(panel) == [
        ("T_1", "1", False, ""), ("T_2", "2", False, ""),
        ("T_3", "no data", False, ""), ("T_9", "not in the config", True, "never loaded")]
    assert not panel.save_button.isEnabled()

    panel.table.item(panel_row(panel, "T_3"), 0).setCheckState(Qt.CheckState.Checked)
    panel.table.item(panel_row(panel, "T_3"), 3).setText("empty well")
    assert panel.save_button.isEnabled() and "(unsaved)" in panel.note.text()
    assert panel.save()
    assert on_disk(replicate) == {"T_9": "never loaded", "T_3": "empty well"}

    panel.select_region_of("T_2_1")
    assert panel.table.selectionModel().selectedRows()[0].row() == panel_row(panel, "T_2")


@pytest.fixture
def qc_window(qapp, dialogs, tmp_path, monkeypatch, replicate):
    """The QC Viewer with *replicate* loaded, as its loader leaves it."""
    from pathlib import Path

    from pytrackinganalysis.apps.qc_viewer import QcViewerWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    window = QcViewerWindow()
    path = Path(replicate)
    window._project_dir = path
    window._on_project_loaded(path, ExperimentMod.Experiment(str(path)))
    yield window
    dialogs["answer"]["question"] = QMessageBox.StandardButton.Discard
    window.close()


def test_saving_in_the_qc_viewer_removes_the_flies_at_once(qc_window, replicate):
    panel = qc_window._removals_panel
    panel.table.item(panel_row(panel, "T_2"), 0).setCheckState(Qt.CheckState.Checked)
    panel.table.item(panel_row(panel, "T_2"), 3).setText("dead at ~20 min")
    panel.save_button.click()

    assert on_disk(replicate) == {"T_2": "dead at ~20 min"}
    excluded = qc_window._exp.excluded_flies
    assert set(excluded["Name"]) == {"T_2_0"}
    assert "dead at ~20 min" in excluded["Reason"].iloc[0]


def test_closing_with_unsaved_removals_asks_first(qc_window, replicate, dialogs):
    panel = qc_window._removals_panel
    panel.table.item(panel_row(panel, "T_1"), 0).setCheckState(Qt.CheckState.Checked)

    dialogs["answer"]["question"] = QMessageBox.StandardButton.Cancel
    assert not qc_window.close()                      # stays open
    dialogs["answer"]["question"] = QMessageBox.StandardButton.Save
    assert qc_window.close()
    assert on_disk(replicate) == {"T_1": removals.DEFAULT_REASON}
    assert len(dialogs["question"]) == 2


# ---- the Hub's removals window ---------------------------------------------

def test_the_hubs_window_never_writes_over_an_unreadable_file(tmp_path, qapp, dialogs):
    """It read such a file as {} and saved over it, deleting what the
    experimenter wrote. Now its rows are greyed out, and a save refuses it."""
    from pytrackinganalysis.apps.removals_dialog import RemovalsDialog

    _make_project(tmp_path)                      # Rep1 and Rep2, region T_0
    broken = tmp_path / "Rep1" / removals.REMOVALS_FILENAME
    broken.write_text("- not a mapping\n")
    dialog = RemovalsDialog(str(tmp_path))
    try:
        assert not enabled(dialog.table.item(0, 0))           # Rep1
        assert not enabled(dialog.table.item(0, 5))
        assert enabled(dialog.table.item(1, 0))               # Rep2
        assert "cannot be read" in dialog.status.text()

        ## Ticked anyway (it cannot be by a click): the save still refuses
        ## Rep1, says so, and writes Rep2.
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        dialog._save()
        assert broken.read_text() == "- not a mapping\n"
        assert on_disk(tmp_path / "Rep2") == {"T_0": removals.DEFAULT_REASON}
        [warned] = dialogs["warning"]
        assert "Rep1" in warned[2] and "cannot be read" in warned[2]
    finally:
        dialog.deleteLater()
