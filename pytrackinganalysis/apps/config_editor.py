"""Config Editor — the standalone editor for ``tracking_config.yaml``.

Wraps the form-based tab widgets (:mod:`._config_tabs`) in a pyflic-style
chrome: :class:`TopBar` with Open/Save/Save-As and a theme toggle, a
three-tab :class:`QTabWidget` hosted inside :class:`Card` panels, and a
live YAML-preview Card at the bottom.  The Script Editor (Phase 4) opens
from the File menu / toolbar as a non-modal child window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from ..gui_env import sanitize_input_method_environment

sanitize_input_method_environment()

import yaml
from PyQt6.QtCore import QSize, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config_validation
from .. import experiment_types as _et
from .. import project as project_mod
from ..io_utils import atomic_write_text
from ..help import HelpButton, make_topbar_help_button
from ..ui import (
    ActionButton,
    Card,
    Category,
    TopBar,
    app_icon,
    apply_theme,
    icon,
    resolved_mode,
)
from ..ui import settings as ui_settings
from ._config_tabs import CountingRegionsTab, GlobalTab, TrackingRegionsTab


class ConfigEditorWindow(QMainWindow):
    """Main window for the Config Editor app."""

    def __init__(self, initial_path: str | None = None) -> None:
        super().__init__()
        ## 25% narrower than it was (1200): the widest thing in it is the
        ## regions table, and the Parameter-override fields that used to
        ## stretch to fill the rest are now two narrow columns.
        self.resize(900, 820)
        self._current_path: Optional[Path] = None
        self._disk_text: str = ""  # yaml serialized at last load/save
        # True while _load_path is populating the tabs, so the experiment-type
        # change signal does not pop a "seed regions?" dialog during a load.
        self._loading: bool = False
        # The document as loaded. Saving edits only the sections this editor
        # owns, so keys it does not display (scripts:, anything hand-added)
        # survive a load/save round trip.
        self._loaded_config: dict = {}
        # A single non-modal Script Editor. Two of them would both hold a
        # snapshot of the same file and the last one to save would win.
        self._script_editor: Optional[QMainWindow] = None

        self._build_ui()

        # Resolve initial path: explicit arg, cwd, or package default.
        if initial_path:
            candidate = Path(initial_path).expanduser().resolve()
            if candidate.is_dir():
                candidate = candidate / "tracking_config.yaml"
            if candidate.exists():
                self._load_path(candidate)
                return
            # An explicit target that doesn't exist yet stays the target: the
            # cwd/package fallback below would silently open an unrelated
            # config — and then save the user's edits over that file.
            self._current_path = candidate
            self._set_title(candidate)
            return

        for cwd_candidate in (
            Path.cwd() / "tracking_config.yaml",
            Path(__file__).resolve().parents[2] / "tracking_config.yaml",
        ):
            if cwd_candidate.exists():
                self._load_path(cwd_candidate)
                return

        self._set_title(None)

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar ─────────────────────────────────────────────────────
        self._top_bar = TopBar("Config Editor")
        btn_new = QToolButton()
        btn_new.setIcon(icon("new"))
        btn_new.setIconSize(QSize(18, 18))
        btn_new.setAutoRaise(True)
        btn_new.setToolTip("New project from an Experiment Type…")
        btn_new.clicked.connect(self._new_project)
        self._top_bar.add_right(btn_new)

        btn_open = QToolButton()
        btn_open.setIcon(icon("open"))
        btn_open.setIconSize(QSize(18, 18))
        btn_open.setAutoRaise(True)
        btn_open.setToolTip("Open a YAML config")
        btn_open.clicked.connect(self._open_dialog)
        self._top_bar.add_right(btn_open)

        btn_save = QToolButton()
        btn_save.setIcon(icon("save"))
        btn_save.setIconSize(QSize(18, 18))
        btn_save.setAutoRaise(True)
        btn_save.setToolTip("Save")
        btn_save.clicked.connect(self._save)
        self._top_bar.add_right(btn_save)

        btn_save_as = QToolButton()
        btn_save_as.setIcon(icon("save_as"))
        btn_save_as.setIconSize(QSize(18, 18))
        btn_save_as.setAutoRaise(True)
        btn_save_as.setToolTip("Save as…")
        btn_save_as.clicked.connect(self._save_as)
        self._top_bar.add_right(btn_save_as)

        btn_script = QToolButton()
        btn_script.setIcon(icon("scripts", category=Category.SCRIPTS))
        btn_script.setIconSize(QSize(18, 18))
        btn_script.setAutoRaise(True)
        btn_script.setToolTip("Open Script Editor (Phase 4)")
        btn_script.clicked.connect(self._launch_script_editor)
        self._top_bar.add_right(btn_script)

        self._top_bar.add_right(
            make_topbar_help_button(self, topic_id="config_overview")
        )

        self._btn_theme = QToolButton()
        self._btn_theme.setIcon(
            icon("theme_dark" if resolved_mode() == "light" else "theme_light")
        )
        self._btn_theme.setIconSize(QSize(18, 18))
        self._btn_theme.setAutoRaise(True)
        self._btn_theme.setToolTip("Toggle light / dark theme")
        self._btn_theme.clicked.connect(self._toggle_theme)
        self._top_bar.add_right(self._btn_theme)
        outer.addWidget(self._top_bar)

        # ── File path bar ───────────────────────────────────────────────
        path_bar = QWidget()
        pbl = QHBoxLayout(path_bar)
        pbl.setContentsMargins(16, 8, 16, 8)
        pbl.setSpacing(8)
        self._path_label = QLabel("No file loaded")
        self._path_label.setStyleSheet("color: palette(text); font-weight: 500;")
        self._dirty_label = QLabel("")
        self._dirty_label.setStyleSheet("color: #f59e0b; font-weight: 600;")
        pbl.addWidget(self._path_label, 1)
        pbl.addWidget(self._dirty_label)
        outer.addWidget(path_bar)

        # Tabs (wrapped in a Card)
        tabs_card = Card(
            "Configuration",
            category=Category.NEUTRAL,
            subtitle="Edit the global settings, tracking regions, and counting regions.",
            icon_name="config",
        )
        tabs_card.add_title_widget(
            HelpButton("config_overview", tooltip="Config file overview")
        )
        self._tabs = QTabWidget()
        self._global_tab = GlobalTab()
        self._tracking_tab = TrackingRegionsTab(self._global_tab)
        self._counting_tab = CountingRegionsTab()
        self._tabs.addTab(self._global_tab, "Global")
        self._tabs.addTab(self._tracking_tab, "Tracking regions")
        self._tabs.addTab(self._counting_tab, "Counting regions")
        # When the user picks a typed Experiment Type, offer to seed its required
        # counting regions (never deleting existing ones).
        self._global_tab.experimentTypeChanged.connect(self._maybe_seed_required_regions)
        # When a typed rig fixes the plate (Valence Max=36, Colosseum=24), lay
        # out the tracking regions on rig change.
        self._global_tab.rigChanged.connect(self._maybe_populate_tracking_regions)
        tabs_card.add_body(self._tabs)
        outer.addWidget(tabs_card, 1)

        # Poll the form vs. disk so the "unsaved changes" indicator stays live.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._refresh_dirty)
        self._refresh_timer.start()

    # ==================================================================
    # Load / save
    # ==================================================================

    @staticmethod
    def scaffold_project(parent_dir, name: str, type_name: str):
        """Create a new project folder for an Experiment Type and return the path
        to its ``tracking_config.yaml``.

        Makes ``<parent>/<name>/`` with ``data/``, ``analysis/``, ``qc/`` and a
        minimal, intentionally-incomplete ``tracking_config.yaml`` from the
        type's scaffold (rig unset, required regions stubbed). Pure filesystem
        work — no Qt — so it is unit-testable. Raises if the folder exists.
        """
        project = Path(parent_dir) / name
        if project.exists():
            raise FileExistsError(f"'{project}' already exists.")
        (project / "data").mkdir(parents=True, exist_ok=False)
        (project / "analysis").mkdir(exist_ok=True)
        (project / "qc").mkdir(exist_ok=True)
        config = _et.get_experiment_type(type_name).scaffold_config()
        config_path = project / "tracking_config.yaml"
        atomic_write_text(
            config_path,
            lambda handle: yaml.dump(config, handle, default_flow_style=False,
                                     allow_unicode=True, sort_keys=False),
        )
        return config_path

    def _new_project(self) -> None:
        types = _et.available_experiment_types()
        labels = [t.display_name for t in types]
        label, ok = QInputDialog.getItem(
            self, "New project", "Experiment type:", labels, 0, False)
        if not ok:
            return
        chosen = next(t for t in types if t.display_name == label)
        name, ok = QInputDialog.getText(self, "New project", "Project folder name:")
        name = name.strip()
        if not ok or not name:
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose where to create the project")
        if not parent:
            return
        try:
            config_path = self.scaffold_project(parent, name, chosen.name)
        except FileExistsError:
            QMessageBox.warning(self, "New project",
                                f"A folder named '{name}' already exists there.")
            return
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "New project", f"Could not create project:\n{err}")
            return
        self._load_path(config_path)
        QMessageBox.information(
            self, "New project",
            f"Created {config_path.parent}.\n\nNext: choose a rig, fill in the "
            "tracking regions, and put the DTrack export in data/.")

    def _open_dialog(self) -> None:
        start = str(self._current_path.parent) if self._current_path else os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open config", start, "YAML files (*.yaml *.yml);;All files (*)"
        )
        if path:
            self._load_path(Path(path))

    def _load_path(self, path: Path) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
                config = yaml.safe_load(text) or {}
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Could not load file:\n{err}")
            return
        self._loaded_config = config if isinstance(config, dict) else {}
        ## The recording beside this config is where a plate that is not
        ## fixed by the rig comes from (Paired Open Field, ADR-0016).
        experiment_dir = path.parent
        self._tracking_tab.set_regions_source(
            lambda: project_mod.tracking_regions_in_data(experiment_dir))
        self._loading = True
        try:
            self._global_tab.load(config)
            self._tracking_tab.load(config)
            self._counting_tab.load(config)
        finally:
            self._loading = False
        # A freshly opened project with no regions yet gets them laid out
        # silently: the rig's fixed plate for a type that has one (e.g. a
        # scaffolded Valence project), else the plate the recording holds.
        # Only when empty — an existing plate is never touched on load.
        t = self._global_tab.current_experiment_type()
        rig = self._global_tab.tracking_rig.currentData()
        expected = t.regions_for_rig(rig) if hasattr(t, "regions_for_rig") else None
        if not self._tracking_tab.region_names():
            if expected:
                self._tracking_tab.set_region_names(expected, self._plate(t, rig))
            else:
                self._tracking_tab.merge_region_names(self._tracking_tab.data_region_names())
        self._current_path = path
        self._disk_text = yaml.safe_dump(config, default_flow_style=False, sort_keys=False)
        self._set_title(path)
        self._refresh_dirty()
        ui_settings.add_recent_project(path.parent)

    def _save(self) -> None:
        if self._current_path is None:
            self._save_as()
            return
        self._write(self._current_path)

    def _save_as(self) -> None:
        start = str(self._current_path) if self._current_path else "tracking_config.yaml"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save config", start, "YAML files (*.yaml *.yml);;All files (*)"
        )
        if path:
            self._write(Path(path))
            self._current_path = Path(path)
            self._set_title(self._current_path)

    def _maybe_seed_required_regions(self) -> None:
        """Offer to add a typed Experiment Type's required counting regions.

        Fires when the user changes the Experiment Type (suppressed during a
        load). Never deletes existing regions — it only appends the missing
        required ones, so the user resolves any extras themselves.
        """
        if self._loading:
            return
        t = self._global_tab.current_experiment_type()
        required = getattr(t, "required_counting_regions", None)
        if not required:
            return
        have = self._counting_tab.labels()
        missing = [k for k in required if k not in have]
        if not missing:
            return
        stub = t.scaffold_config().get("counting_regions") or {}
        resp = QMessageBox.question(
            self,
            f"{t.display_name} counting regions",
            f"{t.display_name} requires counting regions {list(required)} "
            f"(in that order).\nMissing: {missing}.\n\n"
            "Add the missing region(s) now? Existing regions are kept — remove "
            "any extras manually.",
        )
        if resp == QMessageBox.StandardButton.Yes:
            for key in missing:
                aliases = (stub.get(key) or {}).get("alias", key)
                self._counting_tab.add_region(key, aliases)
            self._tabs.setCurrentWidget(self._counting_tab)

    @staticmethod
    def _plate(experiment_type, rig) -> dict | None:
        """The rig's plate geometry from the Experiment Type, or None.

        The type owns which wells face which way (Valence on Arena Max flips
        T_0..T_17); laying the plate out without asking it produced an all-+1
        table that disagreed with the same config built from scratch."""
        getter = getattr(experiment_type, "tracking_regions_for_rig", None)
        return getter(rig) if getter is not None else None

    def _maybe_populate_tracking_regions(self, rig: str) -> None:
        """Lay out a typed Experiment Type's fixed plate when the rig is chosen.

        Fires on rig change (suppressed during a load). Fills an empty table
        silently; if regions are already present and don't match, asks before
        replacing them, so treatment assignments are never wiped without consent.
        """
        if self._loading:
            return
        t = self._global_tab.current_experiment_type()
        expected = t.regions_for_rig(rig) if hasattr(t, "regions_for_rig") else None
        if not expected:
            ## No fixed plate: an empty table takes the recording's regions
            ## (Paired Open Field); a filled one is the user's to change, with
            ## the From-data button.
            if not self._tracking_tab.region_names():
                self._tracking_tab.merge_region_names(self._tracking_tab.data_region_names())
            return
        have = self._tracking_tab.region_names()
        ## Not just the default plate: a rig run at more than one size (the
        ## 18- and 24-well Colosseum) must not have a perfectly valid plate
        ## offered up for replacement — accepting would reset every treatment
        ## assignment on it.
        if hasattr(t, "regions_match_rig"):
            if t.regions_match_rig(rig, have) and have:
                return
        elif have == expected:
            return
        if have:
            sizes = getattr(t, "region_counts_for_rig", lambda _r: ())(rig)
            wanted = (f"{len(expected)}" if len(sizes) < 2 else
                      " or ".join(str(n) for n in sorted(sizes)))
            resp = QMessageBox.question(
                self,
                f"{t.display_name} tracking regions",
                f"{t.display_name} on this rig needs {wanted} tracking "
                f"regions (T_0..T_n).\n\n"
                f"Replace the current {len(have)} region(s) with "
                f"{len(expected)}? Treatment assignments will be reset.",
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        self._tracking_tab.set_region_names(expected, self._plate(t, rig))
        self._tabs.setCurrentWidget(self._tracking_tab)

    def _write(self, path: Path) -> None:
        errors = self._global_tab.validation_errors()
        if errors:
            # Writing anyway would drop the offending fields from the file and
            # leave the analysis running on rig defaults without telling anyone.
            QMessageBox.warning(
                self,
                "Fix these fields first",
                "The config was not saved:\n\n  • " + "\n  • ".join(errors),
            )
            return

        config = self._dump_config()

        # A typed experiment fails hard at load (ADR-0001), so block a save that
        # would produce a config the pipeline will refuse. Custom stays lenient.
        exp_type = self._global_tab.current_experiment_type()
        if not exp_type.is_custom:
            problems = config_validation.validate_config(config)
            if problems:
                QMessageBox.warning(
                    self,
                    f"{exp_type.display_name} configuration is incomplete",
                    "The config was not saved — a typed experiment must be valid "
                    "to load:\n\n  • " + "\n  • ".join(problems),
                )
                return
        try:
            # Atomic: the config is the project's only definition of the
            # experiment, and a plain open(path, "w") truncates it before the
            # first byte is written.
            atomic_write_text(
                path,
                lambda handle: yaml.dump(
                    config, handle,
                    default_flow_style=False, allow_unicode=True, sort_keys=False,
                ),
            )
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Could not save file:\n{err}")
            return
        self._disk_text = yaml.safe_dump(config, default_flow_style=False, sort_keys=False)
        self._refresh_dirty()
        QMessageBox.information(self, "Saved", f"Config saved to:\n{path}")

    def _dump_config(self) -> dict:
        """The document to write: the loaded YAML with this editor's sections replaced.

        Rebuilding the document from the three visible tabs alone would drop every
        top-level key the editor does not render — ``scripts:`` most importantly —
        and any hand-added key inside ``global:``.
        """
        config: dict = dict(self._loaded_config)

        dumped_global = self._global_tab.dump().get("global", {})
        owned = self._global_tab.owned_keys()
        # Walk the loaded keys first so the on-disk key order survives a save.
        merged_global: dict = {}
        for key, value in (config.get("global") or {}).items():
            if key in dumped_global:
                merged_global[key] = dumped_global[key]
            elif key not in owned:
                merged_global[key] = value  # not ours to touch — keep verbatim
            # An owned key the tab no longer produces was cleared by the user.
        for key, value in dumped_global.items():
            merged_global.setdefault(key, value)
        config["global"] = merged_global

        # The region tabs always emit their key, so an empty table means "the
        # user deleted every region" — not "this tab has nothing to say".
        # config.update({}) used to be a no-op, which silently wrote the
        # previous plate's regions back out.
        for section in (self._tracking_tab.dump(), self._counting_tab.dump()):
            for key, value in section.items():
                if value:
                    config[key] = value
                else:
                    config.pop(key, None)
        return config

    # ==================================================================
    # Dirty indicator
    # ==================================================================

    def _refresh_dirty(self) -> None:
        try:
            text = yaml.safe_dump(
                self._dump_config(), default_flow_style=False, sort_keys=False
            )
        except Exception:  # noqa: BLE001
            return
        dirty = text != self._disk_text
        self._dirty_label.setText("●  unsaved changes" if dirty else "")

    def _set_title(self, path: Path | None) -> None:
        if path is None:
            self.setWindowTitle("PyTrackingAnalysis — Config Editor")
            self._path_label.setText("No file loaded")
            self._path_label.setToolTip("")
        else:
            self.setWindowTitle(f"PyTrackingAnalysis — Config Editor — {path.name}")
            self._path_label.setText(path.name)
            self._path_label.setToolTip(str(path))

    # ==================================================================
    # Theme toggle / Script Editor launcher
    # ==================================================================

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        new_mode = "dark" if resolved_mode() == "light" else "light"
        apply_theme(app, mode=new_mode)
        ui_settings.set_value("theme", new_mode)
        self._btn_theme.setIcon(
            icon("theme_dark" if resolved_mode() == "light" else "theme_light")
        )

    def _launch_script_editor(self) -> None:
        if self._current_path is None:
            QMessageBox.information(
                self,
                "Script Editor",
                "Open or save a YAML config first — scripts are stored alongside it.",
            )
            return
        # One editor per window. A second one would open its own snapshot of
        # the same file and whichever saved last would silently win.
        if self._script_editor is not None:
            try:
                self._script_editor.show()
                self._script_editor.raise_()
                self._script_editor.activateWindow()
                return
            except RuntimeError:  # the C++ object is gone — build a fresh one
                self._script_editor = None

        try:
            from ..script_editor.window import ScriptEditorWindow
        except Exception as err:  # noqa: BLE001
            QMessageBox.information(
                self,
                "Script Editor",
                f"Script Editor is still under development (Phase 4).\n\n{err}",
            )
            return

        try:
            # The construction has to be inside the guard: a malformed
            # ``scripts:`` block raises in __init__, and an exception escaping a
            # Qt slot is escalated by PyQt6 to qFatal() — SIGABRT, taking the
            # user's unsaved config edits with it.
            editor = ScriptEditorWindow(self._current_path, parent=self)
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Script Editor", f"Could not open:\n{err}")
            return

        # The Script Editor writes the same file we do. Without this the next
        # Save here would rewrite the document from a snapshot predating the
        # scripts: it just stored, deleting every saved script.
        editor.scriptsSaved.connect(self._reload_external_changes)
        editor.destroyed.connect(self._forget_script_editor)
        self._script_editor = editor
        editor.show()

    def _forget_script_editor(self, *_args) -> None:
        self._script_editor = None

    # Top-level sections this window renders and therefore owns; everything
    # else in the document belongs to whoever wrote it.
    _OWNED_SECTIONS = ("global", "tracking_regions", "counting_regions")

    def _reload_external_changes(self, path: str = "") -> None:
        """Re-read the sections this window does not own after a child wrote the file."""
        target = Path(path) if path else self._current_path
        if target is None:
            return
        try:
            with open(target, encoding="utf-8") as f:
                text = f.read()
            fresh = yaml.safe_load(text) or {}
        except Exception:  # noqa: BLE001 — a re-read failure must not kill the slot
            return
        if not isinstance(fresh, dict):
            return

        for key, value in fresh.items():
            if key not in self._OWNED_SECTIONS:
                self._loaded_config[key] = value
        # A section the other writer *removed* must not be resurrected either.
        for key in [k for k in self._loaded_config
                    if k not in self._OWNED_SECTIONS and k not in fresh]:
            del self._loaded_config[key]

        # The on-disk baseline moved, so re-anchor the dirty indicator to it.
        self._disk_text = yaml.safe_dump(fresh, default_flow_style=False, sort_keys=False)
        self._refresh_dirty()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setDesktopFileName("pytrack-config")
    app.setWindowIcon(app_icon())
    mode = ui_settings.get("theme", "auto")
    apply_theme(app, mode=mode)

    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = ConfigEditorWindow(initial_path=initial)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
