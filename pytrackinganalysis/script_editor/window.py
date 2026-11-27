"""ScriptEditorWindow — assembles palette / canvas / inspector / preview.

Launched non-modally from the Config Editor.  Scripts are stored under
the ``scripts:`` key of the surrounding ``tracking_config.yaml``.  Each
script has ``{name, steps:[{action, params}, ...]}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import layout
from ..help import make_topbar_help_button
from ..ui import ActionButton, Category, TopBar, apply_theme, icon, resolved_mode
from .actions import ACTIONS, validation_issues
from .project_actions import PROJECT_ACTIONS, project_validation_issues
from .canvas import Canvas
from .inspector import Inspector
from .palette import Palette
from .preview import Preview
from .runner import load_scripts, save_scripts


class ScriptEditorWindow(QMainWindow):
    """Visual editor for ``scripts:`` embedded in a tracking_config.yaml."""

    scriptsSaved = pyqtSignal(str)

    def __init__(self, config_path: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path = Path(config_path).expanduser().resolve()
        self.setWindowTitle(f"PyTrackingAnalysis — Script Editor — {self._config_path.name}")
        self.resize(1400, 860)

        ## Sections (ADR-0006): a tracking_config.yaml has one (its scripts);
        ## a project.yaml has two — Project Scripts (project-action palette)
        ## and the centrally-held Experiment Scripts (experiment palette).
        if self._config_path.name == "project.yaml":
            self._sections = [
                {"label": "Project scripts", "key": "scripts",
                 "actions": PROJECT_ACTIONS,
                 "validator": project_validation_issues, "scripts": []},
                {"label": "Experiment scripts", "key": "experiment_scripts",
                 "actions": ACTIONS,
                 "validator": validation_issues, "scripts": []},
            ]
        else:
            self._sections = [
                {"label": "Scripts", "key": "scripts", "actions": ACTIONS,
                 "validator": validation_issues, "scripts": []},
            ]
        self._section_idx = 0

        self._scripts: list[dict] = []
        self._active_idx: int = -1
        self._dirty: bool = False
        self._experiment_type: str | None = None

        self._build_ui()
        self._load_from_disk()

    # ---- section helpers (level switcher) -----------------------------

    def _section(self) -> dict:
        return self._sections[self._section_idx]

    def _section_actions(self) -> dict:
        return self._section()["actions"]

    def _section_exp_type(self) -> str | None:
        """The tracking-type filter for the ACTIVE section: project actions
        are type-agnostic; experiment actions filter by the configured (or
        design-declared) type."""
        if self._section_actions() is PROJECT_ACTIONS:
            return None
        return self._experiment_type

    def _apply_section(self) -> None:
        """Point the shared widgets at the active section's registry and
        scripts."""
        self._scripts = self._section()["scripts"]
        self._active_idx = 0 if self._scripts else -1
        self._palette.set_actions(self._section_actions())
        self._canvas.set_actions(self._section_actions())
        self._palette.set_experiment_type(self._section_exp_type())
        self._inspector.set_experiment_type(self._section_exp_type())
        self._refresh_script_combo()
        self._load_active_script_into_canvas()
        self._refresh_errors()

    def _on_section_changed(self, idx: int) -> None:
        if idx < 0 or idx == self._section_idx:
            return
        self._sync_canvas_to_scripts()
        was_dirty = self._dirty
        self._section_idx = idx
        if getattr(self, "_level_combo", None) is not None \
                and self._level_combo.currentIndex() != idx:
            self._level_combo.blockSignals(True)
            self._level_combo.setCurrentIndex(idx)
            self._level_combo.blockSignals(False)
        self._apply_section()
        ## Switching levels only changes what is shown — it edits nothing.
        self._dirty = was_dirty
        self._update_dirty_indicator()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar with script switcher ────────────────────────────────
        self._top_bar = TopBar("Script Editor")
        if len(self._sections) > 1:
            self._level_combo = QComboBox()
            for section in self._sections:
                self._level_combo.addItem(section["label"])
            self._level_combo.setToolTip(
                "Which script list to edit: project-level steps, or the "
                "experiment-level scripts held centrally for every replicate")
            self._level_combo.currentIndexChanged.connect(
                self._on_section_changed)
            self._top_bar.add_right(self._level_combo)
        self._top_bar.add_right(self._build_script_switcher())
        self._top_bar.add_right(
            make_topbar_help_button(self, topic_id="scripts_overview")
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

        # ── Subtitle bar (file path + dirty indicator) ──────────────────
        sub = QFrame()
        sub.setStyleSheet(
            "background: palette(alternate-base); "
            "border-bottom: 1px solid palette(mid);"
        )
        sub_lay = QHBoxLayout(sub)
        sub_lay.setContentsMargins(16, 4, 16, 4)
        self._path_lbl = QLabel(str(self._config_path))
        self._path_lbl.setStyleSheet("color: palette(mid);")
        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color: #f59e0b; font-weight: 600;")
        sub_lay.addWidget(self._path_lbl, 1)
        sub_lay.addWidget(self._dirty_lbl)
        outer.addWidget(sub)

        # ── Main body: three panes on top, preview + save below ─────────
        splitter_v = QSplitter()
        splitter_v.setOrientation(splitter_v.orientation().Vertical)
        splitter_v.setChildrenCollapsible(False)

        splitter_h = QSplitter()
        splitter_h.setChildrenCollapsible(False)

        self._palette = Palette()
        self._canvas = Canvas()
        self._inspector = Inspector()

        self._palette.actionActivated.connect(self._on_action_activated)
        self._canvas.stepSelected.connect(self._on_step_selected)
        self._canvas.stepsChanged.connect(self._on_steps_changed)
        self._inspector.paramsChanged.connect(self._on_params_changed)

        splitter_h.addWidget(self._palette)
        splitter_h.addWidget(self._canvas)
        splitter_h.addWidget(self._inspector)
        splitter_h.setStretchFactor(0, 2)
        splitter_h.setStretchFactor(1, 5)
        splitter_h.setStretchFactor(2, 3)
        splitter_h.setSizes([320, 640, 440])

        splitter_v.addWidget(splitter_h)

        # Preview + save bar
        bottom = QWidget()
        bot_lay = QVBoxLayout(bottom)
        bot_lay.setContentsMargins(8, 0, 8, 8)
        bot_lay.setSpacing(4)
        self._preview = Preview()
        bot_lay.addWidget(self._preview, 1)

        btns = QHBoxLayout()
        self._btn_save = ActionButton("Save", Category.LOAD, icon_name="save", primary=True)
        self._btn_save.clicked.connect(self._save)
        self._btn_reload = ActionButton("Reload", Category.TOOLS, icon_name="clear")
        self._btn_reload.clicked.connect(self._load_from_disk)
        btns.addStretch(1)
        btns.addWidget(self._btn_reload)
        btns.addWidget(self._btn_save)
        bot_lay.addLayout(btns)
        splitter_v.addWidget(bottom)

        splitter_v.setStretchFactor(0, 4)
        splitter_v.setStretchFactor(1, 1)

        outer.addWidget(splitter_v, 1)

    def _build_script_switcher(self) -> QWidget:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._script_combo = QComboBox()
        self._script_combo.setMinimumWidth(220)
        self._script_combo.currentIndexChanged.connect(self._on_script_changed)
        lay.addWidget(self._script_combo)

        btn_new = QToolButton()
        btn_new.setIcon(icon("new"))
        btn_new.setIconSize(QSize(16, 16))
        btn_new.setAutoRaise(True)
        btn_new.setToolTip("New script")
        btn_new.clicked.connect(self._new_script)
        lay.addWidget(btn_new)

        btn_rename = QToolButton()
        btn_rename.setIcon(icon("config"))
        btn_rename.setIconSize(QSize(16, 16))
        btn_rename.setAutoRaise(True)
        btn_rename.setToolTip("Rename current script")
        btn_rename.clicked.connect(self._rename_script)
        lay.addWidget(btn_rename)

        btn_delete = QToolButton()
        btn_delete.setIcon(icon("delete"))
        btn_delete.setIconSize(QSize(16, 16))
        btn_delete.setAutoRaise(True)
        btn_delete.setToolTip("Delete current script")
        btn_delete.clicked.connect(self._delete_script)
        lay.addWidget(btn_delete)

        return host

    # ==================================================================
    # Disk I/O
    # ==================================================================

    def _load_from_disk(self) -> None:
        for section in self._sections:
            scripts = load_scripts(self._config_path, key=section["key"])
            for s in scripts:
                s.setdefault("name", "Untitled")
                s.setdefault("steps", [])
            section["scripts"] = scripts

        # Restrict the experiment-level palette + validation to the tracking
        # type: from global: (tracking_config.yaml) or the project design.
        cfg = self._read_global_config()
        tt = cfg.get("tracking_type")
        if tt is None and cfg.get("experiment_type"):
            try:
                from .. import experiment_types as _et
                tt = _et.get_experiment_type(
                    cfg["experiment_type"]).resolve_tracking_type(cfg).name
            except Exception:  # noqa: BLE001
                tt = None
        exp_type = str(tt).strip().upper() if tt else None
        self._experiment_type = exp_type
        self._apply_section()
        ## After, not before: loading steps into the canvas emits
        ## stepsChanged, which would otherwise mark a fresh load dirty.
        self._dirty = False
        self._update_dirty_indicator()

        # Surface YAML-inherited defaults as placeholder text in the
        # inspector. Right now only `cutoffs` inherits.
        inherited: dict[str, str] = {}
        cutoffs = cfg.get("facet_cutoffs")
        if isinstance(cutoffs, (list, tuple)) and cutoffs:
            inherited["cutoffs"] = ", ".join(str(c) for c in cutoffs)
        self._inspector.set_inherited(inherited)

        # Live options for multilist params: run_in_experiments' `only` picks
        # from the replicate directory names — the project.yaml's sibling
        # directories holding a tracking_config.yaml, no Project load needed.
        choices: dict[str, list[str]] = {}
        if self._config_path.name == "project.yaml":
            root = self._config_path.parent
            try:
                choices["only"] = sorted(
                    d.name for d in root.iterdir()
                    if d.is_dir() and layout.has_config(d))
            except OSError:
                pass
        self._inspector.set_choices(choices)

        # The canvas warning triangles were painted before the tracking type
        # was known; repaint them so they agree with the inspector banner and
        # the save gate.
        self._refresh_errors()

    def _read_global_config(self) -> dict:
        """The ``global:`` block (tracking_config.yaml), or the design's
        global block (project.yaml), or ``{}``."""
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001
            return {}
        if self._config_path.name == "project.yaml":
            return (data.get("design") or {}).get("global") or {}
        return data.get("global") or {}

    def script_validation_errors(self) -> list[str]:
        """Human-readable problems across *every* script, not just the visible one.

        The inspector shows its error banner but still emits ``paramsChanged``
        (it has to — the canvas must track what the user typed), and a value
        loaded from a hand-edited YAML never passes through a widget at all.
        Without this gate an out-of-range recipe reached disk behind a "Saved"
        dialog.
        """
        errors: list[str] = []
        for section in self._sections:
            validator = section["validator"]
            exp_type = (None if section["actions"] is PROJECT_ACTIONS
                        else self._experiment_type)
            for script in section["scripts"]:
                name = script.get("name", "Untitled")
                steps = script.get("steps", [])
                for i, err in validator(steps, experiment_type=exp_type):
                    errors.append(
                        f"{section['label']}: {name} — step {i + 1}: {err}")
        return errors

    def _save(self) -> None:
        errors = self.script_validation_errors()
        if errors:
            # Writing anyway hands the runner a recipe it will refuse to run,
            # while the "Saved" dialog tells the user everything is fine.
            QMessageBox.warning(
                self,
                "Fix these steps first",
                "The scripts were not saved:\n\n  • " + "\n  • ".join(errors),
            )
            return
        try:
            for section in self._sections:
                save_scripts(self._config_path, section["scripts"],
                             key=section["key"])
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(err))
            return
        self._dirty = False
        self._update_dirty_indicator()
        self.scriptsSaved.emit(str(self._config_path))
        QMessageBox.information(self, "Saved", f"Scripts saved to {self._config_path}")

    # ==================================================================
    # Script switcher
    # ==================================================================

    def _refresh_script_combo(self) -> None:
        self._script_combo.blockSignals(True)
        self._script_combo.clear()
        for s in self._scripts:
            self._script_combo.addItem(s.get("name", "Untitled"))
        if 0 <= self._active_idx < len(self._scripts):
            self._script_combo.setCurrentIndex(self._active_idx)
        self._script_combo.blockSignals(False)

    def _on_script_changed(self, idx: int) -> None:
        if idx < 0 or idx == self._active_idx:
            return
        self._active_idx = idx
        self._load_active_script_into_canvas()

    def _new_script(self) -> None:
        name, ok = QInputDialog.getText(self, "New script", "Name:")
        if not ok or not name.strip():
            return
        self._scripts.append({"name": name.strip(), "steps": []})
        self._active_idx = len(self._scripts) - 1
        self._mark_dirty()
        self._refresh_script_combo()
        self._load_active_script_into_canvas()

    def _rename_script(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            return
        current = self._scripts[self._active_idx]
        name, ok = QInputDialog.getText(
            self, "Rename script", "Name:", text=current.get("name", "")
        )
        if not ok or not name.strip():
            return
        current["name"] = name.strip()
        self._mark_dirty()
        self._refresh_script_combo()

    def _delete_script(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            return
        ans = QMessageBox.question(
            self, "Delete script",
            f"Delete script {self._scripts[self._active_idx].get('name')!r}?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self._scripts[self._active_idx]
        self._active_idx = min(self._active_idx, len(self._scripts) - 1)
        self._mark_dirty()
        self._refresh_script_combo()
        self._load_active_script_into_canvas()

    # ==================================================================
    # Canvas ↔ Inspector wiring
    # ==================================================================

    def _load_active_script_into_canvas(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            self._canvas.load_steps([])
            self._inspector.clear()
            self._refresh_preview()
            return
        script = self._scripts[self._active_idx]
        self._canvas.load_steps(list(script.get("steps", [])))
        self._refresh_preview()

    def _on_action_activated(self, key: str) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            self._scripts.append({"name": "New script", "steps": []})
            self._active_idx = len(self._scripts) - 1
            self._refresh_script_combo()
        self._canvas.add_action(key)
        self._sync_canvas_to_scripts()
        self._mark_dirty()

    def _on_step_selected(self, index: int) -> None:
        if index < 0:
            self._inspector.clear()
            return
        step = self._canvas.current_step()
        if step is None:
            self._inspector.clear()
            return
        action = self._section_actions().get(step.get("action", ""))
        if action is None:
            self._inspector.clear()
            return
        self._inspector.show_step(index, action, step.get("params", {}))

    def _on_params_changed(self, index: int, params: dict) -> None:
        self._canvas.update_params(index, params)
        self._sync_canvas_to_scripts()
        self._mark_dirty()
        self._refresh_preview()
        self._refresh_errors()

    def _on_steps_changed(self) -> None:
        self._sync_canvas_to_scripts()
        self._mark_dirty()
        self._refresh_preview()
        self._refresh_errors()

    def _sync_canvas_to_scripts(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            return
        self._scripts[self._active_idx]["steps"] = self._canvas.steps()

    def _refresh_errors(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            return
        steps = self._scripts[self._active_idx].get("steps", [])
        issues = self._section()["validator"](
            steps, experiment_type=self._section_exp_type())
        by_idx: dict[int, list[str]] = {}
        for i, err in issues:
            by_idx.setdefault(i, []).append(err)
        self._canvas.set_errors(by_idx)

    def _refresh_preview(self) -> None:
        if not (0 <= self._active_idx < len(self._scripts)):
            self._preview.set_text("# No active script")
            return
        script = self._scripts[self._active_idx]
        try:
            text = yaml.safe_dump(
                {self._section()["key"]: [script]}, default_flow_style=False,
                sort_keys=False, allow_unicode=True,
            )
        except Exception as err:  # noqa: BLE001
            text = f"# Could not serialize: {err}"
        self._preview.set_text(text)

    # ==================================================================
    # Dirty indicator / theme
    # ==================================================================

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_dirty_indicator()

    def _update_dirty_indicator(self) -> None:
        self._dirty_lbl.setText("●  unsaved changes" if self._dirty else "")

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        new_mode = "dark" if resolved_mode() == "light" else "light"
        apply_theme(app, mode=new_mode)
        self._btn_theme.setIcon(
            icon("theme_dark" if resolved_mode() == "light" else "theme_light")
        )
