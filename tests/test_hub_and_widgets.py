"""Regressions for the Hub, the QC viewer, and the shared UI widgets.

These are the code paths the existing suite never touched, which is how a
tab-closing crash, three flavours of leak, and a silently-ignored config
selector all survived it. Everything runs headless (offscreen Qt, Agg
matplotlib) against tmp_path, with no real Experiment anywhere.
"""

from __future__ import annotations

import json
import os

import pandas as pd
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

pytest.importorskip("PyQt6.QtWidgets")

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QEvent, QModelIndex, QPoint  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
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


def flush_deletions() -> None:
    """Run the deferred deletions that ``deleteLater`` queued."""
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


@pytest.fixture
def no_dialogs(monkeypatch):
    """Swallow modal dialogs — they would block a headless run."""
    seen: dict[str, list] = {"warning": [], "critical": [], "information": [], "question": []}
    for kind in seen:
        monkeypatch.setattr(
            QMessageBox, kind,
            staticmethod(
                lambda *args, _kind=kind, **kwargs: (
                    seen[_kind].append(args) or QMessageBox.StandardButton.Cancel
                )
            ),
        )
    return seen


@pytest.fixture
def png(tmp_path):
    """A real (tiny) PNG on disk — ZoomableImageView rejects unloadable files."""
    path = tmp_path / "artifact.png"
    pix = QPixmap(8, 8)
    pix.fill()
    assert pix.save(str(path), "PNG")
    return path


@pytest.fixture
def hub(qapp, no_dialogs, tmp_path, monkeypatch):
    from pytrackinganalysis.apps.hub import HubWindow

    # Keep the real ui.json out of the way: HubWindow touches recent projects.
    monkeypatch.setattr(
        "pytrackinganalysis.ui.settings._CONFIG_FILE", tmp_path / "ui.json"
    )
    monkeypatch.setattr("pytrackinganalysis.ui.settings._CONFIG_DIR", tmp_path)
    window = HubWindow()
    ## These tests exercise the artifact/plot tab machinery itself, and the
    ## Batch card's "Suppress new plot / output tabs" switch — on by default —
    ## short-circuits every one of them before a tab is ever made.
    window._chk_suppress_tabs.setChecked(False)
    yield window
    window.close()


# --------------------------------------------------------------------------
# C7 — closing a plot tab then re-running the same task aborted the Hub
# --------------------------------------------------------------------------

def test_reopening_a_closed_artifact_tab_does_not_crash(hub, png):
    """The second 'Saved:' line used to hit a deleted C++ object and SIGABRT."""
    hub._add_artifact_tab(png)
    before = hub._plot_dock.count()

    hub._plot_dock._on_close(hub._plot_dock.count() - 1)
    flush_deletions()

    hub._add_artifact_tab(png)  # must not raise RuntimeError
    assert hub._plot_dock.count() == before


def test_closing_an_artifact_tab_prunes_the_mapping(hub, png):
    key = str(png.resolve())
    hub._add_artifact_tab(png)
    assert key in hub._artifact_tabs

    hub._plot_dock._on_close(hub._plot_dock.count() - 1)
    flush_deletions()

    assert key not in hub._artifact_tabs, "the dead wrapper was left in the map"


def test_clear_plots_prunes_the_artifact_mapping(hub, png):
    hub._add_artifact_tab(png)
    hub._plot_dock.clear_figures()
    flush_deletions()

    assert hub._artifact_tabs == {}
    hub._add_artifact_tab(png)  # must not raise
    assert hub._plot_dock.count() >= 3


def test_add_artifact_tab_tolerates_a_stale_wrapper(hub, png):
    """Defence in depth: a dead entry must not raise out of a queued slot."""
    from pytrackinganalysis.ui import ZoomableTextView

    dead = ZoomableTextView(png)
    dead.deleteLater()
    flush_deletions()
    hub._artifact_tabs[str(png.resolve())] = dead

    hub._add_artifact_tab(png)  # the RuntimeError path

    assert hub._artifact_tabs[str(png.resolve())] is not dead


def test_an_existing_artifact_tab_is_reused_not_duplicated(hub, png):
    hub._add_artifact_tab(png)
    count = hub._plot_dock.count()
    hub._add_artifact_tab(png)
    assert hub._plot_dock.count() == count


# --------------------------------------------------------------------------
# U3 — interactive figures were never closed
# --------------------------------------------------------------------------

@pytest.fixture
def dock(qapp):
    from pytrackinganalysis.ui import OutputLog, PlotDock

    plt.close("all")
    widget = PlotDock(OutputLog(), OutputLog())
    yield widget
    widget.deleteLater()
    flush_deletions()
    plt.close("all")


def test_closing_an_interactive_tab_closes_its_figure(dock):
    fig = plt.figure()
    fig.add_subplot(111).plot([0, 1], [0, 1])
    dock.add_figure("Live", fig, interactive=True)
    assert plt.get_fignums(), "the figure should still be open while the tab is"

    dock._on_close(dock.count() - 1)
    flush_deletions()

    assert plt.get_fignums() == [], "interactive figures accumulated forever"


def test_clear_figures_closes_interactive_figures(dock):
    for _ in range(3):
        fig = plt.figure()
        fig.add_subplot(111).plot([0, 1], [1, 0])
        dock.add_figure("Live", fig, interactive=True)
    assert len(plt.get_fignums()) == 3

    dock.clear_figures()
    flush_deletions()

    assert plt.get_fignums() == []


def test_the_dock_has_a_clear_button_per_thing_that_accumulates(dock):
    """Analysis tabs, Output, and Errors each fill up on their own, so each
    gets its own clear — "Clear plots" only ever emptied the first."""
    from PyQt6.QtWidgets import QToolButton

    labels = [b.text() for b in dock.cornerWidget().findChildren(QToolButton)]
    assert labels == ["Clear Analysis Tabs", "Clear Output", "Clear Errors"]


def test_clearing_output_and_errors_empties_only_its_own_log(dock):
    out, err = dock.widget(0), dock.widget(1)
    out.append_line("kept until cleared")
    err.append_line("FAILED: boom")

    dock.clear_output()
    assert out.toPlainText() == ""
    assert "boom" in err.toPlainText()

    dock.clear_errors()
    assert err.toPlainText() == ""


def test_clearing_errors_drops_the_unseen_badge(dock):
    """The count badge means "unread issues"; with the tab emptied there are
    none left to read."""
    dock.setCurrentIndex(0)                      # Errors not visible
    dock.widget(1).append_line("FAILED: boom")
    assert dock.tabText(1) == "Errors (1)"

    dock.clear_errors()
    assert dock.tabText(1) == "Errors"


def test_a_cleared_log_does_not_rerender_a_partial_line(dock):
    """``append_stream`` rewrites the block holding a partial line; after a
    clear that block is gone, so the pending text must be dropped with it."""
    out = dock.widget(0)
    out.append_stream("half a line")
    dock.clear_output()

    out.append_stream(" and the rest\n")
    assert out.toPlainText().strip() == "and the rest"


def test_a_static_figure_is_closed_immediately(dock):
    fig = plt.figure()
    fig.add_subplot(111).plot([0, 1], [0, 1])
    dock.add_figure("Static", fig, interactive=False)
    assert plt.get_fignums() == []


def test_a_savefig_failure_still_closes_the_figure(dock):
    fig = plt.figure()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    fig.savefig = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        dock.add_figure("Doomed", fig, interactive=False)

    assert plt.get_fignums() == [], "a failed savefig leaked the figure"


# --------------------------------------------------------------------------
# U4 — QC viewer reloads duplicated every artifact tab
# --------------------------------------------------------------------------

@pytest.fixture
def qc_window(qapp, no_dialogs, tmp_path, monkeypatch):
    from pytrackinganalysis.apps.qc_viewer import QcViewerWindow

    monkeypatch.setattr(
        "pytrackinganalysis.ui.settings._CONFIG_FILE", tmp_path / "ui.json"
    )
    monkeypatch.setattr("pytrackinganalysis.ui.settings._CONFIG_DIR", tmp_path)
    window = QcViewerWindow()
    yield window
    window.close()


def test_qc_reload_replaces_png_tabs_instead_of_duplicating(qc_window, png):
    qc_window._add_png_tab(png)
    after_first = qc_window._plot_dock.count()

    for _ in range(9):
        qc_window._add_png_tab(png)

    assert qc_window._plot_dock.count() == after_first, (
        "ten reloads used to leave ten copies of every artifact tab"
    )


def test_qc_png_tabs_for_different_files_still_coexist(qc_window, png, tmp_path):
    other = tmp_path / "second.png"
    pix = QPixmap(8, 8)
    pix.fill()
    pix.save(str(other), "PNG")

    qc_window._add_png_tab(png)
    qc_window._add_png_tab(other)

    titles = {qc_window._plot_dock.tabText(i) for i in range(qc_window._plot_dock.count())}
    assert {"artifact", "second"} <= titles


# --------------------------------------------------------------------------
# U5 — closing a window mid-load destroyed a running QThread
# --------------------------------------------------------------------------

def test_shutdown_worker_waits_for_a_running_thread(qapp):
    import time

    from pytrackinganalysis.apps.common import TaskWorker, shutdown_worker

    worker = TaskWorker("slow", lambda: time.sleep(0.2) or "done")
    worker.start()
    assert shutdown_worker(worker) is True
    assert not worker.isRunning(), "the thread was still running after the wait"


def test_closing_the_qc_viewer_waits_for_the_loader(qc_window):
    import time

    from pytrackinganalysis.apps.common import TaskWorker

    worker = TaskWorker("Load experiment", lambda: time.sleep(0.2) or "ok")
    qc_window._loader = worker
    worker.start()

    qc_window.close()

    assert not worker.isRunning()
    assert qc_window._loader is None


def test_closing_the_hub_waits_for_a_running_task(hub, no_dialogs, monkeypatch):
    import time

    from pytrackinganalysis.apps.common import TaskWorker

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
    )
    worker = TaskWorker("Run analysis", lambda: time.sleep(0.2) or "ok")
    hub._worker = worker
    worker.start()

    hub.close()

    assert not worker.isRunning()


# --------------------------------------------------------------------------
# U1 — Load / Scripts always use the canonical tracking_config.yaml
# --------------------------------------------------------------------------

def _write_config(path, script_name):
    path.write_text(yaml.safe_dump({
        "global": {"tracking_type": "TRACKER", "tracking_rig": "small_arena"},
        "tracking_regions": {"T_1": {"experimental_factors": "Ctrl"}},
        "scripts": [{
            "name": script_name,
            "steps": [{"action": "load_experiment", "params": {"path": "."}}],
        }],
    }, sort_keys=False))


def test_scripts_follow_the_canonical_config(hub, tmp_path):
    """Scripts read tracking_config.yaml — alternate YAMLs are ignored."""
    _write_config(tmp_path / "tracking_config.yaml", "canonical")
    _write_config(tmp_path / "alt_config.yaml", "alternative")
    hub._set_project_dir(tmp_path)
    assert hub._scripts_combo.currentText() == "canonical"
    assert hub._config_path() == tmp_path / "tracking_config.yaml"


def test_loading_uses_the_canonical_config(hub, tmp_path, monkeypatch):
    """Load always opens tracking_config.yaml (no Project-card picker)."""
    _write_config(tmp_path / "tracking_config.yaml", "canonical")
    _write_config(tmp_path / "alt_config.yaml", "alternative")
    hub._set_project_dir(tmp_path)

    from pytrackinganalysis.apps import hub as hub_mod

    seen: dict = {}

    class _FakeExperiment:
        def __init__(self, project_directory, config_path=None, **kwargs):
            seen["project_directory"] = project_directory
            seen["config_path"] = config_path

        def run_qc(self):
            pass

        def __str__(self):
            return "fake"

    monkeypatch.setattr(hub_mod.ExperimentMod, "Experiment", _FakeExperiment)
    monkeypatch.setattr(
        hub, "_spawn_task_with_callbacks",
        lambda label, task, on_ok, on_fail, **kwargs: task(),
    )

    hub._load_experiment()

    assert seen.get("config_path") == "tracking_config.yaml"


def test_experiment_honours_an_alternative_config_path(tmp_path):
    """The parameter the Hub relies on, pinned at the Experiment level."""
    from pytrackinganalysis import Experiment as ExperimentMod
    from test_experiment_integration import write_project

    root = write_project(tmp_path / "proj")
    alt = root / "alt_config.yaml"
    canonical = yaml.safe_load((root / "tracking_config.yaml").read_text())
    canonical["global"]["facet_cutoffs"] = [3]
    alt.write_text(yaml.safe_dump(canonical, sort_keys=False))

    exp = ExperimentMod.Experiment(str(root), config_path="alt_config.yaml")

    assert exp.config_path == str(alt)
    assert exp.facet_cutoffs == (3,), "the alternative config was not the one loaded"


# --------------------------------------------------------------------------
# U2 — plot rendering raced the worker over the global plt.show
# --------------------------------------------------------------------------

def test_busy_disables_the_plots_card(hub):
    hub._set_busy(True)
    assert not hub._cards["plots"].isEnabled(), (
        "a plot click during an analysis corrupted plt.show for both"
    )
    hub._set_busy(False)
    assert hub._cards["plots"].isEnabled()


def test_plot_rendering_is_dispatched_to_the_worker(hub, qapp):
    """The only workload the Hub ran inline, straight into the worker's plt.show."""
    import threading
    import time
    from types import SimpleNamespace

    ran_on: list[int] = []

    def _plot():
        ran_on.append(threading.get_ident())
        plt.figure()
        plt.show()

    hub._exp = SimpleNamespace(
        arena=SimpleNamespace(plot_thing=_plot), facet_cutoffs=None
    )
    tabs_before = hub._plot_dock.count()

    hub._render_plot("plot_thing", {}, "Thing")
    assert hub._worker is not None, "the plot ran inline on the GUI thread"

    deadline = time.monotonic() + 10
    while hub._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert ran_on and ran_on[0] != threading.get_ident()
    assert hub._plot_dock.count() == tabs_before + 1
    assert plt.get_fignums() == []


def test_overlapping_captures_restore_plt_show(qapp):
    """The naive save/restore pair left plt.show pointing at a dead capture."""
    from pytrackinganalysis.apps.common import capture_figures

    pristine = plt.show
    with capture_figures() as outer:
        with capture_figures() as inner:
            fig = plt.figure()
            plt.show()
            assert inner and not outer
        plt.show()
        assert len(outer) == 1
    plt.close(fig)

    assert plt.show is pristine


def test_a_capture_in_another_thread_does_not_steal_figures(qapp):
    import threading

    from pytrackinganalysis.apps.common import capture_figures

    pristine = plt.show
    started = threading.Event()
    release = threading.Event()

    def _hold():
        with capture_figures():
            started.set()
            release.wait(5)

    thread = threading.Thread(target=_hold)
    thread.start()
    started.wait(5)
    try:
        with capture_figures() as mine:
            fig = plt.figure()
            plt.show()
        assert len(mine) == 1
        plt.close(fig)
    finally:
        release.set()
        thread.join(5)

    assert plt.show is pristine, "plt.show was left corrupted"


# --------------------------------------------------------------------------
# U9 — table model parent handling
# --------------------------------------------------------------------------

def test_row_and_column_count_are_zero_for_a_valid_parent(qapp):
    """Every cell used to claim len(df) children."""
    from pytrackinganalysis.ui.table_model import DataFrameModel

    model = DataFrameModel(pd.DataFrame({"Tracker": ["a", "b"], "HighQuality": [0.9, 0.5]}))
    cell = model.index(0, 0)

    assert model.rowCount(QModelIndex()) == 2
    assert model.columnCount(QModelIndex()) == 2
    assert model.rowCount(cell) == 0
    assert model.columnCount(cell) == 0


def test_recursive_filtering_reports_the_real_row_count(qapp):
    from PyQt6.QtCore import QSortFilterProxyModel

    from pytrackinganalysis.ui.table_model import DataFrameModel

    model = DataFrameModel(pd.DataFrame({"Tracker": ["T_1", "T_2"], "HighQuality": [0.9, 0.5]}))
    proxy = QSortFilterProxyModel()
    proxy.setRecursiveFilteringEnabled(True)
    proxy.setSourceModel(model)
    proxy.setFilterKeyColumn(0)
    proxy.setFilterFixedString("T_1")

    assert proxy.rowCount() == 1, "recursive filtering double-counted the rows"


# --------------------------------------------------------------------------
# U9 — settings must survive an interrupted write
# --------------------------------------------------------------------------

@pytest.fixture
def settings(tmp_path, monkeypatch):
    from pytrackinganalysis.ui import settings as ui_settings

    monkeypatch.setattr(ui_settings, "_CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(ui_settings, "_CONFIG_FILE", tmp_path / "cfg" / "ui.json")
    return ui_settings


def test_settings_round_trip(settings):
    settings.set_value("theme", "dark")
    settings.add_recent_project("/tmp/project-a")

    loaded = settings.load()
    assert loaded["theme"] == "dark"
    assert loaded["recent_projects"] == [str(os.path.abspath("/tmp/project-a"))]


def test_an_interrupted_save_leaves_the_previous_settings_intact(settings, monkeypatch):
    """A truncating write left '{"theme": "da' on disk and lost everything."""
    settings.set_value("theme", "dark")
    for i in range(8):
        settings.add_recent_project(f"/tmp/project-{i}")
    good = settings.load()

    def _boom(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(settings.json, "dumps", _boom)
    settings.set_value("theme", "light")  # swallowed, as before

    assert settings.load() == good, "an interrupted write destroyed the settings"
    assert json.loads(settings._CONFIG_FILE.read_text())["theme"] == "dark"


def test_a_failed_save_leaves_no_temp_files_behind(settings, monkeypatch):
    settings.set_value("theme", "dark")

    monkeypatch.setattr(
        settings.json, "dumps",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    settings.set_value("theme", "light")

    leftovers = [p.name for p in settings._CONFIG_DIR.iterdir() if p.name != "ui.json"]
    assert leftovers == []


# --------------------------------------------------------------------------
# U9 — assorted smaller items
# --------------------------------------------------------------------------

def test_zoom_anchoring_below_100_percent(qapp, png):
    """max(1.0, zoom) clamped the denominator, breaking the anchor when zoomed out."""
    from pytrackinganalysis.ui.zoom import ZoomableImageView

    view = ZoomableImageView(png)
    view._zoom = 0.25
    view._fit = False
    scroll = view._scroll
    scroll.horizontalScrollBar().setRange(0, 10_000)
    scroll.verticalScrollBar().setRange(0, 10_000)
    scroll.horizontalScrollBar().setValue(400)
    scroll.verticalScrollBar().setValue(400)
    anchor = QPoint(100, 100)

    # The image point under the cursor, computed the way zoom_by must.
    expected_x = (400 + anchor.x()) / 0.25
    view.zoom_by(2.0, anchor=anchor)

    assert view._zoom == pytest.approx(0.5)
    assert scroll.horizontalScrollBar().value() == pytest.approx(
        int(expected_x * view._zoom - anchor.x()), abs=1
    )
    view.deleteLater()


def test_desktop_exec_quotes_paths_with_spaces():
    from pytrackinganalysis.install_desktop import _exec_arg

    assert _exec_arg("/home/dr who/My Venv/bin/pytrack-hub") == \
        '"/home/dr who/My Venv/bin/pytrack-hub"'
    assert _exec_arg("/usr/local/bin/pytrack-hub") == "/usr/local/bin/pytrack-hub"
    assert _exec_arg('/opt/a"b/pytrack-hub') == '"/opt/a\\"b/pytrack-hub"'


def test_every_app_id_gets_a_desktop_entry():
    """Each app registers its ID with the desktop portal; an ID with no
    installed .desktop entry logs "App info not found" on every launch (the
    Plot Editor was missing from the installer)."""
    import re
    from pathlib import Path

    from pytrackinganalysis import install_desktop

    apps = Path(install_desktop.__file__).parent / "apps"
    ids = {m for path in apps.glob("*.py")
           for m in re.findall(r'setDesktopFileName\("([^"]+)"\)',
                               path.read_text(encoding="utf-8"))}
    assert ids >= {"pytrack-hub", "pytrack-plots"}
    assert ids <= {cmd for cmd, _name, _comment in install_desktop._APPS}


def test_gui_environment_strips_ibus_module_overrides(monkeypatch):
    import os

    from pytrackinganalysis.gui_env import sanitize_input_method_environment

    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("GTK_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")

    sanitize_input_method_environment()

    assert "QT_IM_MODULE" not in os.environ
    assert "GTK_IM_MODULE" not in os.environ
    assert os.environ["XMODIFIERS"] == "@im=ibus"


def test_worker_output_does_not_steal_main_thread_prints(qapp, capsys):
    """redirect_stdout rebound sys.stdout process-wide from the worker thread."""
    import threading

    from pytrackinganalysis.apps.common import route_output

    sink = []
    started = threading.Event()
    release = threading.Event()

    class _Sink:
        def write(self, text):
            sink.append(text)
            return len(text)

        def flush(self):
            pass

    def _hold():
        with route_output(_Sink()):
            started.set()
            print("from the worker")
            release.wait(5)

    thread = threading.Thread(target=_hold)
    thread.start()
    started.wait(5)
    try:
        print("from the main thread")
    finally:
        release.set()
        thread.join(5)

    assert "from the worker" in "".join(sink)
    assert "from the main thread" not in "".join(sink)
    assert "from the main thread" in capsys.readouterr().out


def test_a_failed_task_shows_a_dialog(hub, no_dialogs):
    hub._on_task_failed("Run analysis failed:\nTraceback…")
    assert no_dialogs["warning"], "a failed analysis looked exactly like a finished one"


# --------------------------------------------------------------------------
# Run-notes dialog on the analysis / report buttons
# --------------------------------------------------------------------------

class _NotesExp:
    def __init__(self, existing="old note"):
        self.existing = existing
        self.saved = None

    def read_run_notes(self):
        return self.existing

    def write_run_notes(self, text):
        self.saved = text

    def run_analysis(self):
        pass

    def create_report(self):
        return "done"


def test_run_notes_dialog_saves_before_the_report_task(hub, monkeypatch):
    from pytrackinganalysis.apps import hub as hub_mod

    exp = _NotesExp()
    hub._exp = exp
    spawned = []
    monkeypatch.setattr(hub, "_spawn_task",
                        lambda title, fn: spawned.append(title))
    seen = {}

    def _fake_dialog(parent, title, label, text=""):
        seen["prefill"] = text
        return "fresh notes", True

    monkeypatch.setattr(hub_mod.QInputDialog, "getMultiLineText",
                        staticmethod(_fake_dialog))
    hub._run_create_report()
    assert seen["prefill"] == "old note", "existing notes were not offered for editing"
    assert exp.saved == "fresh notes"
    assert spawned == ["PDF report"]


def test_run_notes_dialog_cancel_keeps_existing_notes(hub, monkeypatch):
    from pytrackinganalysis.apps import hub as hub_mod

    exp = _NotesExp()
    hub._exp = exp
    spawned = []
    monkeypatch.setattr(hub, "_spawn_task",
                        lambda title, fn: spawned.append(title))
    monkeypatch.setattr(hub_mod.QInputDialog, "getMultiLineText",
                        staticmethod(lambda *a, **k: ("ignored", False)))
    hub._run_full_analysis()
    assert exp.saved is None, "cancel must not touch the saved notes"
    assert spawned == ["Run analysis"], "cancelling notes must not cancel the run"


def test_finished_child_apps_are_reaped(hub):
    class _FakeProcess:
        def __init__(self, code):
            self.pid = 4242
            self._code = code
            self.polled = 0

        def poll(self):
            self.polled += 1
            return self._code

    done, running = _FakeProcess(0), _FakeProcess(None)
    hub._subapps = [("qc", done), ("config", running)]

    hub._reap_subapps()

    assert [entry[1] for entry in hub._subapps] == [running]
    assert done.polled == 1


def test_qc_threshold_comes_from_the_config(qc_window):
    """0.90/0.80 were hardcoded while the core's cutoff is configurable."""
    from types import SimpleNamespace

    from pytrackinganalysis.apps.qc_viewer import _core_qc_cutoff

    exp = SimpleNamespace(config={"global": {"qc_cutoff": 0.75}})
    qc_window._apply_qc_cutoff(exp)
    assert qc_window._THR_GREEN == pytest.approx(0.75)
    assert qc_window._THR_YELLOW < qc_window._THR_GREEN

    qc_window._apply_qc_cutoff(SimpleNamespace(config={"global": {}}))
    assert qc_window._THR_GREEN == pytest.approx(_core_qc_cutoff())


def test_the_qc_default_matches_the_analysis_core(qapp):
    import inspect

    from pytrackinganalysis import Experiment as ExperimentMod
    from pytrackinganalysis.apps.qc_viewer import _core_qc_cutoff

    core = inspect.signature(ExperimentMod.Experiment.qc).parameters["cutoff"].default
    assert _core_qc_cutoff() == pytest.approx(core)
