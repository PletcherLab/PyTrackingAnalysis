"""Analysis Hub — the main entry point for PyTrackingAnalysis.

Loads a tracking experiment, exposes single + batch analyses and per
tracking-type plot actions as category-coloured buttons, routes figures
and logs to a tabbed :class:`~pytrackinganalysis.ui.widgets.PlotDock`, and
launches the Config Editor and QC Viewer apps in their own subprocesses.

Modelled on pyflic's ``base/analysis_hub.py``; see the plan file for the
card-by-card breakdown.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..gui_env import sanitize_input_method_environment

sanitize_input_method_environment()

# Force a non-GUI matplotlib backend before the domain layer imports pyplot.
import matplotlib

matplotlib.use("Agg")

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import Experiment as ExperimentMod
from .batch_preflight import (
    BatchPreflightDialog,
    PROJECT_COLUMN_MAX_WIDTH,
    blocked_color,
)
from ._hub_tiles import (
    ClickAwayFilter,
    StatusPanel,
    StatusTile,
    TilePanel,
    chrome_colors,
)
from ..help import HelpButton, make_topbar_help_button
from ..ui import (
    ActionButton,
    Card,
    Category,
    OutputLog,
    PlotDock,
    TopBar,
    ZoomableImageView,
    ZoomableTextView,
    app_icon,
    apply_theme,
    icon,
    resolved_mode,
)
from ..ui import settings as ui_settings
from .common import TaskWorker, capture_figures, shutdown_worker


# Lines like "Saved: /path/to/file.png" come out of the various Experiment
# methods that write artifacts to disk. We mirror them as zoomable PlotDock
# tabs in the Hub.
_SAVED_RE = re.compile(r"^\s*Saved:\s+(\S.*?)\s*$")

# The only config file ``Experiment.__init__`` ever opens — it takes a project
# directory and joins this name onto it, with no parameter to override it.
_CANONICAL_CONFIG = "tracking_config.yaml"
## The replicates table is sized to its rows (``_fit_exp_table_height``):
## enough for one row when the Project is empty, and never so tall that the
## Analysis section below it falls off the panel.
_EXP_TABLE_MIN_HEIGHT = 70
_EXP_TABLE_MAX_HEIGHT = 170


# Per-tracking-type plot buttons for the Plots card. Each entry is
# (label, flat_method, facet_method); the Hub picks the variant based on the
# Analyze card's facet checkbox and updates button labels accordingly.
_PLOT_BUTTONS: dict[str, list[tuple[str, str, str]]] = {
    "TRACKER": [
        ("Total distance", "plot_totaldistance", "plot_totaldistance_facet"),
    ],
    "TWOCHOICETRACKER": [
        ("PI", "plot_pi", "plot_pi_facet"),
        ("Percentage", "plot_percentage", "plot_percentage_facet"),
        ("Transitions", "plot_transitions", "plot_transitions_facet"),
        ("Total distance", "plot_totaldistance", "plot_totaldistance_facet"),
    ],
    "TWOCHOICECOUNTER": [
        ("PI", "plot_pi", "plot_pi_facet"),
        ("Percentage", "plot_percentage", "plot_percentage_facet"),
    ],
    "XCHOICETRACKER": [
        ("Adjusted X position", "plot_adjusted_x_position", "plot_adjusted_x_position_facet"),
        ("Total distance", "plot_totaldistance", "plot_totaldistance_facet"),
    ],
    "PAIRWISEINTERACTIONTRACKER": [
        ("Interactions", "plot_interactions", "plot_interactions_facet"),
        ("Total distance", "plot_totaldistance", "plot_totaldistance_facet"),
    ],
    "PAIRWISEINTERACTIONCOUNTER": [
        ("Interactions", "plot_interactions", "plot_interactions_facet"),
    ],
}

_PLOT_ICON_BY_LABEL: dict[str, str] = {
    "Total distance": "distance",
    "PI": "plot",
    "Percentage": "plot",
    "Transitions": "transition",
    "Adjusted X position": "xy",
    "Interactions": "plot",
}


#: Tracking types as the Experiment tile names them: a word or two of
#: plain English rather than the enum's shouting (``TWOCHOICETRACKER``).
_TRACKING_TYPE_LABELS = {
    "TRACKER": "tracker",
    "TWOCHOICETRACKER": "two-choice tracker",
    "XCHOICETRACKER": "X-choice tracker",
    "DDROPTRACKER": "D-drop tracker",
    "PAIRWISEINTERACTIONTRACKER": "pairwise tracker",
    "CENTROPHOBISMTRACKER": "centrophobism tracker",
    "COUNTER": "counter",
    "TWOCHOICECOUNTER": "two-choice counter",
    "PAIRWISEINTERACTIONCOUNTER": "pairwise counter",
}


def _tracking_type_label(exp) -> str | None:
    """The loaded experiment's tracking type in plain words, or None when
    the object cannot say (a partial experiment, a test double)."""
    try:
        name = exp.parameters.get_tracking_type().name
    except Exception:  # noqa: BLE001
        return None
    return _TRACKING_TYPE_LABELS.get(str(name), str(name).lower())


class HubWindow(QMainWindow):
    """The Analysis Hub main window."""

    #: The four tiles the Experiment tile expands into (ADR-0012). Their
    #: panels anchor under the sub-strip, so opening one expands it.
    _EXPERIMENT_SUBTILES = ("analyze", "plots", "scripts", "ai")
    #: The sub-strip's fixed height: one compact tile plus its bottom margin.
    _SUB_STRIP_HEIGHT = StatusTile.COMPACT_HEIGHT + 8

    _BATCH_PROJECT_COLUMN_MAX_WIDTH = PROJECT_COLUMN_MAX_WIDTH

    def __init__(self, initial_project: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("PyTrackingAnalysis — Analysis Hub")
        self.resize(1350, 860)

        self._exp: ExperimentMod.Experiment | None = None
        self._project_dir: Path | None = None
        self._worker: TaskWorker | None = None
        #: What to reveal once a load finishes: "analyze" (the panel) or
        #: "group" (the Experiment tile's sub-strip) — or nothing.
        self._reveal_after_load: str | None = None
        # (app_name, Popen) for child apps launched from the Tools card. They run
        # independently of the Hub; tracked only so an immediate failure is visible.
        self._subapps: list[tuple[str, object]] = []
        # Maps a Plots-card ActionButton to its Arena method name; rebuilt whenever
        # an experiment is loaded so the buttons always reflect the tracking type.
        self._plot_buttons: list[ActionButton] = []
        # Cards are staged once, then moved into anchored tile panels.
        self._cards: dict[str, Card] = {}
        self._scripts: list[dict] = []
        # Resolved artifact path → tab widget. Used to deduplicate tabs when
        # the same file is re-saved by a subsequent task run. Stored by widget
        # (not index) so it survives the user closing other tabs.
        self._artifact_tabs: dict[str, QWidget] = {}

        self._build_ui()

        if initial_project:
            self._set_project_dir(initial_project)

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
        self._top_bar = TopBar("PyTrackingAnalysis — Analysis Hub")
        ## No "Interactive plots" toggle (user feedback 2026-08-29): it was
        ## never checked, so figures always render as static PNGs — the
        ## PlotDock still supports live canvases (add_figure interactive=)
        ## for any caller that wants one.
        self._top_bar.add_right(make_topbar_help_button(self, topic_id="getting_started"))
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

        # ── Body: tile strip + full-width output — ADR-0007 ───────────────
        main_col = QWidget()
        main_lay = QVBoxLayout(main_col)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # The tile ribbon (ADR-0012): a strip of three container tiles —
        # Batch · Project · Experiment — and, under it, the sub-strip the
        # Experiment tile expands into: the four experiment-level tiles.
        # Tiles are compact live-status chips; every control lives in a
        # tile's anchored panel.
        self._ribbon = QWidget()
        self._ribbon.setObjectName("TileRibbon")
        ribbon_lay = QVBoxLayout(self._ribbon)
        ribbon_lay.setContentsMargins(0, 0, 0, 0)
        ribbon_lay.setSpacing(0)
        self._strip = QFrame()
        self._strip.setObjectName("TileStrip")
        ## Fixed vertically: when the sub-strip hides, the ribbon keeps its
        ## taller geometry until the next relayout, and a Preferred strip
        ## would stretch into that space — so a panel anchored to "the
        ## strip's bottom" that instant landed a sub-strip too low.
        self._strip.setSizePolicy(QSizePolicy.Policy.Preferred,
                                  QSizePolicy.Policy.Fixed)
        strip_lay = QHBoxLayout(self._strip)
        strip_lay.setContentsMargins(10, 8, 10, 8)
        ## Distinct chips with a hairline seam (user feedback 2026-08-22):
        ## flush tiles merged into one unreadable bar, wide gaps read as
        ## dead space — 2px is the middle ground.
        strip_lay.setSpacing(2)
        self._tiles: dict[str, StatusTile] = {}
        ## Leftmost: the containment hierarchy reads left-to-right — a Batch
        ## holds Projects, a Project holds experiments (ADR-0009), and the
        ## loaded experiment holds the tools that act on it (ADR-0012). The
        ## three are one width, 1.75x a regular tile: three equal levels,
        ## and room for a replicate name with its counts.
        for key, title, icon_name, cat in (
            ## LOAD, not NEUTRAL: the batch glyph tints LOAD-blue, and the
            ## title matches its icon (user feedback 2026-08-22).
            ("batch", "Batch", "batch", Category.LOAD),
            ("project", "Project", "project", Category.NEUTRAL),
            ## QC red, not LOAD blue: three levels want three colors, and
            ## blue is Batch's (user feedback 2026-08-29). Red is otherwise
            ## unused on the ribbon — purple is the Scripts sub-tile's.
            ("experiment", "Experiment", "experiment", Category.QC),
        ):
            tile = StatusTile(key, title, icon_name, cat, wide=True)
            self._tiles[key] = tile
            strip_lay.addWidget(tile)
        self._tiles["batch"].clicked.connect(self._toggle_panel)
        self._tiles["project"].clicked.connect(self._toggle_panel)
        ## The Experiment tile opens no panel: it expands the sub-strip.
        self._tiles["experiment"].clicked.connect(self._toggle_experiment)
        ## The strip's leftover width was dead space; it now carries the
        ## general status readout (what is open right now) — and it got wider
        ## still when the Tools tile went, which is the point: the readout
        ## says which project and which experiment, and a truncated path
        ## answers neither.
        self._status_panel = StatusPanel()
        strip_lay.addWidget(self._status_panel, 1)
        ribbon_lay.addWidget(self._strip)

        ## The sub-strip: hidden until the Experiment tile expands it. Its
        ## tiles sit left-aligned under the Experiment tile (the indent is
        ## set when it opens — see _place_sub_strip) so they read as that
        ## tile's contents rather than a second, unrelated row. They are
        ## title-only (user feedback 2026-08-29): the Experiment tile above
        ## already says what is loaded, so each sub-tile needs only its
        ## name — its status is a hover away, in the tooltip.
        self._sub_strip = QFrame()
        self._sub_strip.setObjectName("TileSubStrip")
        sub_lay = QHBoxLayout(self._sub_strip)
        sub_lay.setContentsMargins(10, 0, 10, 8)
        sub_lay.setSpacing(2)
        for key, title, icon_name, cat in (
            ("analyze", "Analyze", "basic", Category.ANALYZE),
            ("plots", "Plots", "plots", Category.PLOTS),
            ("scripts", "Scripts", "scripts", Category.SCRIPTS),
            ("ai", "AI", "ai", Category.AI),
        ):
            tile = StatusTile(key, title, icon_name, cat, compact=True)
            tile.clicked.connect(self._toggle_panel)
            self._tiles[key] = tile
            sub_lay.addWidget(tile)
        sub_lay.addStretch(1)
        self._sub_strip.setFixedHeight(self._SUB_STRIP_HEIGHT)
        self._sub_strip.hide()
        self._experiment_expanded = False
        ribbon_lay.addWidget(self._sub_strip)
        ## Any transient surplus height goes here, never to a row.
        ribbon_lay.addStretch(1)
        main_lay.addWidget(self._ribbon)

        # The output area — the whole point of the redesign: full width,
        # full height below the strip.
        self._log = OutputLog()
        self._err_log = OutputLog()
        self._plot_dock = PlotDock(self._log, self._err_log)
        main_lay.addWidget(self._plot_dock, 1)

        outer.addWidget(main_col, 1)

        # Build the existing cards (unchanged) into a hidden staging host,
        # then move each into its tile's anchored panel.
        cards_host = QWidget()
        self._cards_lay = QVBoxLayout(cards_host)
        self._build_batch_card()
        self._build_project_card()
        self._build_experiments_card()
        self._build_project_view_card()
        self._build_analyze_card()
        self._build_plots_card()
        self._build_scripts_card()
        self._build_ai_card()

        self._panels: dict[str, TilePanel] = {}
        panel_map = {
            "batch": (620, ["batch"]),
            ## Three sections, top to bottom: project identity, the
            ## replicates, then what to do with them (ADR-0005).
            "project": (640, ["project", "experiments", "projectview"]),
            ## Experiment-level panels are a column of buttons, and a button
            ## only needs its label: these widths give the longest label in
            ## each panel a little air and no more (user feedback
            ## 2026-08-29 — the 440–500px panels stretched every button to
            ## twice what it said).
            "analyze": (310, ["analyze"]),
            "plots": (260, ["plots"]),
            "scripts": (390, ["scripts"]),
            "ai": (270, ["ai"]),
        }
        for key, (width, card_keys) in panel_map.items():
            panel = TilePanel(key, width, parent=central)
            for card_key in card_keys:
                card = self._cards.get(card_key)
                if card is not None:
                    panel.add_card(card)
            panel.finish()
            self._panels[key] = panel
        self._open_panel_key: str | None = None
        self._batch_panel_root: Path | None = None
        self._load_ready = False
        ## App-level filter: a click outside the open panel (and outside the
        ## strip) closes it; the click itself is NOT swallowed. A dedicated
        ## QObject (never the window itself) — see ClickAwayFilter.
        self._click_filter = ClickAwayFilter(self)
        QApplication.instance().installEventFilter(self._click_filter)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  self._close_panel,
                  context=Qt.ShortcutContext.WindowShortcut)

        # Progress bar (hidden until a task runs)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(4)
        outer.addWidget(self._progress)

        self._log.append_line(
            "Welcome to PyTrackingAnalysis. Click the Project tile to open or "
            "create a Project, then double-click a replicate in its table to "
            "load that experiment."
        )
        self._restyle_chrome()
        self._refresh_tiles()

    def _restyle_chrome(self) -> None:
        """Skin the strip, tiles, and panels for the CURRENT theme — palette
        roles cannot be trusted under qdarktheme (see chrome_colors)."""
        chrome = chrome_colors()
        ## No band (user feedback 2026-08-22): the strip is transparent and
        ## the tiles float on the window background — their chip color is
        ## offset from the window (see StatusTile._restyle) so they stay
        ## legible without any surrounding surface.
        self._strip.setStyleSheet(
            "QFrame#TileStrip { background: transparent; border: none; }")
        self._sub_strip.setStyleSheet(
            "QFrame#TileSubStrip { background: transparent; border: none; }")
        for tile in self._tiles.values():
            tile.restyle()
        self._status_panel.restyle()
        for panel in self._panels.values():
            panel.restyle()
        ## Card surfaces are theme colors too, dimmed ones especially.
        for card in self._cards.values():
            card.restyle()

    # ---------------- Project card ----------------

    def _build_project_card(self) -> None:
        card = Card(
            "Create/Load",
            category=Category.NEUTRAL,
            subtitle="Open a Project directory and edit its project.yaml.",
            icon_name="project",
        )
        card.add_title_widget(
            HelpButton(
                "project_create",
                tooltip="Open / Create / Initialize a Project, and its "
                        "project.yaml",
            )
        )
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._project_edit = QLineEdit()
        self._project_edit.setPlaceholderText("/path/to/project/folder")
        self._project_edit.setReadOnly(True)
        # One action, not two: reloading a Project was picking the same
        # directory again, so the picker is the reload.
        load_btn = ActionButton("Open Project", Category.NEUTRAL,
                                icon_name="browse")
        load_btn.setToolTip(
            "Open a directory that already holds a project.yaml. Picking the "
            "one already open re-reads it from disk — replicates added or "
            "analyzed outside the Hub show up.")
        load_btn.clicked.connect(self._pick_project_dir)
        # Path on its own line so long paths stay readable; buttons below.
        form.addRow("Project dir:", self._project_edit)
        card.add_body(form)

        # project.yaml is fixed in the Project directory — Edit when present,
        # Create (writes a default) when missing; both open the Project editor.
        # QC viewer is experiment-level and lives on the Experiment tile.
        self._btn_edit_cfg = ActionButton("Edit config…", Category.NEUTRAL,
                                          icon_name="config")
        self._btn_edit_cfg.setEnabled(False)
        self._btn_edit_cfg.clicked.connect(self._edit_or_create_project_config)
        # Making a Project is project-level work: it belongs beside the
        # directory it writes into, not in the Experiment (Load) card.
        new_project_btn = ActionButton("Create project…", Category.NEUTRAL,
                                       icon_name="project")
        new_project_btn.setToolTip(
            "Make a Project that does not exist yet: choose where it goes, "
            "name it, and fill in the project.yaml information. The directory "
            "is created for you."
        )
        new_project_btn.clicked.connect(self._new_project)
        init_btn = ActionButton("Initialize existing directory…",
                                Category.NEUTRAL, icon_name="project")
        init_btn.setToolTip(
            "Turn a directory you already have into a Project: it keeps its "
            "own name, any experiment subdirectories already in it become the "
            "replicates, and project.yaml is written there."
        )
        init_btn.clicked.connect(self._initialize_existing_directory)
        btn_validate = ActionButton("Validate YAMLs", Category.NEUTRAL,
                                    icon_name="lint")
        btn_validate.setToolTip(
            "Check this Project's project.yaml AND every replicate's "
            "tracking_config.yaml — parse errors and semantic problems "
            "(unknown rig, missing calibration, design mismatch) alike. "
            "Results go to the log.")
        btn_validate.clicked.connect(self._validate_yaml)
        self._btn_validate = btn_validate
        ## Two columns, four buttons, no ragged row: the three ways in (the
        ## Project exists / does not exist / the directory does but the
        ## project.yaml does not), then the editor for the one that is open.
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for i, btn in enumerate((load_btn, new_project_btn,
                                 init_btn, self._btn_edit_cfg)):
            grid.addWidget(btn, i // 2, i % 2)
        ## Full width on its own row: it is not a fifth way into a Project,
        ## it is the check you run over the one that is open.
        grid.addWidget(btn_validate, 2, 0, 1, 2)
        for col in range(2):
            grid.setColumnStretch(col, 1)
        card.add_body(grid)
        self._create_load_grid = grid

        ## What is loaded, described: name, type, replicate count, design
        ## factors and any load warnings. Project info, so it sits with the
        ## project it describes rather than over the replicates table.
        self._projectview_summary = QLabel("")
        self._projectview_summary.setWordWrap(True)
        card.add_body(self._projectview_summary)

        self._cards["project"] = card
        self._cards_lay.addWidget(card)

    # ---------------- Analyze card ----------------

    def _build_analyze_card(self) -> None:
        card = Card(
            "Analyze",
            category=Category.ANALYZE,
            subtitle="Summaries, QC, stats, and PDF reports.",
            icon_name="basic",
        )
        card.add_title_widget(
            HelpButton("hub_workflow", tooltip="Analysis Hub workflow")
        )

        # Facet checkbox controls whether Summarize / Pairwise / Plots buttons
        # call the faceted variants. Disabled until a project is loaded; its
        # label is rewritten then to show the configured cutoffs.
        self._facet_checkbox = QCheckBox("Faceted (no project loaded)")
        self._facet_checkbox.setEnabled(False)
        self._facet_checkbox.setToolTip(
            "When checked, Summarize, Run pairwise comparisons, and the Plots "
            "buttons all call their faceted variants using the cutoffs from "
            "the project's tracking_config.yaml."
        )
        self._facet_checkbox.toggled.connect(self._on_facet_toggled)
        self._facet_checkbox.toggled.connect(lambda _on: self._refresh_tiles())
        card.add_body(self._facet_checkbox)

        # Tracks (button, base_label) for any button whose text gets the
        # "(facet)" suffix appended when the checkbox is on.
        self._dynamic_label_buttons: list[tuple[ActionButton, str]] = []

        self._btn_run_analysis = ActionButton(
            "Run Analysis", Category.ANALYZE, icon_name="basic", primary=True
        )
        self._btn_run_analysis.clicked.connect(self._run_full_analysis)

        self._btn_run_qc = ActionButton(
            "Run QC only", Category.QC, icon_name="quality"
        )
        self._btn_run_qc.clicked.connect(self._run_qc_only)

        self._btn_create_report = ActionButton(
            "Create PDF Report", Category.ANALYZE, icon_name="report"
        )
        self._btn_create_report.clicked.connect(self._run_create_report)

        self._btn_summarize = ActionButton(
            "Summarize", Category.ANALYZE, icon_name="csv"
        )
        self._btn_summarize.clicked.connect(self._run_summarize)
        self._dynamic_label_buttons.append((self._btn_summarize, "Summarize"))

        self._btn_pairwise = ActionButton(
            "Run pairwise comparisons", Category.ANALYZE, icon_name="compare"
        )
        self._btn_pairwise.clicked.connect(self._run_pairwise)
        self._dynamic_label_buttons.append((self._btn_pairwise, "Run pairwise comparisons"))

        for btn in (
            self._btn_run_analysis,
            self._btn_run_qc,
            self._btn_create_report,
            self._btn_summarize,
            self._btn_pairwise,
        ):
            card.add_body(btn)
            btn.setEnabled(False)

        self._cards["analyze"] = card
        self._cards_lay.addWidget(card)

    # ---------------- Plots card ----------------

    def _build_plots_card(self) -> None:
        card = Card(
            "Plots",
            category=Category.PLOTS,
            subtitle="Figures appear as tabs on the right.",
            icon_name="plots",
        )
        card.add_title_widget(
            HelpButton("hub_workflow", tooltip="Plots and Hub workflow")
        )
        self._plots_empty = QLabel(
            "Load an experiment to see available plots for its tracking type."
        )
        self._plots_empty.setStyleSheet("color: palette(mid); font-style: italic;")
        self._plots_empty.setWordWrap(True)
        card.add_body(self._plots_empty)
        self._cards["plots"] = card
        self._plots_card = card
        self._cards_lay.addWidget(card)

    # ---------------- Scripts card ----------------

    def _build_scripts_card(self) -> None:
        card = Card(
            "Scripts",
            category=Category.SCRIPTS,
            subtitle="Run saved recipes from the Script Editor.",
            icon_name="scripts",
        )
        card.add_title_widget(
            HelpButton("scripts_overview", tooltip="Scripts and Script Editor")
        )

        self._scripts_combo = QComboBox()
        self._scripts_combo.setToolTip(
            "Scripts defined under the 'scripts:' key of the active config."
        )
        card.add_body(self._scripts_combo)

        row = QHBoxLayout()
        self._btn_run_script = ActionButton(
            "Run Script", Category.SCRIPTS, icon_name="script", primary=True
        )
        self._btn_run_script.clicked.connect(self._run_selected_script)
        self._btn_run_all_scripts = ActionButton(
            "Run All", Category.SCRIPTS, icon_name="run"
        )
        self._btn_run_all_scripts.clicked.connect(self._run_all_scripts)
        self._btn_refresh_scripts = ActionButton(
            "Reload", Category.TOOLS, icon_name="clear"
        )
        self._btn_refresh_scripts.clicked.connect(self._refresh_scripts)
        row.addWidget(self._btn_run_script)
        row.addWidget(self._btn_run_all_scripts)
        row.addWidget(self._btn_refresh_scripts)
        card.add_body(row)

        self._scripts_empty = QLabel(
            "No scripts yet. Use the Config Editor's Script Editor to add some."
        )
        self._scripts_empty.setStyleSheet("color: palette(mid); font-style: italic;")
        self._scripts_empty.setWordWrap(True)
        card.add_body(self._scripts_empty)

        for w in (self._scripts_combo, self._btn_run_script, self._btn_run_all_scripts):
            w.setEnabled(False)

        self._cards["scripts"] = card
        self._cards_lay.addWidget(card)

    # ---------------- AI card ----------------

    def _build_ai_card(self) -> None:
        """AI-assisted functions (one for now: the AI Summary, ADR-0004).

        The action is offered only when a provider API key is present in the
        environment / a ``.env`` file — presence is checked, validity is not;
        a bad key surfaces as an error when a summary is requested."""
        from ..ai import available_providers

        card = Card(
            "AI",
            category=Category.AI,
            subtitle="AI-assisted functions.",
            icon_name="ai",
        )
        card.add_title_widget(
            HelpButton("ai_summary", tooltip="AI summary")
        )
        self._ai_available = bool(available_providers())
        self._btn_ai_summary = ActionButton(
            "AI summary…", Category.AI, icon_name="ai", primary=True
        )
        self._btn_ai_summary.setToolTip(
            "Have an AI provider write a one-page summary of the current "
            "analysis and embed it in the report."
        )
        self._btn_ai_summary.clicked.connect(self._open_ai_summary_dialog)
        self._btn_ai_summary.setEnabled(False)
        card.add_body(self._btn_ai_summary)
        if not self._ai_available:
            note = QLabel(
                "No API key found. Add ANTHROPIC_API_KEY or OPENAI_API_KEY "
                "to a .env file (or the environment) and restart to enable "
                "AI features."
            )
            note.setStyleSheet("color: palette(mid); font-style: italic;")
            note.setWordWrap(True)
            card.add_body(note)
        self._cards["ai"] = card
        self._cards_lay.addWidget(card)

    def _open_ai_summary_dialog(self) -> None:
        if self._exp is None:
            return
        from .ai_summary_dialog import AiSummaryDialog

        AiSummaryDialog(self).exec()

    # ---------------- Experiments (the replicates of a Project) --------------

    def _build_experiments_card(self) -> None:
        """Section 2 of the Project tile: the replicates themselves — the
        status table plus the actions that create and configure experiments.

        Every action here inherits the shared design from project.yaml, so
        all three wait for a Project rather than offering a replicate with
        nothing to conform to (see ``_refresh_project_view``)."""
        card = Card(
            "Experiments",
            category=Category.NEUTRAL,
            icon_name="project",
        )
        card.add_title_widget(
            HelpButton("experiment_create",
                       tooltip="Create / Initialize a replicate, and how its "
                               "config is finished")
        )

        self._exp_table = QTableWidget(0, 6)
        self._exp_table.setHorizontalHeaderLabels(
            ["Experiment", "Config", "Flies", "Excluded", "Flagged", "Report"])
        # Config sits next to the name: for a folder that has none, it is the
        # only cell with anything in it. Contents-sized so all six columns fit
        # the card column without horizontal scrolling.
        header = self._exp_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._exp_table.verticalHeader().setVisible(False)
        self._exp_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._exp_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._exp_table.setMaximumHeight(_EXP_TABLE_MAX_HEIGHT)
        self._exp_table.itemDoubleClicked.connect(self._open_selected_replicate)
        card.add_body(self._exp_table)
        hint = QLabel("Double-click a replicate to open it as the current "
                      "experiment.")
        hint.setStyleSheet("color: palette(mid); font-style: italic;")
        card.add_body(hint)

        ## The same three cases as the Project above it, one level down: the
        ## replicate exists (the table), it does not exist at all (Create),
        ## or its directory does but its tracking_config.yaml does not
        ## (Initialize).
        create_exp_btn = ActionButton("Create experiment…", Category.NEUTRAL,
                                      icon_name="new")
        create_exp_btn.setToolTip(
            "Create a replicate directory and scaffold its "
            "tracking_config.yaml from the project design — everything but "
            "the name is inherited from project.yaml, so the shared design "
            "holds by construction.")
        create_exp_btn.clicked.connect(self._create_experiment)
        self._btn_create_experiment = create_exp_btn
        init_exp_btn = ActionButton("Initialize existing directory…",
                                    Category.NEUTRAL, icon_name="project")
        init_exp_btn.setToolTip(
            "Adopt a directory that is already in the Project but has no "
            "tracking_config.yaml: any loose recording is filed into data/ "
            "and every other loose file into extra_files/, the config is "
            "scaffolded from the project design, and the Config Editor opens "
            "on it.")
        init_exp_btn.clicked.connect(self._project_initialize_experiment)
        self._btn_init_experiment = init_exp_btn
        btn_configs = ActionButton("Experiment configs…", Category.NEUTRAL,
                                   icon_name="config")
        btn_configs.setToolTip(
            "Create or edit each experiment's tracking_config.yaml. The "
            "Project's shared design lives in project.yaml; the per-experiment "
            "configs live one level down, one per experiment directory.")
        btn_configs.clicked.connect(self._project_experiment_configs)
        self._btn_exp_configs = btn_configs

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for i, btn in enumerate((create_exp_btn, init_exp_btn, btn_configs)):
            grid.addWidget(btn, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        card.add_body(grid)
        self._experiments_actions_grid = grid

        ## Nothing to configure or add to until a Project is loaded.
        self._set_project_experiment_actions_enabled(False)

        self._cards["experiments"] = card
        self._cards_lay.addWidget(card)

    # ---------------- Project view (a directory of replicates) ----------------

    def _build_project_view_card(self) -> None:
        """Section 3 of the Project tile: what you DO with a Project once its
        experiments exist — reports, figures, the AI narrative, removals, and
        the script runner at the bottom (ADR-0005)."""
        card = Card(
            "Analysis",
            category=Category.NEUTRAL,
            icon_name="project",
        )
        card.add_title_widget(
            HelpButton("project_actions", tooltip="Project actions and combined analysis")
        )

        btn_plots = ActionButton("Plot editor…", Category.NEUTRAL,
                                 icon_name="report")
        btn_plots.clicked.connect(
            lambda: self._launch_subapp("plots", self._project_root()))
        btn_report = ActionButton("Create report", Category.NEUTRAL,
                                  icon_name="report")
        btn_report.clicked.connect(self._project_report)
        self._btn_project_report = btn_report
        btn_view_reports = ActionButton("View reports", Category.NEUTRAL,
                                        icon_name="report")
        btn_view_reports.clicked.connect(self._project_view_reports)
        self._btn_view_reports = btn_view_reports
        btn_ai = ActionButton("AI narrative…", Category.NEUTRAL, icon_name="ai")
        btn_ai.setToolTip(
            "Have an AI provider write the project narrative from the "
            "Combined Analysis and rebuild the Project report to embed it.")
        btn_ai.clicked.connect(self._project_ai_narrative)
        btn_removals = ActionButton("Removed regions…", Category.NEUTRAL,
                                    icon_name="clear")
        btn_removals.setToolTip(
            "Declare tracking regions the experimenter removed from the "
            "analysis — a dead or escaped fly, an empty well. Every fly in a "
            "removed region is excluded from figures, statistics and the "
            "summary CSVs, and named with its reason in the reports. See the "
            "'Removed regions' help topic.")
        btn_removals.clicked.connect(self._project_removed_regions)
        self._btn_removals = btn_removals

        ## Three equal columns, in the order the work happens: build the
        ## report, shape its figures, write its narrative — then review what
        ## came out and correct the flies that should not have counted.
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for i, btn in enumerate((btn_report, btn_plots, btn_ai,
                                 btn_view_reports, btn_removals)):
            grid.addWidget(btn, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        card.add_body(grid)
        self._project_actions_grid = grid

        script_row = QHBoxLayout()
        script_row.addWidget(QLabel("Script:"))
        self._project_script_combo = QComboBox()
        ## Sized by the row, not by the longest script name: AdjustToContents
        ## let one long name push Run/Edit off the card and onto a second row.
        self._project_script_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._project_script_combo.setMinimumContentsLength(12)
        self._project_script_combo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                                 QSizePolicy.Policy.Fixed)
        script_row.addWidget(self._project_script_combo, 1)
        btn_run_script = ActionButton("Run script", Category.NEUTRAL,
                                      icon_name="scripts")
        btn_run_script.setToolTip(
            "Run the selected Project Script — this project's own scripts "
            "first (every project.yaml is created with a default one), then "
            "the built-ins: 'Standard pipeline' (validate design → project "
            "report → publication figures) and 'Report pipeline' (the same, "
            "ungated). The report step is this card's Create-report button, "
            "so it analyzes and pools before building the PDF; the figure "
            "step is skipped when there is no plot_specs.yaml.")
        btn_run_script.clicked.connect(self._project_run_script)
        btn_edit_scripts = ActionButton("Edit scripts…", Category.NEUTRAL,
                                        icon_name="config")
        btn_edit_scripts.setToolTip(
            "Open the Script Editor on project.yaml — Project Scripts plus "
            "the experiment scripts held centrally for every replicate.")
        btn_edit_scripts.clicked.connect(self._project_edit_scripts)
        script_row.addWidget(btn_run_script)
        script_row.addWidget(btn_edit_scripts)
        card.add_body(script_row)
        self._project_script_row = script_row

        card.setVisible(False)
        self._cards["projectview"] = card
        self._cards_lay.addWidget(card)

    def _project_root(self):
        """The selected Project, or None when the selection is not one.

        The selection is normalized to a Project root on the way in (see
        ``_set_project_dir``), so this never has to climb."""
        from .. import project as prj

        if not self._project_dir:
            return None
        return (Path(self._project_dir)
                if prj.is_project_dir(self._project_dir) else None)

    def _current_project(self):
        """The effective Project, loaded — or None (with the design-mismatch
        error surfaced when that is why)."""
        from .. import project as prj

        root = self._project_root()
        if root is None:
            return None
        try:
            return prj.Project(str(root))
        except Exception as err:  # noqa: BLE001
            self._log_issue(f"Project failed to load: {err}")
            ## The summary sits in the always-visible Create/Load card, so the
            ## failure is readable even though the sections below stay down.
            self._projectview_summary.setText(
                f"<b style='color:#dc2626'>Project failed to load:</b> {err}")
            return None

    def _fit_exp_table_height(self) -> None:
        """Size the replicates table to the rows it actually has.

        A fixed height reserved room for rows a two-replicate Project does not
        have, and that dead band is height the three sections of the panel
        need. Capped, so a large Project scrolls inside the table rather than
        pushing the Analysis section off the panel."""
        table = self._exp_table
        height = (table.horizontalHeader().height()
                  + 2 * table.frameWidth())
        for row in range(table.rowCount()):
            height += table.rowHeight(row)
        if table.rowCount() == 0:
            ## Empty still reads as a table, not a stripe.
            height += table.verticalHeader().defaultSectionSize()
        table.setFixedHeight(
            min(max(height, _EXP_TABLE_MIN_HEIGHT), _EXP_TABLE_MAX_HEIGHT))

    def _set_project_experiment_actions_enabled(self, enabled: bool) -> None:
        """Gate the Experiments card's actions on there being a Project.

        The card itself stays visible — it is where the replicates are, and an
        empty table with dimmed buttons says "load a Project" more plainly
        than an absent section does."""
        for name in ("_btn_create_experiment", "_btn_init_experiment",
                     "_btn_exp_configs"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(enabled)

    def _refresh_project_view(self) -> None:
        from .. import project as prj

        card = self._cards.get("projectview")
        if card is None:
            return
        ## The card stays up while a replicate is loaded: it represents the
        ## selected Project, with the loaded replicate highlighted, so project
        ## actions and replicate-hopping stay one click away.
        root = self._project_root()
        if root is None:
            card.setVisible(False)
            self._projectview_summary.setText("")
            self._exp_table.setRowCount(0)
            self._fit_exp_table_height()
            self._set_project_experiment_actions_enabled(False)
            return
        project = self._current_project()
        if project is None:
            self._exp_table.setRowCount(0)
            self._fit_exp_table_height()
            self._set_project_experiment_actions_enabled(False)
            return
        self._set_project_experiment_actions_enabled(True)
        loaded = self._experiment_dir()
        current_name = (loaded.name
                        if loaded is not None and loaded.parent == root
                        else None)
        factors = ";  ".join(
            f"{k}: {', '.join(v)}" for k, v in project.design_factors.items())
        pending = project.unconfigured_dirs()
        lines = [f"<b>{project.name}</b> — "
                 f"{project.experiment_type.display_name}, "
                 f"{len(project.experiment_names)} replicate(s)"]
        if factors:
            lines.append(factors)
        if pending:
            ## Discovery is by config file, so these folders would otherwise
            ## be invisible — the Project would look empty to the user who
            ## just created it around them.
            lines.append(
                f"<i>{len(pending)} folder(s) without a config: "
                f"{', '.join(pending)} — 'Experiment configs…' creates them.</i>")
        for warning in project.warnings:
            lines.append(f"<i>Note: {warning}</i>")
        self._projectview_summary.setText("<br>".join(lines))
        self._refresh_project_report_button(project)

        self._exp_table.setRowCount(0)
        for name in project.experiment_names:
            st = project.experiment_status(name)
            row = self._exp_table.rowCount()
            self._exp_table.insertRow(row)
            ## "no data" and "not analyzed" are different problems: the first
            ## needs the DTrack export dropped into data/, the second a run.
            if st["analyzed"]:
                flies = str(st["flies"])
            else:
                flies = "not analyzed" if st["has_data"] else "no data"
            values = [name, "yes", flies,
                      prj.format_excluded_cell(st) if st["excluded"] is not None
                      else ("re-run needed" if st["stale"] else "—"),
                      str(st["flagged"]) if st["flagged"] is not None else "—",
                      "yes" if st["report"] else "no"]
            for col, value in enumerate(values):
                self._exp_table.setItem(row, col, QTableWidgetItem(value))
            if current_name is not None and name == current_name:
                self._exp_table.selectRow(row)
        for name in pending:
            row = self._exp_table.rowCount()
            self._exp_table.insertRow(row)
            values = [name, "missing", "—", "—", "—", "—"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                # Italic: a folder without a config is not a replicate yet, so
                # it must not read as one that simply hasn't been analyzed.
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                self._exp_table.setItem(row, col, item)

        self._fit_exp_table_height()

        # Script picker: the Project's OWN scripts first — every project.yaml
        # is created with a default one, so the first entry is the run the
        # user can open and read in the Script Editor. The built-ins stay
        # available below as explicit choices (ADR-0009 amendment).
        self._project_script_combo.blockSignals(True)
        self._project_script_combo.clear()
        for script in project.scripts:
            self._project_script_combo.addItem(
                str(script.get("name", "Untitled")), script.get("name"))
        self._project_script_combo.addItem("Standard pipeline (built-in)",
                                           ("builtin", "standard"))
        self._project_script_combo.addItem("Report pipeline (built-in)",
                                           ("builtin", "report"))
        self._project_script_combo.setCurrentIndex(0)
        self._project_script_combo.blockSignals(False)
        card.setVisible(True)

    def _project_report_path(self, project) -> Path:
        return Path(project.project_directory) / f"{project.name}_report.pdf"

    def _project_report_exists(self, project) -> bool:
        return self._project_report_path(project).is_file()

    def _replicate_report_paths(self, project) -> list[Path]:
        """Every per-replicate report that exists on disk, in table order.

        A replicate's report is named for its DIRECTORY, not the recording
        inside it (``Experiment.create_report`` writes the project's front
        page), which is the same rule ``Project.experiment_status`` uses.
        """
        paths = []
        for name in project.experiment_names:
            candidate = (Path(project.experiment_dir(name))
                         / f"{name}_report.pdf")
            if candidate.is_file():
                paths.append(candidate)
        return paths

    def _refresh_project_report_button(self, project) -> None:
        btn = getattr(self, "_btn_project_report", None)
        if btn is None:
            return
        exists = self._project_report_exists(project)
        btn.setText("Update report" if exists else "Create report")
        btn.setToolTip(
            "Analyze all experiments, rebuild Combined Analysis, and update "
            "the Project report."
            if exists else
            "Analyze all experiments, build Combined Analysis, and create the "
            "Project report."
        )

        view = getattr(self, "_btn_view_reports", None)
        if view is None:
            return
        replicates = self._replicate_report_paths(project)
        ## Both kinds must exist: the button's job is to lay the pooled
        ## report beside the replicates it pools, and opening one of those
        ## halves alone is what the existing per-replicate table already does.
        ready = exists and bool(replicates)
        view.setEnabled(ready)
        if ready:
            view.setToolTip(
                f"Open the Project report and {len(replicates)} replicate "
                "report(s), each in its own PDF viewer window.")
        elif not exists and not replicates:
            view.setToolTip("No reports yet — run Create report first.")
        elif not exists:
            view.setToolTip(
                "No Project report yet — run Create report first "
                f"({len(replicates)} replicate report(s) already exist).")
        else:
            view.setToolTip(
                "No per-replicate reports yet. Create report writes one per "
                "replicate unless that step was turned off.")

    def _enclosing_project_dir(self, path):
        """The Project *path* is a replicate of, or None when *path* is not a
        replicate inside one.

        The parent must be a Project in the ADR-0011 sense — a marker plus a
        replicate the run can use. A bare ``project.yaml`` one level up used
        to capture the selection, so choosing a grouping folder inside a batch
        whose root carried a stray marker silently retargeted the whole
        selection to that root.
        """
        from .. import batch as batch_mod
        from .. import project as prj

        candidate = Path(path)
        if prj.is_project_dir(candidate):
            return None
        parent = candidate.parent
        kind, _experiments = batch_mod.project_kind(parent)
        return parent if kind == "project" else None

    def _experiment_dir(self):
        """Directory of the loaded experiment — the subject of every
        experiment-level action. None when nothing is loaded."""
        if self._exp is None:
            return None
        directory = getattr(self._exp, "project_directory", None)
        return Path(directory) if directory else None

    def _open_selected_replicate(self, item) -> None:
        from .. import project as prj

        name_item = self._exp_table.item(item.row(), 0)
        root = self._project_root()
        if name_item is None or root is None:
            return
        name = name_item.text()
        target = Path(root) / name
        ## A row without a config is a folder, not a replicate: there is
        ## nothing to load yet, so offer the config it is missing instead and
        ## stop there — region treatments still have to be assigned.
        if not prj.is_experiment_dir(target):
            resp = QMessageBox.question(
                self, "Create config",
                f"'{name}' has no {_CANONICAL_CONFIG} yet.\n\nCreate one from "
                "the project design and open it?")
            if resp != QMessageBox.StandardButton.Yes:
                return
            if self._create_replicate_config(name) is None:
                return
            ## The dialog promises to OPEN the new config, not just write it —
            ## without this the user was left hunting for the file by hand.
            self._launch_subapp("config", directory=str(target))
            ## The row now has a config: re-read the set rather than moving the
            ## selection, which stays on the Project.
            self._refresh_project_view()
            self._refresh_tiles()
            return
        ## The table is the only way to load an experiment (ADR-0008). The
        ## selection stays on the Project — only the loaded experiment moves.
        ## An analyzed replicate loads as it is — no QC re-run, no QC viewer
        ## — and its Experiment group opens, where Run QC / Run Analysis wait
        ## for anyone who wants them redone (user feedback 2026-08-29). An
        ## unanalyzed one still gets Load + QC, the QC viewer, and Analyze.
        analyzed = self._replicate_is_analyzed(name)
        if self._load_experiment(target, run_qc=not analyzed,
                                 reveal="group" if analyzed else "analyze"):
            self._close_panel()

    def _replicate_is_analyzed(self, name: str) -> bool:
        """Whether *name* has a saved analysis (the table's own cheap
        status — no data load). False when it cannot be told."""
        project = self._current_project()
        if project is None:
            return False
        try:
            return bool(project.experiment_status(name)["analyzed"])
        except Exception:  # noqa: BLE001
            return False

    def _create_replicate_config(self, name: str):
        """Scaffold *name*'s tracking_config.yaml from the project design.

        Returns the written path, or None when it failed (reported to the
        user). Shared by the table's double-click, Create experiment,
        Initialize existing directory, and the Experiment configs dialog.
        """
        project = self._current_project()
        if project is None:
            return None
        try:
            path = project.scaffold_replicate(name)
        except Exception as err:  # noqa: BLE001
            self._warn(f"Could not create the config for '{name}': {err}")
            return None
        self._log.append_line(
            f"Created {path} from the project design. Assign region "
            "treatments in the Config Editor and put the DTrack export in "
            f"{name}/data/.")
        return path

    def _project_report(self) -> None:
        project = self._current_project()
        if project is None:
            return
        task_name = "Update report" if self._project_report_exists(project) \
            else "Create report"
        ## The report action rewrites every replicate's outputs before pooling.
        ## Drop the loaded replicate so the Hub cannot keep stale in-memory
        ## results after the files under that replicate have been regenerated.
        self._unload_experiment()

        def _do() -> str:
            failures = project.run_all()
            if failures:
                raise RuntimeError(
                    f"{len(failures)} replicate(s) failed: "
                    + "; ".join(failures))
            result = project.build_combined_analysis()
            path = project.create_report()
            return (
                f"{task_name} complete: ran {len(project.experiment_names)} "
                f"replicate analyses, wrote Combined Analysis "
                f"({len(result['written'])} files), and saved {path}."
            )

        self._spawn_task(task_name, _do)

    def _open_pdf(self, path: Path) -> bool:
        """Hand *path* to the desktop's PDF viewer. True when it was handed
        over; the viewer itself decides window vs tab.

        ``QDesktopServices`` covers all three desktops (ShellExecute, Launch
        Services, xdg-open). It can still fail on a Linux box with no
        xdg-utils and no registered handler, so each platform's own opener is
        tried before giving up.
        """
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            return True
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606 — the OS picks the app
            elif sys.platform == "darwin":
                ## -n: a new instance, so several files cannot collapse into
                ## one reused window.
                subprocess.Popen(["open", "-n", str(path)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(path)], close_fds=True)
        except Exception as err:  # noqa: BLE001
            self._log_issue(f"[reports] could not open {path.name}: {err}")
            return False
        return True

    def _project_view_reports(self) -> None:
        """Open the Project report and every per-replicate report at once."""
        project = self._current_project()
        if project is None:
            return
        targets = [self._project_report_path(project)]
        targets += self._replicate_report_paths(project)
        missing = [p for p in targets if not p.is_file()]
        if missing:
            ## Reports can be deleted between the refresh that enabled the
            ## button and the click on it.
            self._warn("These reports are no longer on disk:\n"
                       + "\n".join(str(p) for p in missing))
            targets = [p for p in targets if p.is_file()]
            self._refresh_project_view()
        opened = sum(1 for path in targets if self._open_pdf(path))
        if opened:
            self._log.append_line(
                f"[reports] opened {opened} report(s) in your PDF viewer.")

    def _project_ai_narrative(self) -> None:
        project = self._current_project()
        if project is None:
            return
        choice = self._choose_ai_provider("AI narrative")
        if choice is None:
            return

        def _do() -> str:
            project.generate_ai_summary(choice)
            path = project.create_report()
            return (f"AI narrative saved and report rebuilt: {path}. "
                    "It summarizes the pipeline's numbers; it performs no "
                    "analysis of its own.")

        self._spawn_task("AI narrative", _do)

    def _project_removed_regions(self, project_dir=None) -> None:
        """Open the removals window on a Project (ADR-0010).

        *project_dir* lets the Batch panel open it for a Project other than
        the selected one; without it the current selection is used.
        """
        from .removals_dialog import RemovalsDialog

        root = Path(project_dir) if project_dir else self._project_root()
        if root is None:
            self._warn("Select a Project first — removals are declared per "
                       "replicate, and a Project is what lists them.")
            return
        ## A Removal Sheet at the Project root is offered here — the explicit
        ## act ADR-0010 requires, at the moment the user is looking at exactly
        ## what it would change.
        if self._removal_sheet_note(root) is not None:
            self._apply_removal_sheet(root, "project")
        dialog = RemovalsDialog(str(root), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        changed = dialog.changed_experiments
        if not changed:
            self._log.append_line("[removals] nothing changed.")
            return
        for path in dialog.written:
            self._log.append_line(f"Saved: {path}")
        self._log.append_line(
            f"[removals] updated {len(changed)} replicate(s): "
            + ", ".join(changed)
            + ". Re-run the analysis for the change to reach saved results.")
        ## The loaded experiment's Arena was filtered at load, so re-apply now
        ## — otherwise a fly the user just removed keeps appearing in plots
        ## until the next full analysis (ADR-0010).
        exp = self._exp
        if exp is not None:
            loaded = os.path.normpath(str(exp.project_directory))
            if any(os.path.normpath(os.path.join(str(root), name)) == loaded
                   for name in changed):
                try:
                    exp.refresh_exclusions()
                    for line in exp.exclusion_notes():
                        self._log.append_line(line)
                except Exception as err:  # noqa: BLE001
                    self._log_issue(f"[removals] could not re-apply: {err}")
        self._refresh_project_view()
        self._refresh_tiles()

    def _apply_removal_sheet(self, root, label: str, members=None) -> None:
        """Apply a Removal Sheet found at *root* (a Batch or Project), after
        confirming — applying writes into every experiment it names.

        *members* limits it to those Member keys: at a Batch the checked rows,
        so unchecking a Project protects it here exactly as it does during a
        run (ADR-0011). Without it this button was the one writer the scoping
        rule did not reach.
        """
        from .. import batch as batch_mod
        from .. import removals as removals_mod

        sheet = removals_mod.find_sheet(str(root))
        if sheet is None:
            self._warn(
                f"No removal sheet in this {label}. Add a "
                f"{removals_mod.SHEET_STEM}.csv with project, experiment, "
                f"region and reason columns.")
            return
        scope = ("" if members is None else
                 f"\n\nOnly the {len(members)} checked project(s) are "
                 "touched — rows naming any other are skipped.")
        answer = QMessageBox.question(
            self, "Apply removal sheet",
            f"Apply {os.path.basename(sheet)} to the experiments it names?\n\n"
            "Declarations already in place are kept; a row whose reason "
            "differs is reported as a conflict, not overwritten." + scope)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = batch_mod.apply_removal_sheet(
                str(root), log=self._log.append_line, members=members)
            if result.get("error"):
                raise RuntimeError(result["error"])
        except Exception as err:  # noqa: BLE001
            self._log_issue(f"[removals] {os.path.basename(sheet)}: {err}")
            self._warn(f"The removal sheet could not be read:\n{err}")
            return
        conflicts = result["counts"].get("conflict", 0)
        if conflicts:
            self._warn(
                f"{conflicts} row(s) conflict with declarations already in "
                "place — the existing reasons were kept. See the log.")
        self._refresh_project_view()
        self._refresh_tiles()

    def _create_experiment(self) -> None:
        """The replicate does not exist at all: make its directory and
        scaffold its config from the project design.

        Everything the config needs — experiment type, factors, facets,
        quality criteria, counting regions — is already settled in
        project.yaml and inherited from it, so the only thing to ask for is
        the name."""
        from .. import project as prj

        project = self._current_project()
        if project is None:
            self._warn("Open a Project first — a replicate's design is "
                       "inherited from its project.yaml.")
            return
        name, ok = QInputDialog.getText(
            self, "Create experiment", "New experiment directory name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        ## Anchor on the Project, not the selected directory: with a replicate
        ## open, the latter nested the new replicate inside it.
        directory = Path(project.experiment_dir(name))
        if prj.is_experiment_dir(directory):
            self._warn(f"'{name}' already exists and has a config.")
            return
        if directory.exists():
            ## The other scenario, and it has its own button: initializing
            ## files what is already in there instead of assuming an empty
            ## directory.
            self._warn(
                f"'{name}' already exists.\n\nUse 'Initialize existing "
                "directory…' to give the directory you already have a config.")
            return
        if self._create_replicate_config(name) is None:
            return
        self._refresh_project_view()
        self._refresh_tiles()
        ## The scaffold is a starting point, not the config: its rig and its
        ## region treatments are still blank (or the first replicate's). Both
        ## ways of finishing it are one click away rather than left to be
        ## found.
        self._finish_new_experiment_config(project, name, directory)

    def _finish_new_experiment_config(self, project, name: str,
                                      directory: Path) -> None:
        """Offer the two ways to finish a just-created replicate's config:
        edit the scaffold, or replace it with one copied from elsewhere.

        Copying is the common case for the second and later replicates of a
        run — the same rig and the same region treatments as an experiment
        that already works. It is checked against the project design before
        it is written, and anything that would not conform sends the user to
        the editor instead, with the scaffold still in place."""
        box = QMessageBox(self)
        box.setWindowTitle("Create experiment")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"'{name}' is ready, with a {_CANONICAL_CONFIG} "
                    "scaffolded from the project design.")
        box.setInformativeText(
            "Edit it now, or replace it with a config copied from an "
            "experiment that is already set up.")
        edit_btn = box.addButton("Edit config…",
                                 QMessageBox.ButtonRole.AcceptRole)
        copy_btn = box.addButton("Copy config from…",
                                 QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(edit_btn)
        box.exec()
        if box.clickedButton() is copy_btn and self._copy_replicate_config(
                project, name, directory):
            return
        ## Either they chose to edit, or the copy did not happen — and a
        ## replicate left on its scaffold is one nobody has told which rig it
        ## ran on.
        self._launch_subapp("config", directory=str(directory))

    def _copy_replicate_config(self, project, name: str,
                               directory: Path) -> bool:
        """Replace *name*'s scaffolded config with one chosen from elsewhere.

        Returns True when the copy was made. False means nothing was written
        — the user cancelled, or the chosen file would not be a conforming
        replicate of this Project — and the caller opens the editor on the
        scaffold that is still there."""
        import yaml as _yaml

        chosen, _ = QFileDialog.getOpenFileName(
            self, f"Choose a {_CANONICAL_CONFIG} to copy into '{name}'",
            str(project.project_directory),
            f"Tracking config ({_CANONICAL_CONFIG});;"
            "YAML files (*.yaml *.yml);;All files (*)")
        if not chosen:
            QMessageBox.information(
                self, "Copy config",
                "No file chosen — opening the scaffolded config in the "
                "Config Editor instead.")
            return False
        source = Path(chosen)
        target = directory / _CANONICAL_CONFIG
        if source.resolve() == target.resolve():
            self._warn(f"That is '{name}'s own config.\n\nOpening it in the "
                       "Config Editor instead.")
            return False
        try:
            with open(source, encoding="utf-8") as handle:
                config = _yaml.safe_load(handle)
        except Exception as err:  # noqa: BLE001
            self._warn(f"'{source.name}' could not be read:\n{err}\n\n"
                       "Opening the scaffolded config in the Config Editor "
                       "instead.")
            return False
        ## Checked BEFORE it is written: a non-conforming replicate makes the
        ## whole Project refuse to load, and the user would have to find and
        ## undo the copy by hand.
        problems = project.design_problems_for(config, source.name)
        if problems:
            self._warn(
                f"'{source.name}' does not fit this Project's design:\n  - "
                + "\n  - ".join(problems[:6])
                + ("\n  - …" if len(problems) > 6 else "")
                + "\n\nNothing was copied. Opening the scaffolded config in "
                "the Config Editor instead.")
            return False
        try:
            shutil.copyfile(source, target)
        except Exception as err:  # noqa: BLE001
            self._warn(f"Could not copy '{source.name}':\n{err}")
            return False
        self._log.append_line(f"Copied {source} to {target}")
        ## Conforming is not the same as complete — a copied config can still
        ## be missing its region treatments. Say so rather than let the
        ## replicate fail at run time.
        from .. import config_validation
        remaining = config_validation.validate_config(config)
        if remaining:
            self._log.append_line(
                f"[config] {name}: {len(remaining)} thing(s) still to fix — "
                + "; ".join(remaining[:3]))
        self._refresh_project_view()
        self._refresh_tiles()
        return True

    def _project_initialize_experiment(self) -> None:
        """The replicate's directory exists but its config does not: file
        whatever is loose in it, scaffold the config, and open the editor.

        Filing first, config second: the Config Editor's own view of the
        experiment (and the QC that follows a load) reads ``data/``, so a
        recording still sitting at the root would make the freshly configured
        replicate look empty."""
        from .. import layout as layout_mod

        project = self._current_project()
        if project is None:
            self._warn("Open a Project first — a replicate's design is "
                       "inherited from its project.yaml.")
            return
        candidates = layout_mod.initializable_dirs(project.project_directory)
        if not candidates:
            self._warn(
                f"Every directory in '{project.name}' already has a "
                f"{_CANONICAL_CONFIG}.\n\nUse 'Create experiment…' to make a "
                "new replicate.")
            return
        labels = [f"{item.name}  —  {item.status or 'empty'}"
                  for item in candidates]
        choice, ok = QInputDialog.getItem(
            self, "Initialize existing directory",
            "Directory to make a replicate of "
            f"'{project.name}':", labels, 0, False)
        if not ok or not choice:
            return
        item = candidates[labels.index(choice)]

        ## Re-classify rather than trusting the listing: the picker was built
        ## before the user had a chance to change anything on disk.
        state = layout_mod.classify(item.directory)
        if state.status in (layout_mod.AMBIGUOUS, layout_mod.UNREADABLE):
            self._warn(f"'{state.name}': {state.detail or state.status}\n\n"
                       "This one has to be sorted out by hand.")
            return
        if state.status == layout_mod.UNFILED:
            plan = layout_mod.file_recording(item.directory,
                                             log=self._log.append_line)
            if plan.refused:
                ## A locked workbook or a symlink: stop before writing the
                ## config, so a retry after the fix still does the whole job.
                self._warn(f"Could not file '{state.name}': {plan.refused}")
                return
            self._log.append_line(f"[file] {state.name}: {plan.describe()}")
            for skipped_name, why in plan.skipped:
                self._log.append_line(
                    f"[file] {state.name}: {skipped_name} skipped — {why}")

        if self._create_replicate_config(item.name) is None:
            return
        ## The point of this button is the editor: a scaffolded config still
        ## needs its rig and its region treatments before the replicate runs.
        self._launch_subapp("config", directory=str(item.directory))
        self._refresh_project_view()
        self._refresh_tiles()

    def _project_experiment_configs(self) -> None:
        """Open the per-experiment config manager for the current Project."""
        project = self._current_project()
        if project is None:
            return
        ExperimentConfigsDialog(self, project).exec()
        self._refresh_project_view()

    def _project_run_script(self) -> None:
        from ..script_editor.project_actions import (
            STANDARD_PIPELINE,
            preflight_project_script_issues,
            report_pipeline_for,
            run_project_script,
        )

        project = self._current_project()
        if project is None:
            return
        data = self._project_script_combo.currentData()
        note = None
        if data == ("builtin", "report"):
            script, note = report_pipeline_for(project)
        elif data is None or data == ("builtin", "standard"):
            script = STANDARD_PIPELINE
        else:
            script = project.find_script(str(data))
            if script is None:
                self._warn(f"No project script named '{data}'.")
                return
        ## Project-in-hand pre-check the static validator cannot do: unknown
        ## only: replicate names and scripts that resolve nowhere abort here,
        ## so a typo never reaches a run (grill 2026-08).
        issues = preflight_project_script_issues(script, project)
        if issues:
            self._warn("Script pre-check failed:\n" + "\n".join(issues))
            return
        if note:
            self._log.append_line(f"[report pipeline] {note}")

        def _do() -> str:
            figures: list = []
            run_project_script(
                script, project, log_cb=print,
                figure_cb=lambda title, fig: figures.append(title))
            msg = f"Project script '{script.get('name')}' complete."
            if figures:
                msg += (f" ({len(figures)} figure(s) were produced by "
                        "replicate steps; open a replicate to view plots.)")
            return msg

        self._spawn_task(f"Project script: {script.get('name')}", _do)

    def _project_edit_scripts(self) -> None:
        from .. import project as prj
        from ..script_editor.window import ScriptEditorWindow

        root = self._project_root()
        if root is None:
            return
        window = ScriptEditorWindow(Path(root) / prj.PROJECT_FILENAME,
                                    parent=self)
        window.scriptsSaved.connect(
            lambda _path: self._refresh_project_view())
        window.show()
        self._script_editor_win = window   # keep a reference alive

    def _new_project(self) -> None:
        """Open the Create/Edit Project dialog (the project.yaml editor)."""
        start = str(self._project_dir) if self._project_dir else os.getcwd()
        dialog = ProjectInfoDialog(self, start_dir=start)
        if dialog.exec() and dialog.saved_dir:
            self._set_project_dir(dialog.saved_dir)

    def _initialize_existing_directory(self) -> None:
        """Promote a directory that already exists — often with experiment
        subdirectories in it — into a Project, keeping its own name.

        The third way in: Open Project wants a project.yaml already there,
        Create project makes the directory too."""
        from .. import project as prj

        ## Prefilling a directory that is already a Project would only earn
        ## the dialog's "that one is already a Project" refusal, so offer the
        ## selection only when it is the kind of directory this button takes.
        start = ""
        if self._project_dir and not prj.is_project_dir(self._project_dir):
            start = str(self._project_dir)
        dialog = ProjectInfoDialog(self, start_dir=start,
                                   initialize_existing=True)
        if dialog.exec() and dialog.saved_dir:
            self._log.append_line(
                f"Initialized {Path(dialog.saved_dir) / prj.PROJECT_FILENAME}")
            self._set_project_dir(dialog.saved_dir)

    # ---------------- Batch card ----------------

    def _build_batch_card(self) -> None:
        """The Batch panel (ADR-0009, ADR-0011): a Batch is a directory with
        Projects anywhere beneath it, found by a walk that prunes at each one.
        A Batch Run executes one designated Project Script in every checked
        Project — there is no third script level, and a Batch never pools
        results across Projects."""
        ## LOAD-blue, like the Batch tile (whose title and glyph are LOAD-
        ## blue): the card's title wears the same color as the tile it
        ## opens under (user feedback 2026-08-29).
        card = Card(
            "Batch",
            category=Category.LOAD,
            subtitle="Run a Project Script in every Project of this folder.",
            icon_name="batch",
            tinted_title=True,
        )
        card.add_title_widget(
            HelpButton("batch_run", tooltip="Batch Runs over many Projects")
        )
        ## The panel's own way in: choosing the parent directory here
        ## auto-loads every Project inside it into the table below. Never
        ## gated — it IS the fix for the empty state.
        pick_row = QHBoxLayout()
        btn_pick_batch = ActionButton("Choose batch folder…", Category.LOAD,
                                      icon_name="browse")
        btn_pick_batch.setToolTip(
            "Pick the folder that holds your Projects. They are found "
            "recursively, so they can sit at any depth inside it — every one "
            "found is listed below for the run.")
        btn_pick_batch.clicked.connect(self._pick_batch_dir)
        pick_row.addWidget(btn_pick_batch)
        self._btn_batch_rescan = ActionButton(
            "Rescan folder", Category.TOOLS, icon_name="refresh")
        self._btn_batch_rescan.setToolTip(
            "Walk the batch folder again. The project list is read once when "
            "the folder is selected; rescan after adding or fixing projects "
            "outside the app.")
        self._btn_batch_rescan.setSizePolicy(QSizePolicy.Policy.Fixed,
                                             QSizePolicy.Policy.Fixed)
        self._btn_batch_rescan.clicked.connect(self._rescan_batch)
        pick_row.addWidget(self._btn_batch_rescan)
        pick_row.addStretch(1)
        ## Right of the folder picker on the same row, and deliberately small:
        ## a secondary action on the folder you just chose, not a peer of Run
        ## batch. Enabled only when that folder actually holds a sheet.
        self._btn_batch_removals = ActionButton(
            "Apply removal sheet…", Category.TOOLS, icon_name="clear")
        self._btn_batch_removals.setToolTip(
            "Read removed_regions.csv at the batch folder and write its rows "
            "into each experiment's removed_regions.yaml. A Batch Run applies "
            "it automatically before running; this is for applying it now. "
            "Declarations already in place are kept.")
        self._btn_batch_removals.setSizePolicy(QSizePolicy.Policy.Fixed,
                                               QSizePolicy.Policy.Fixed)
        font = self._btn_batch_removals.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        self._btn_batch_removals.setFont(font)
        self._btn_batch_removals.setStyleSheet(
            self._btn_batch_removals.styleSheet().replace(
                "padding: 6px 12px;", "padding: 3px 8px;"))
        self._btn_batch_removals.setIconSize(QSize(13, 13))
        self._btn_batch_removals.clicked.connect(
            lambda: self._apply_removal_sheet(
                self._batch_view_root(), "batch folder",
                members=self._batch_checked_names()))
        pick_row.addWidget(self._btn_batch_removals, 0,
                           Qt.AlignmentFlag.AlignRight)
        card.add_body(pick_row)
        self._batch_empty = QLabel(
            "Choose a batch folder — one with Projects anywhere inside it — "
            "and every Project found is listed here for the run. A Project is "
            "a folder with a project.yaml and at least one experiment "
            "directory.")
        self._batch_empty.setStyleSheet(
            "color: palette(mid); font-style: italic;")
        self._batch_empty.setWordWrap(True)
        card.add_body(self._batch_empty)

        self._batch_table = QTableWidget(0, 4)
        self._batch_table.setHorizontalHeaderLabels(
            ["Project", "Replicates", "Report", "Status"])
        self._batch_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._batch_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self._batch_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._batch_table.setColumnWidth(
            0, self._BATCH_PROJECT_COLUMN_MAX_WIDTH)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._batch_table.verticalHeader().setVisible(False)
        self._batch_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._batch_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._batch_table.setMaximumHeight(170)
        self._batch_table.setToolTip(
            "Checked Projects join the next Batch Run. Projects are found "
            "recursively, so a row's name is its path inside the batch "
            "folder. Double-click a row to open that Project; right-click "
            "for its blocked experiments and removed regions.")
        self._batch_table.itemDoubleClicked.connect(
            self._open_selected_batch_project)
        ## Right-click, not double-click: double-click already means "open
        ## this Project" (ADR-0009), so the removals window takes the gesture
        ## that is free.
        self._batch_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._batch_table.customContextMenuRequested.connect(
            self._batch_table_menu)
        card.add_body(self._batch_table)
        hint = QLabel("Double-click a project to open its Project panel. "
                      "Right-click to fix a blocked experiment or edit its "
                      "removed regions.")
        hint.setStyleSheet("color: palette(mid); font-style: italic;")
        hint.setWordWrap(True)
        card.add_body(hint)

        script_row = QHBoxLayout()
        script_row.addWidget(QLabel("Script:"))
        self._batch_script_combo = QComboBox()
        self._batch_script_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._batch_script_combo.setMinimumContentsLength(12)
        self._batch_script_combo.setSizePolicy(QSizePolicy.Policy.Ignored,
                                               QSizePolicy.Policy.Fixed)
        self._batch_script_combo.setToolTip(
            "The designated Project Script — resolved per Project from "
            "batch.yaml project_scripts, then the project's own scripts, "
            "then the built-ins. The default runs each project's own "
            "'batch' script — every project.yaml is created with one; a "
            "project with none is reported and skipped. Changing it is "
            "remembered in batch.yaml.")
        self._batch_script_combo.currentIndexChanged.connect(
            self._on_batch_script_changed)
        script_row.addWidget(self._batch_script_combo, 1)
        self._btn_run_batch = ActionButton("Run batch", Category.ANALYZE,
                                           icon_name="run")
        self._btn_run_batch.setToolTip(
            "Run the designated Project Script in every checked Project — "
            "continue-on-error, per-Project summary at the end. Unloads the "
            "loaded experiment first.")
        ## Through a lambda: clicked() would otherwise pass its bool into
        ## the focus argument.
        self._btn_run_batch.clicked.connect(lambda: self._run_batch())
        script_row.addWidget(self._btn_run_batch)
        card.add_body(script_row)

        ## A Batch Run touches every replicate of every Project, so the
        ## artifact/figure tabs it would open run into the hundreds and bury
        ## the Output tab the user is actually reading. Checked, new tabs stop
        ## being created; Output and Errors keep streaming.
        self._chk_batch_narrative = QCheckBox("AI narrative of the batch")
        self._chk_batch_narrative.setToolTip(
            "After the run, ask an AI provider to synthesize the Projects' "
            "own narratives into batch_ai_narrative.md at the batch folder — "
            "results across the batch, design problems, and Projects that "
            "lost a lot of flies. A Project with no narrative gets one "
            "generated first (one extra provider call each), because the "
            "default 'batch' script rebuilds Combined Analysis, which "
            "deletes it.")
        card.add_body(self._chk_batch_narrative)

        self._chk_suppress_tabs = QCheckBox("Suppress new plot / output tabs")
        self._chk_suppress_tabs.setToolTip(
            "Stop opening a tab for every figure and saved file. The Output "
            "and Errors tabs keep updating, and every artifact is still "
            "written to disk — only the tabs are skipped. Applies to all "
            "runs while it is checked, not just Batch Runs.")
        ## On by default: a long run's tabs pile up faster than anyone reads
        ## them, and every artifact is on disk regardless. Uncheck to watch
        ## figures appear as they are made.
        self._chk_suppress_tabs.setChecked(True)
        card.add_body(self._chk_suppress_tabs)

        for w in (self._batch_table, self._batch_script_combo,
                  self._btn_run_batch, self._chk_batch_narrative,
                  self._btn_batch_removals, self._btn_batch_rescan):
            w.setEnabled(False)
        self._cards["batch"] = card
        self._cards_lay.addWidget(card)

    def _removal_sheet_note(self, root) -> str | None:
        """Log the Removal Sheet at *root* the first time it is seen, and
        return its path — the report half of "report, never apply"."""
        from .. import removals as removals_mod

        if root is None:
            return None
        sheet = removals_mod.find_sheet(str(root))
        if sheet is None:
            return None
        if getattr(self, "_noted_removal_sheet", None) != sheet:
            self._noted_removal_sheet = sheet
            try:
                rows = len(removals_mod.read_sheet(sheet))
                self._log.append_line(
                    f"[removals] {os.path.basename(sheet)} found: {rows} row(s). "
                    f"'Apply removal sheet…' writes them into the experiments; "
                    f"a Batch Run applies it automatically.")
            except Exception as err:  # noqa: BLE001
                self._log_issue(
                    f"[removals] {os.path.basename(sheet)} could not be read: {err}")
        return sheet

    def _rescan_batch(self) -> None:
        """Walk the batch folder again (ADR-0011's Rescan)."""
        root = self._batch_view_root()
        if root is None:
            return
        found = self._scan_batch(root, refresh=True)
        blocked = sum(len(m.blocked) for m in found["members"])
        self._log.append_line(
            f"[batch] rescanned {root}: {len(found['members'])} project(s)"
            + (f", {blocked} blocked experiment(s)" if blocked else ""))
        for key, why in found["skipped"]:
            self._log_issue(f"[batch] {key} skipped — {why}")
        self._refresh_batch_view()
        self._refresh_tiles()

    def _batch_member(self, key):
        """The scanned Member for *key*, or None if it has gone."""
        for member in self._batch_members():
            if member.key == key:
                return member
        return None

    def _batch_table_menu(self, point) -> None:
        """Right-click on a project row: fix its blocked experiments
        (ADR-0011), or open its removals window (ADR-0010).

        Both live here because the other two gestures are taken: double-click
        selects the Project (ADR-0009) and a check runs it.
        """
        root = self._batch_view_root()
        if root is None:
            return
        item = self._batch_table.itemAt(point)
        if item is None:
            return
        name_item = self._batch_table.item(item.row(), 0)
        if name_item is None:
            return
        key = name_item.text().strip()
        member = self._batch_member(key)
        menu = QMenu(self)
        fix = menu.addAction(f"Fix blocked experiments in {key}…")
        fix.setEnabled(bool(member is not None and member.blocked))
        if member is not None and member.blocked:
            fix.setToolTip("\n".join(e.describe() for e in member.blocked))
        removals_action = menu.addAction(f"Removed regions in {key}…")
        chosen = menu.exec(self._batch_table.viewport().mapToGlobal(point))
        if chosen is removals_action:
            from .. import batch as batch_mod

            self._project_removed_regions(
                Path(batch_mod.member_directory(root, key)))
        elif chosen is fix:
            ## The same review window, so its Run button means the same thing
            ## it does everywhere else — opened from here it was inert.
            self._run_batch(focus=key)

    def _pick_batch_dir(self) -> None:
        """The Batch panel's own way in: pick the parent directory, and every
        Project inside it auto-loads into the projects table (the ordinary
        selection machinery does the rest)."""
        from .. import project as prj

        start = str(self._project_dir) if self._project_dir else os.getcwd()
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose batch directory (a folder of Projects)", start)
        if not chosen:
            return
        p = Path(chosen).expanduser().resolve()
        self._set_project_dir(p)
        if self._batch_root() is not None:
            return
        ## Not a Batch after all — say why, about the folder the user picked
        ## rather than the one the selection normalized to, and using what the
        ## walk actually saw. "No Project subdirectories" is the wrong answer
        ## now that the search is recursive.
        found = self._scan_batch(p)
        if prj.is_project_dir(p) and not found["skipped"]:
            self._log_issue(
                f"[batch] {p.name} is a single Project — to batch it, "
                "choose a folder that contains it.")
        else:
            self._log_issue(
                f"[batch] no Project found anywhere in {p} — a Project is a "
                "folder with a project.yaml and at least one experiment "
                "directory inside it.")
            for key, why in found["skipped"][:10]:
                self._log_issue(f"[batch]   {key} — {why}")
            if found.get("truncated"):
                self._log_issue(
                    "[batch]   the scan stopped early: this folder is larger "
                    "than a batch should be. Choose one closer to the "
                    "projects.")

    def _scan_batch(self, path, refresh: bool = False) -> dict:
        """The recursive walk of *path*, cached (ADR-0011).

        Discovery is recursive now, so the walk is far more expensive than the
        single ``listdir`` it replaced — and ``_refresh_tiles`` runs on every
        checkbox toggle and every finished task. One walk per selection is
        kept and reused; :meth:`_invalidate_batch_scan` drops it whenever the
        tree may have changed underneath (a new selection, a filed recording,
        a finished run), and the Rescan button covers changes made outside the
        app entirely.
        """
        from .. import batch as batch_mod

        key = str(path)
        cached = getattr(self, "_batch_scan_cache", None)
        if refresh or cached is None or cached[0] != key:
            cached = (key, batch_mod.discover(key))
            self._batch_scan_cache = cached
        return cached[1]

    def _invalidate_batch_scan(self) -> None:
        self._batch_scan_cache = None

    def _batch_root(self):
        """The selected Batch, or None when the selection is not one. The
        selection names exactly one working container — a Batch or a Project
        (ADR-0009)."""
        if not self._project_dir:
            return None
        ## Gated on the walk alone. An earlier short-circuit here asked
        ## is_project_dir, which disagreed with the walk's stricter test: a
        ## batch root carrying a stray or legacy project.yaml (every flat
        ## batch someone once clicked "Edit config" on has one) enumerated
        ## its Members fine and showed an empty, dead Batch card. The walk
        ## already refuses to call a real Project a Batch.
        found = self._scan_batch(self._project_dir)
        if found["members"]:
            root = Path(self._project_dir)
            self._batch_panel_root = root
            return root
        return None

    def _batch_view_root(self):
        """Batch whose projects table is currently being shown.

        The app selection still names exactly one container, but the Batch
        panel is allowed to stay open while a row double-click selects one of
        its Projects. In that state the panel keeps displaying the Batch it
        came from instead of rebuilding itself as an empty Batch card.
        """
        root = self._batch_root()
        if root is not None:
            return root
        remembered = getattr(self, "_batch_panel_root", None)
        if remembered is None or self._open_panel_key != "batch":
            return None
        found = self._scan_batch(remembered)
        if found["members"]:
            return Path(remembered)
        self._batch_panel_root = None
        return None

    def _batch_members(self) -> list:
        root = self._batch_view_root()
        return self._scan_batch(root)["members"] if root is not None else []

    def _refresh_batch_view(self) -> None:
        from .. import batch as batch_mod
        from ..script_editor.project_actions import (
            REPORT_PIPELINE,
            STANDARD_PIPELINE,
        )

        card = self._cards.get("batch")
        if card is None:
            return
        root = self._batch_view_root()
        live = root is not None
        self._batch_empty.setVisible(not live)
        ## The suppress-tabs box is deliberately NOT in this list: it applies
        ## to every run, not only Batch Runs, so it stays usable with no
        ## Batch selected.
        for w in (self._batch_table, self._batch_script_combo,
                  self._btn_run_batch, self._chk_batch_narrative,
                  self._btn_batch_rescan):
            w.setEnabled(live)
        ## Selecting a Batch REPORTS its Removal Sheet; it never applies one.
        ## Browsing to a colleague's batch folder must not silently rewrite
        ## eighty experiment directories (ADR-0010).
        self._btn_batch_removals.setEnabled(
            live and self._removal_sheet_note(root) is not None)
        if not live:
            self._batch_table.setRowCount(0)
            self._batch_script_combo.blockSignals(True)
            self._batch_script_combo.clear()
            self._batch_script_combo.blockSignals(False)
            return

        found = self._scan_batch(root)
        members = found["members"]
        if found.get("truncated") and getattr(self, "_noted_truncation", None) != str(root):
            ## Once per selection: a partial scan reported as a complete one
            ## is how an unattended run silently skips half a batch.
            self._noted_truncation = str(root)
            self._log_issue(
                f"[batch] the scan of {root} stopped early — this folder is "
                "larger than a batch should be, and projects deeper in it "
                "were not found. Choose a folder closer to the projects.")
        meta = batch_mod.load_batch_file(root)

        ## Rebuilding must not silently re-check a Project the user
        ## unchecked; a new row defaults to checked unless nothing in it can
        ## run, which can only produce a failure (ADR-0011).
        prev: dict[str, Qt.CheckState] = {}
        for row in range(self._batch_table.rowCount()):
            item = self._batch_table.item(row, 0)
            if item is not None:
                prev[item.text()] = item.checkState()
        self._batch_table.setRowCount(0)
        for member in members:
            row = self._batch_table.rowCount()
            self._batch_table.insertRow(row)
            item = QTableWidgetItem(member.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            default = (Qt.CheckState.Checked if member.runnable
                       else Qt.CheckState.Unchecked)
            item.setCheckState(prev.get(member.key, default))
            item.setToolTip(member.key)
            self._batch_table.setItem(row, 0, item)
            reps = QTableWidgetItem(
                f"{len(member.usable)}/{len(member.experiments)}")
            report = QTableWidgetItem("yes" if member.has_report else "no")
            blocked = member.blocked
            status = QTableWidgetItem(
                f"{len(blocked)} blocked" if blocked else "ok")
            self._batch_table.setItem(row, 1, reps)
            self._batch_table.setItem(row, 2, report)
            self._batch_table.setItem(row, 3, status)
            if blocked:
                ## Red, and the reasons in the tooltip: the strip has no room
                ## for them and the preflight is a click away (ADR-0011).
                brush = QBrush(blocked_color())
                detail = "\n".join(e.describe() for e in blocked)
                for column in range(self._batch_table.columnCount()):
                    cell = self._batch_table.item(row, column)
                    if cell is not None:
                        cell.setForeground(brush)
                        cell.setToolTip(
                            f"{member.key}\n\n{detail}"
                            if column == 0 else detail)

        ## Picker: built-ins plus batch.yaml's central Project Scripts. The
        ## designation may also name a script each project.yaml defines, so
        ## an unlisted designation gets an entry rather than vanishing.
        combo = self._batch_script_combo
        combo.blockSignals(True)
        combo.clear()
        ## No designation now means "each Project's own default script"
        ## (ADR-0009 amendment) — the built-ins below are explicit choices,
        ## not the silent fallback they used to be.
        combo.addItem("Each project's own 'batch' script (default)",
                      ("default", None))
        combo.addItem("Report pipeline (built-in)", ("builtin", "report"))
        combo.addItem("Standard pipeline (built-in)", ("builtin", "standard"))
        for script in meta["project_scripts"]:
            script_name = str(script.get("name"))
            combo.addItem(f"{script_name} (batch.yaml)",
                          ("name", script_name))
        want = meta["script"]
        index = 0
        if want == REPORT_PIPELINE["name"]:
            index = 1
        elif want == STANDARD_PIPELINE["name"]:
            index = 2
        elif want:
            index = next((i for i in range(combo.count())
                          if combo.itemData(i) == ("name", want)), -1)
            if index < 0:
                combo.addItem(f"{want} (from each project)", ("name", want))
                index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _batch_checked_names(self) -> list[str]:
        names = []
        for row in range(self._batch_table.rowCount()):
            item = self._batch_table.item(row, 0)
            if item is not None \
                    and item.checkState() == Qt.CheckState.Checked:
                names.append(item.text())
        return names

    def _on_batch_script_changed(self, _index: int) -> None:
        from .. import batch as batch_mod
        from ..script_editor.project_actions import (
            REPORT_PIPELINE,
            STANDARD_PIPELINE,
        )

        root = self._batch_view_root()
        if root is None:
            return
        data = self._batch_script_combo.currentData()
        if data is None:
            return
        ## The default ("each project's own script") is stored as "no
        ## designation": it never creates batch.yaml — the lazy-marker rule.
        if data == ("default", None):
            name = None
        elif data == ("builtin", "report"):
            name = REPORT_PIPELINE["name"]
        elif data == ("builtin", "standard"):
            name = STANDARD_PIPELINE["name"]
        else:
            name = data[1]
        try:
            batch_mod.save_batch_designation(root, name)
        except Exception as err:  # noqa: BLE001
            self._log_issue(f"[batch] could not save the designation: {err}")

    def _open_selected_batch_project(self, item) -> None:
        """Enter a Batch member Project and switch the visible work panel."""
        from .. import batch as batch_mod

        root = self._batch_view_root()
        name_item = self._batch_table.item(item.row(), 0)
        if root is None or name_item is None:
            return
        ## The row's text is the Member key — a path relative to the batch
        ## root, which may hold separators (ADR-0011).
        project_dir = Path(batch_mod.member_directory(root, name_item.text()))
        self._set_project_dir(project_dir)
        self._open_panel("project")

    def _run_batch(self, focus=None) -> None:
        from .. import batch as batch_mod
        from ..script_editor.project_actions import (
            REPORT_PIPELINE,
            STANDARD_PIPELINE,
        )

        root = self._batch_view_root()
        if root is None:
            return
        ## The preflight is where the target list is confirmed and blocked
        ## experiments are repaired (ADR-0011). Always shown: with recursive
        ## discovery the folder you picked no longer says what will run.
        confirmed = self._open_batch_preflight(root, focus=focus)
        if confirmed is None:
            return
        checked, apply_removals = confirmed
        if not checked:
            self._warn("No Projects checked — check at least one row.")
            return
        data = self._batch_script_combo.currentData()
        if data == ("builtin", "standard"):
            name = STANDARD_PIPELINE["name"]
        elif data == ("builtin", "report"):
            name = REPORT_PIPELINE["name"]
        elif data is None or data == ("default", None):
            name = None
        else:
            name = data[1]

        ## The provider is chosen BEFORE the run: the narrative is written
        ## from the worker thread, which cannot raise a dialog, and finding
        ## out there is no API key after an hour of analysis is no use.
        provider = None
        if self._chk_batch_narrative.isChecked():
            provider = self._choose_ai_provider("Batch AI narrative")
            if provider is None:
                return

        ## A Batch Run rewrites every replicate's analysis in every Project —
        ## a loaded experiment would survive as a stale copy of results that
        ## no longer exist (ADR-0008's rule, one level up).
        self._unload_experiment()

        def _do() -> str:
            results = batch_mod.run_batch(str(root), script_name=name,
                                          project_names=checked, log=print,
                                          apply_removals=apply_removals)
            ok = sum(1 for v in results.values() if v == "ok")
            failed = [f"{n}: {v.splitlines()[0] if v else '<no message>'}"
                      for n, v in results.items() if v != "ok"]
            msg = (f"Batch Run complete: {ok}/{len(results)} Project(s) "
                   "succeeded.")
            narrative = ""
            if provider is not None:
                ## Only the Projects that actually ran: summarizing one that
                ## just failed would describe stale numbers as fresh ones.
                ran = [n for n, v in results.items() if v == "ok"]
                narrative = self._batch_narrative_note(
                    batch_mod, root, provider, ran)
            if failed:
                raise RuntimeError(
                    msg + narrative + "\nFailed:\n  - " + "\n  - ".join(failed))
            return msg + narrative

        self._spawn_task("Batch Run", _do)

    def _open_batch_preflight(self, root, focus=None):
        """Show the preflight for *root*; returns ``(keys, apply_removals)``
        when the user chose to run, or None when they cancelled.

        Opened from Run batch and from the table's right-click fix entry —
        the same dialog either way, so there is one place that states what a
        Batch Run is about to do.
        """
        checked = self._batch_checked_names()
        dialog = BatchPreflightDialog(
            self, root, checked=checked, log=self._log.append_line)
        if focus is not None:
            dialog.focus_member(focus)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        ## Filing or scaffolding inside the dialog changes the tree, so the
        ## cached walk is stale either way.
        self._invalidate_batch_scan()
        self._refresh_batch_view()
        self._refresh_tiles()
        if not accepted:
            return None
        return dialog.selected_keys, dialog.apply_removals

    def _choose_ai_provider(self, title: str) -> str | None:
        """Ask which configured provider to use, or None when unavailable or
        cancelled (the caller then does nothing)."""
        from ..ai import available_providers

        providers = [p.provider_name for p in available_providers()]
        if not providers:
            self._warn("No AI provider key found (.env: ANTHROPIC_API_KEY "
                       "or OPENAI_API_KEY).")
            return None
        choice, ok = QInputDialog.getItem(
            self, title, "Provider:", providers, 0, False)
        return choice if ok else None

    def _batch_narrative_note(self, batch_mod, root, provider: str,
                              ran: list[str]) -> str:
        """Write the Batch narrative and return a line for the task message.

        ADR-0004: a provider failure never fails the work that preceded it.
        The Batch Run's own result stands either way.
        """
        if not ran:
            return "\nBatch AI narrative skipped — no Project succeeded."
        try:
            path = batch_mod.generate_batch_narrative(
                str(root), provider, project_names=ran, log=print)
        except Exception as err:  # noqa: BLE001
            return f"\nBatch AI narrative failed: {err}"
        return f"\nBatch AI narrative: {path}"

    # ==================================================================
    # Behaviour — project dir / config
    # ==================================================================

    # ---------------- Tile strip & panels (ADR-0007) ----------------

    def _toggle_panel(self, key: str) -> None:
        if self._open_panel_key == key:
            self._close_panel()
        else:
            self._open_panel(key)

    def _open_panel(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel is None:
            return
        self._close_panel()
        ## An experiment-level panel hangs from a sub-strip tile, so the
        ## sub-strip must be showing for the panel to have an anchor — the
        ## auto-open after a load (Analyze) reaches here with it collapsed.
        ## Choosing a container tile instead folds the sub-strip away: the
        ## expanded Experiment group counts as "the open thing", and one
        ## thing is open at a time (user feedback 2026-08-29).
        if key in self._EXPERIMENT_SUBTILES:
            self._expand_experiment()
        else:
            self._collapse_experiment()
        central = self.centralWidget()
        tile = self._tiles.get(key)
        if tile is not None:
            x = tile.mapTo(central, tile.rect().bottomLeft()).x()
            tile.set_active(True)
        else:
            x = central.width() - 8 - 440
        ## rect().bottom* coordinates are inclusive; +1 starts the panel just
        ## below the ribbon while preserving the caller-owned bottom margin.
        panel.open_at(x, self._ribbon_bottom() + 1, central.height() - 8)
        self._open_panel_key = key

    def _ribbon_bottom(self) -> int:
        """The y (central-widget coordinates) of the ribbon's last row: the
        strip, or the sub-strip while the Experiment tile is expanded. The
        sub-strip's height is fixed, so this is right even before Qt has
        laid out a sub-strip shown a moment ago."""
        central = self.centralWidget()
        bottom = self._strip.mapTo(central, self._strip.rect().bottomLeft()).y()
        if self._experiment_expanded:
            bottom += self._SUB_STRIP_HEIGHT
        return bottom

    def _reanchor_open_panel(self) -> None:
        """Re-open the open panel where its tile is now (window resize, the
        sub-strip expanding or collapsing under a container panel)."""
        key = getattr(self, "_open_panel_key", None)
        if key is not None:
            self._open_panel_key = None
            self._open_panel(key)

    # ---- The Experiment tile: a group, not a panel (ADR-0012) ----

    def _toggle_experiment(self, _key: str = "experiment") -> None:
        """The Experiment tile's click: expand or collapse its sub-strip.
        Inert with nothing loaded — the tile is un-clickable then, but the
        guard also covers a stale signal."""
        if self._exp is None:
            return
        if self._experiment_expanded:
            self._collapse_experiment()
        else:
            self._expand_experiment()

    def _expand_experiment(self) -> None:
        """Show the experiment-level tiles under the Experiment tile. An
        open Batch or Project panel closes first — the group and a container
        panel are never open together (user feedback 2026-08-29)."""
        if self._experiment_expanded:
            return
        if self._open_panel_key not in self._EXPERIMENT_SUBTILES:
            self._close_panel()
        self._experiment_expanded = True
        self._place_sub_strip()
        self._sub_strip.show()
        self._tiles["experiment"].set_active(True)
        self._settle_ribbon_layout()

    def _collapse_experiment(self) -> None:
        """Hide the experiment-level tiles; a panel hanging from one of them
        goes with them."""
        if not self._experiment_expanded:
            return
        if self._open_panel_key in self._EXPERIMENT_SUBTILES:
            self._close_panel()
        self._experiment_expanded = False
        self._sub_strip.hide()
        self._tiles["experiment"].set_active(False)
        self._settle_ribbon_layout()
        self._reanchor_open_panel()

    def _place_sub_strip(self) -> None:
        """Indent the sub-strip so its first tile starts under the Experiment
        tile's left edge."""
        lay = self._sub_strip.layout()
        margins = lay.contentsMargins()
        indent = max(10, self._tiles["experiment"].geometry().x())
        lay.setContentsMargins(indent, margins.top(), margins.right(),
                               margins.bottom())

    def _settle_ribbon_layout(self) -> None:
        """Lay the ribbon out NOW. Qt defers geometry for a widget shown a
        moment ago, and a panel anchored to a sub-strip tile needs that
        tile's real position, not the pending one."""
        for widget in (self.centralWidget(), self._ribbon.parentWidget(),
                       self._ribbon, self._sub_strip):
            lay = widget.layout() if widget is not None else None
            if lay is not None:
                lay.activate()

    def _close_panel(self) -> None:
        if self._open_panel_key is None:
            return
        panel = self._panels.get(self._open_panel_key)
        if panel is not None:
            panel.hide()
        tile = self._tiles.get(self._open_panel_key)
        if tile is not None:
            tile.set_active(False)
        self._open_panel_key = None

    def _handle_click_away(self, event) -> None:
        ## Click-away closes the open panel — and folds the sub-strip away,
        ## with or without a panel hanging from it (user feedback
        ## 2026-08-29). The click itself still lands.
        open_key = getattr(self, "_open_panel_key", None)
        expanded = getattr(self, "_experiment_expanded", False)
        if open_key is None and not expanded:
            return
        panel = self._panels.get(open_key) if open_key is not None else None
        if open_key is not None and (panel is None or not panel.isVisible()):
            return
        widget = QApplication.widgetAt(event.globalPosition().toPoint())
        if widget is not None and widget.window() is not self:
            return
        probe = widget
        while probe is not None:
            ## The ribbon covers both strips: a click on a tile (either row)
            ## is the tile's to decide.
            if probe is panel or probe is self._ribbon:
                return
            probe = probe.parentWidget()
        if self._open_panel_key == "batch":
            probe = widget
            while probe is not None:
                if probe is self._plot_dock:
                    break
                probe = probe.parentWidget()
            else:
                return
        self._close_panel()
        self._collapse_experiment()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        ## Keep the sub-strip under its tile and an open panel anchored when
        ## the window resizes.
        if getattr(self, "_experiment_expanded", False):
            self._place_sub_strip()
        self._reanchor_open_panel()

    def _refresh_tiles(self) -> None:
        """Live tile summaries — cheap sources only (no data loads).

        Runs inside Qt slots (task-finished, checkbox toggles), where an
        uncaught exception is escalated to qFatal by PyQt6 — so the whole
        refresh is best-effort: a summary that cannot be computed leaves the
        tile as it was, never kills the app.
        """
        try:
            self._refresh_tiles_inner()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._refresh_card_dimming()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_card_dimming(self) -> None:
        """Grey the cards whose actions have no subject yet.

        Same gating as the buttons inside them (and the tiles above them):
        everything experiment-level waits on a loaded replicate. The cards
        stay live — dimming says "nothing to act on", not "do not touch".
        """
        loaded = self._exp is not None
        for key, ready in (
            ("analyze", loaded),
            ("plots", loaded),
            ("scripts", loaded and bool(getattr(self, "_scripts", []))),
            ("ai", loaded and self._ai_available),
        ):
            card = self._cards.get(key)
            if card is not None:
                card.set_dimmed(not ready)

    def _loaded_experiment_name(self) -> str | None:
        """The loaded experiment as the replicates table names it — its
        directory — so the Experiment tile, the status readout, and the
        table agree (user feedback 2026-08-29). The recording's own name
        (the xlsx basename the arena carries, often the same rig name for
        every replicate) is only the fallback for an object without a
        directory. None when nothing is loaded."""
        if self._exp is None:
            return None
        directory = getattr(self._exp, "project_directory", None)
        if directory:
            return Path(directory).name
        return str(getattr(getattr(self._exp, "arena", None),
                           "experiment_name", "loaded"))

    def _loaded_experiment_line(self, short: bool = True) -> str | None:
        """The loaded experiment as one line — its name and headline counts,
        read from attributes computed at load time (never a fresh summarize).
        None when nothing is loaded. *short* abbreviates for a regular-width
        tile; the wide Experiment tile and the strip's status panel have room
        for whole words."""
        if self._exp is None:
            return None
        name = self._loaded_experiment_name()
        total, n_flagged = self._fly_counts()
        excluded = getattr(self._exp, "excluded_flies", None)
        bits = [name]
        if total:
            bits.append(f"{total} flies")
        if excluded is not None:
            bits.append(f"{len(excluded)} " + ("ex" if short else "excluded"))
        if n_flagged is not None:
            bits.append(f"{n_flagged} " + ("flag" if short else "flagged"))
        return " · ".join(bits)

    def _fly_counts(self):
        """``(analysed flies, flagged)`` from load-time attributes. The
        Low-Movement Flag carries both; a type with advisory flags instead
        (Paired Open Field) counts its trackers and its flag rows."""
        flagged = getattr(self._exp, "flagged_flies", None)
        if flagged is not None:
            return flagged.attrs.get("n_total"), len(flagged)
        advisory = getattr(self._exp, "advisory_flags", None)
        if advisory is None:
            return None, None
        excluded = getattr(self._exp, "excluded_flies", None)
        trackers = getattr(getattr(self._exp, "arena", None), "trackers", None) or {}
        total = len(trackers) - (len(excluded) if excluded is not None else 0)
        return total, len(advisory)

    def _experiment_tile_lines(self) -> list[str] | None:
        """The Experiment tile's two lines — which experiment, and its
        flies — each one complete thought, chosen to FIT the tile: every
        line is offered most-detailed first and the tile measures which
        version renders without eliding (user feedback 2026-08-29: no "…",
        no wrapping). Same load-time attributes as the status readout,
        never a fresh summarize. None when nothing is loaded."""
        if self._exp is None:
            return None
        tile = self._tiles["experiment"]
        name = self._loaded_experiment_name()
        ## Which experiment: its name, with its tracking type when that
        ## still fits on the line.
        tracking = _tracking_type_label(self._exp)
        which = [name]
        if tracking:
            which.insert(0, f"{name} — {tracking}")
        ## Its flies: whole words, then abbreviations, then just the total.
        excluded = getattr(self._exp, "excluded_flies", None)
        total, n_flagged = self._fly_counts()
        long_bits, short_bits = [], []
        if total:
            long_bits.append(f"{total} flies")
            short_bits.append(f"{total} flies")
        if excluded is not None:
            long_bits.append(f"{len(excluded)} excluded")
            short_bits.append(f"{len(excluded)} ex")
        if n_flagged is not None:
            long_bits.append(f"{n_flagged} flagged")
            short_bits.append(f"{n_flagged} flag")
        flies = [" · ".join(long_bits), " · ".join(short_bits)]
        if total:
            flies.append(f"{total} flies")
        if not long_bits:
            flies = ["no fly counts yet"]
        return [tile.fitting(which), tile.fitting(flies)]

    def _set_status_for_project(self, project, root, n_reps: int,
                                analyzed: int, pending: int) -> None:
        """Fill the strip's status readout from the already-loaded *project*
        (loading it a second time here would double every refresh's cost)."""
        reps = f"{n_reps} — {analyzed} analyzed"
        if pending:
            reps += f", {pending} folder(s) without a config"
        loaded = self._loaded_experiment_line(short=False)
        rows = [
            ("Project", f"{project.name} — "
                        f"{project.experiment_type.display_name}"),
            ("Path", str(root)),
            ("Replicates", reps),
            ("Experiment", loaded or
                           "none loaded — double-click a replicate"),
        ]
        factors = ";  ".join(f"{k}: {', '.join(v)}"
                             for k, v in project.design_factors.items())
        if factors:
            ## Beyond the four shown rows, so it lands in the tooltip.
            rows.append(("Design", factors))
        self._status_panel.set_rows(rows)

    def _refresh_tiles_inner(self) -> None:
        from .. import batch as batch_mod
        from .. import project as prj
        from ..script_editor.project_actions import DEFAULT_PROJECT_SCRIPT_NAME

        tiles = getattr(self, "_tiles", None)
        if not tiles:
            return

        ## Batch state, computed once: the Batch tile and the Project tile's
        ## batch branch both read it (cheap directory scans, no Project load).
        batch_root = self._batch_root()
        batch_members: list = []
        batch_names: list[str] = []
        ## No designation runs each Project's own default script, so the
        ## readout names that script rather than a built-in (ADR-0009).
        default_designation = f"each project's '{DEFAULT_PROJECT_SCRIPT_NAME}'"
        designated = default_designation
        if batch_root is not None:
            ## The cached walk (ADR-0011) — this runs on every tile refresh.
            batch_members = self._scan_batch(batch_root)["members"]
            batch_names = [member.key for member in batch_members]
            meta = batch_mod.load_batch_file(batch_root)
            designated = meta["script"] or default_designation

        # Batch tile: the containment level above Project (ADR-0009).
        ## Never dimmed, whatever is selected (user feedback 2026-08-24):
        ## Batch and Project are the two ways INTO the Hub — their panels
        ## hold the pickers that create the state everything else waits on,
        ## so they are always available and must not read as unavailable.
        tile = tiles.get("batch")
        if tile is not None:
            tile.set_dimmed(False)
            if batch_root is not None:
                blocked = sum(len(m.blocked) for m in batch_members)
                headline = f"{len(batch_names)} project(s)"
                if blocked:
                    headline += f" · {blocked} blocked"
                tile.set_summary([headline, designated])
            elif self._project_root() is not None:
                tile.set_summary(["selection is a project",
                                  "load its parent to batch"])
            else:
                tile.set_summary(["no batch",
                                  "load a folder of projects"])

        # Project tile: the effective project (enclosing one when a
        # replicate is loaded). Its second line is the replicates' state; the
        # loaded experiment is the Experiment tile's to report (ADR-0012).
        root = self._project_root()
        tile = tiles["project"]
        tile.set_dimmed(False)          # always available — see the Batch tile
        if root is not None:
            try:
                project = prj.Project(str(root))
                analyzed = sum(
                    1 for n in project.experiment_names
                    if project.experiment_status(n)["analyzed"])
                n_reps = len(project.experiment_names)
                try:
                    pending = len(project.unconfigured_dirs())
                except Exception:  # noqa: BLE001
                    pending = 0
                if pending and not n_reps:
                    line = f"{pending} folder(s) await configs"
                elif pending:
                    line = f"{n_reps} reps · {analyzed} ✓ · {pending} pend"
                else:
                    line = f"{n_reps} replicates · {analyzed} analyzed"
                tile.set_summary([project.name, line])
                self._set_status_for_project(project, root, n_reps, analyzed,
                                             pending)
            except Exception as err:  # noqa: BLE001
                tile.set_summary(["project error", str(err)])
                self._status_panel.set_rows(
                    [("Project", str(root)), ("Error", str(err))])
        elif batch_root is not None:
            ## A Batch offers Projects, not experiments: the Project tile's
            ## fix is choosing one in the Batch panel's table (ADR-0009).
            tile.set_summary([Path(batch_root).name,
                              "double-click a project"])
            self._status_panel.set_rows([
                ("Batch", f"{Path(batch_root).name} — "
                          f"{len(batch_names)} Project(s)"),
                ("Path", str(batch_root)),
                ("Script", designated),
                ("Experiment", "none loaded"),
            ])
        elif self._project_dir is not None:
            ## Only a Project offers experiments to load (ADR-0008), so a bare
            ## directory's hint is the Project it still needs.
            tile.set_summary([Path(self._project_dir).name,
                              "not a project yet"])
            self._status_panel.set_rows([
                ("Directory", str(self._project_dir)),
                ("Status", "not a project yet — use Create config…"),
            ])
        else:
            tile.set_summary(["no project", "open or create one"])
            self._status_panel.set_rows([
                ("Project", "no project loaded"),
                ("Next", "Project tile → Open Project or Create…"),
            ])

        # Experiment tile: the loaded replicate, and the gate on the four
        # tiles beneath it (ADR-0012). Unlike Batch and Project it is not a
        # way in — nothing loads from it — so with no experiment it is dimmed
        # AND inert: the sub-strip it would open has nothing to act on.
        tile = tiles["experiment"]
        loaded = self._experiment_tile_lines()
        if loaded is None:
            tile.set_summary(["no experiment loaded",
                              "double-click a replicate in Project"])
            ## Collapse on the UNLOAD transition (lit -> dimmed), not on
            ## every refresh with nothing loaded: refreshes run after every
            ## task and checkbox toggle, and a sub-tile panel opened with
            ## nothing loaded (a programmatic open) must survive them.
            if not tile.is_dimmed():
                self._collapse_experiment()
            tile.set_dimmed(True)
            tile.set_clickable(False)
        else:
            tile.set_summary(loaded)
            tile.set_dimmed(False)
            tile.set_clickable(True)

        # Analyze tile.
        tile = tiles["analyze"]
        if self._exp is None:
            tile.set_summary(["load an experiment first"])
            tile.set_dimmed(True)
        else:
            facet = "faceted" if self._facet_checkbox.isChecked() else "flat"
            tile.set_summary(["ready", facet])
            tile.set_dimmed(False)

        # Plots tile.
        tile = tiles["plots"]
        if self._exp is None:
            tile.set_summary(["load an experiment first"])
            tile.set_dimmed(True)
        else:
            tile.set_summary([f"{len(self._plot_buttons)} plot types",
                              "tabs in the output area"])
            tile.set_dimmed(False)

        # Scripts tile. Experiment Scripts run against the loaded experiment,
        # and are read from its config — with nothing loaded there is nothing
        # to list, so the hint is the load rather than the empty list.
        tile = tiles["scripts"]
        n = len(self._scripts)
        selected = self._scripts_combo.currentText().strip()
        if self._exp is None:
            tile.set_summary(["no experiment", "load one to run scripts"])
            tile.set_dimmed(True)
        elif not n:
            tile.set_summary(["no scripts", "author in Script Editor"])
            tile.set_dimmed(True)
        else:
            tile.set_summary([f"{n} script(s)", selected or "—"])
            tile.set_dimmed(False)

        # AI tile. The summary is about the AI subject (a loaded experiment
        # to summarize) as much as the provider key: with a key but nothing
        # loaded the tile used to sit lit next to a dimmed AI card.
        tile = tiles["ai"]
        if not self._ai_available:
            tile.set_summary(["no API key", "add one to .env"])
            tile.set_dimmed(True)
        else:
            from ..ai import available_providers

            names = ", ".join(prov.provider_name
                              for prov in available_providers())
            ready = self._exp is not None
            tile.set_summary(["ready", names] if ready
                             else [names, "load an experiment first"])
            tile.set_dimmed(not ready)

    def _pick_project_dir(self) -> None:
        start = str(self._project_dir) if self._project_dir else os.getcwd()
        chosen = QFileDialog.getExistingDirectory(self, "Choose project directory", start)
        if chosen:
            self._set_project_dir(chosen)

    def _set_project_dir(self, path: str | Path) -> None:
        """Select *path* as the working Project.

        A replicate inside a Project normalizes to that Project: the selection
        names the Project and nothing else, so no action can silently retarget
        a replicate and there is no drill-in state to return from. Loading a
        replicate is a separate context (``_load_experiment``)."""
        p = Path(path).expanduser().resolve()
        ## A new selection means a new tree to walk (ADR-0011's cache).
        self._invalidate_batch_scan()
        enclosing = self._enclosing_project_dir(p)
        if enclosing is not None:
            p = enclosing
        self._project_dir = p
        # Show only the folder name; the full path lives in self._project_dir
        # and is surfaced via the tooltip.
        self._project_edit.setText(p.name or str(p))
        self._project_edit.setToolTip(str(p))
        ui_settings.add_recent_project(p)
        ## Read through the cache — is_batch_dir walks the whole tree now,
        ## and this runs on every selection change.
        kind = "Batch" if self._scan_batch(p)["members"] else "Project"
        self._log.append_line(f"{kind}: {p}")
        self._refresh_project_config_button()
        self._refresh_scripts()
        self._refresh_project_view()
        self._refresh_batch_view()
        self._refresh_tiles()

    def _project_config_root(self) -> Path | None:
        """Directory whose ``project.yaml`` the Project card edits: the
        effective Project root, or the selected directory when promoting a
        standalone folder into a Project."""
        return self._project_root() or self._project_dir

    def _refresh_project_config_button(self) -> None:
        from .. import project as prj

        root = self._project_config_root()
        if root is None:
            self._btn_edit_cfg.setEnabled(False)
            self._btn_edit_cfg.setText("Edit config…")
            return
        self._btn_edit_cfg.setEnabled(True)
        if prj.is_project_dir(root):
            self._btn_edit_cfg.setText("Edit config…")
            self._btn_edit_cfg.setToolTip(
                "Open the Project editor on this directory's project.yaml.")
        else:
            self._btn_edit_cfg.setText("Create config…")
            self._btn_edit_cfg.setToolTip(
                "Write a default project.yaml here and open the Project editor.")

    def _edit_or_create_project_config(self) -> None:
        """Edit ``project.yaml`` when present; otherwise write a default and
        open the Project editor (``ProjectInfoDialog``) either way."""
        from .. import project as prj

        root = self._project_config_root()
        if root is None:
            self._warn("Choose a project directory first.")
            return
        path = Path(root)
        if not prj.is_project_dir(path):
            ## A project.yaml written beside a tracking_config.yaml makes a
            ## Project whose only experiment is its own root — zero replicates
            ## and nothing to load. The Project belongs on the parent.
            if prj.is_experiment_dir(path):
                parent = path.parent
                resp = QMessageBox.question(
                    self, "Create project",
                    f"'{path.name}' is an experiment, not a Project.\n\n"
                    f"Create the Project on '{parent.name}' instead, so "
                    f"'{path.name}' becomes one of its replicates?")
                if resp != QMessageBox.StandardButton.Yes:
                    return
                path = parent
            try:
                prj.create_project_file(str(path))
            except Exception as err:  # noqa: BLE001
                self._warn(f"Could not write project.yaml:\n{err}")
                return
            self._log.append_line(f"Created {path / prj.PROJECT_FILENAME}")
        dialog = ProjectInfoDialog(self, start_dir=str(path))
        if dialog.exec() and dialog.saved_dir:
            self._set_project_dir(dialog.saved_dir)
        else:
            # Refresh even on cancel: Create may have just written the marker.
            self._set_project_dir(path)

    # ==================================================================
    # Scripts card
    # ==================================================================

    def _config_path(self) -> Path | None:
        """Canonical ``tracking_config.yaml`` of the experiment in hand: the
        loaded one, else a selected standalone experiment directory. None at a
        Project root, whose configs live one level down."""
        from .. import project as prj

        directory = self._experiment_dir()
        if directory is None:
            if self._project_dir is None or prj.is_project_dir(
                    self._project_dir):
                return None
            directory = self._project_dir
        return directory / _CANONICAL_CONFIG

    def _refresh_scripts(self) -> None:
        from ..script_editor.runner import load_scripts

        cfg = self._config_path()
        scripts: list[dict] = []
        if cfg and cfg.exists():
            try:
                scripts = load_scripts(cfg)
            except Exception as err:  # noqa: BLE001
                self._log_issue(f"[scripts] failed to read {cfg}: {err}")
        self._scripts = scripts
        self._scripts_combo.blockSignals(True)
        self._scripts_combo.clear()
        for s in scripts:
            self._scripts_combo.addItem(s.get("name", "Untitled"))
        self._scripts_combo.blockSignals(False)
        has = bool(scripts)
        self._scripts_empty.setVisible(not has)
        self._scripts_combo.setEnabled(has)
        self._btn_run_script.setEnabled(has)
        self._btn_run_all_scripts.setEnabled(has)

    def _run_selected_script(self) -> None:
        idx = self._scripts_combo.currentIndex()
        if idx < 0 or idx >= len(getattr(self, "_scripts", [])):
            return
        self._spawn_script_task([self._scripts[idx]])

    def _run_all_scripts(self) -> None:
        scripts = list(getattr(self, "_scripts", []))
        if not scripts:
            return
        self._spawn_script_task(scripts)

    def _spawn_script_task(self, scripts: list[dict]) -> None:
        from ..script_editor.runner import run_script

        project_dir = self._experiment_dir() or self._project_dir
        exp = self._exp

        # Figure callback (runs on worker thread, signal-free — Qt queues
        # add_figure on the main thread at the end via finished signal).
        # Simpler: capture figures + titles in a list and replay after the task.
        figures: list[tuple[str, object]] = []
        worker_log = []  # collected log lines; flushed via finished_ok

        def _fig(title: str, fig) -> None:
            figures.append((title, fig))

        def _log_cb(msg: str) -> None:
            worker_log.append(str(msg))

        names = [s.get("name", "?") for s in scripts]

        def _run() -> str:
            current_exp = exp
            for s in scripts:
                current_exp = run_script(
                    s,
                    project_dir=project_dir,
                    log_cb=_log_cb,
                    figure_cb=_fig,
                    exp=current_exp,
                ).exp
            return f"Ran {len(scripts)} script(s): {', '.join(names)}"

        def _on_ok(msg: str) -> None:
            for ln in worker_log:
                self._log.append_line(ln)
            if self._discard_figures(figures, "script"):
                self._log.append_line(msg)
                return
            for title, fig in figures:
                self._plot_dock.add_figure(title, fig, interactive=False)
            self._log.append_line(msg)

        def _on_fail(msg: str) -> None:
            for ln in worker_log:
                self._log.append_line(ln)
            self._log_issue(msg)

        # Dispatch through our regular TaskWorker for thread safety + log redirect
        self._spawn_task_with_callbacks("Script run", _run, _on_ok, _on_fail)

    # ==================================================================
    # Behaviour — loading an Experiment
    # ==================================================================

    #: The task names a load runs under; _on_task_finished reveals after them.
    _LOAD_TASKS = ("Load", "Load + QC")

    def _load_experiment(
            self, directory: str | Path | None = None, *,
            run_qc: bool = True, reveal: str | None = None) -> bool:
        """Load *directory* as the current experiment (the replicates table
        passes the row's directory). Without one, fall back to the selected
        directory — the standalone-experiment path, which the Hub itself no
        longer offers (ADR-0008) but the Python API still supports.

        *run_qc* runs the QC suite as part of the load and opens the QC
        viewer on its artifacts; off, the experiment is loaded as it is.
        *reveal* is what to show when the load finishes: ``"analyze"`` (the
        Analyze panel) or ``"group"`` (the Experiment tile's sub-strip)."""
        from .. import project as prj

        target = Path(directory) if directory is not None else self._project_dir
        if target is None:
            self._warn("Choose a project directory first.")
            return False
        if prj.is_project_dir(target):
            self._warn(
                "This is a Project directory. Double-click a replicate in the "
                "Experiments table to load it, or use the Project actions."
            )
            return False
        project_dir = target
        # Experiments always use the canonical tracking_config.yaml (the
        # Project card no longer offers alternate YAML pickers).
        config_name = _CANONICAL_CONFIG
        # Use a list as a mutable holder so the worker callable can hand the
        # built Experiment back to the GUI thread without a custom signal.
        result_holder: list = []

        def _do_load() -> str:
            exp = ExperimentMod.Experiment(str(project_dir), config_path=config_name)
            result_holder.append(exp)
            print(str(exp))
            if run_qc:
                exp.run_qc()
                return f"Loaded experiment and ran QC ({project_dir})."
            return (f"Loaded experiment ({project_dir}) — already analyzed, "
                    "QC not re-run; Run QC / Run Analysis in the Experiment "
                    "group redo it.")

        def _on_ok(msg: str) -> None:
            if result_holder:
                self._exp = result_holder[0]
                self._on_experiment_ready()
                self._load_ready = True
                if run_qc:
                    # Launch the QC viewer so it picks up the freshly-saved
                    # qc/ artifacts — of the experiment just loaded, which is
                    # not the selected directory (that stays on the Project).
                    self._launch_subapp("qc", directory=str(project_dir))
            self._log.append_line(msg)

        def _on_fail(msg: str) -> None:
            self._reveal_after_load = None
            self._load_ready = False
            self._log_issue(msg)
            self._warn("Failed to load experiment — see Output for details.")

        self._log.append_line(f"Loading experiment from {project_dir}…")
        # Don't pop QC artifacts as Hub tabs — the QC viewer (auto-launched on
        # success) is the canonical surface for them.
        self._reveal_after_load = reveal
        self._load_ready = False
        started = self._spawn_task_with_callbacks(
            "Load + QC" if run_qc else "Load", _do_load, _on_ok, _on_fail,
            surface_artifacts=False,
        )
        if not started:
            self._reveal_after_load = None
            self._load_ready = False
        return bool(started)

    def _on_experiment_ready(self) -> None:
        # Enable single-mode analysis buttons (including the new dynamic ones).
        for btn in (
            self._btn_run_analysis,
            self._btn_run_qc,
            self._btn_create_report,
            self._btn_summarize,
            self._btn_pairwise,
        ):
            btn.setEnabled(True)
        # Configure the facet checkbox based on the experiment's cutoffs.
        cutoffs = getattr(self._exp, "facet_cutoffs", None)
        if cutoffs is None:
            self._facet_checkbox.setEnabled(False)
            self._facet_checkbox.setChecked(False)
            self._facet_checkbox.setText("Faceted (no cutoffs in config)")
        else:
            self._facet_checkbox.setEnabled(True)
            self._facet_checkbox.setChecked(True)
            cuts = ", ".join(str(c) for c in cutoffs)
            self._facet_checkbox.setText(f"Faceted (cutoffs: {cuts})")
        self._rebuild_plot_buttons()
        # Sync labels after both rebuild + checkbox state changes.
        self._refresh_dynamic_labels()

        self._refresh_tiles()

    def _unload_experiment(self) -> None:
        """Drop the loaded experiment. Experiment-level actions have no subject
        again until a replicate is double-clicked; the Project is untouched."""
        if self._exp is None:
            return
        name = self._loaded_experiment_line() or "experiment"
        self._exp = None
        self._facet_checkbox.setEnabled(False)
        self._facet_checkbox.setChecked(False)
        self._rebuild_plot_buttons()
        ## Re-derives every experiment-gated button from _exp being None.
        self._set_busy(False)
        self._refresh_scripts()
        self._refresh_project_view()
        self._refresh_tiles()
        self._log.append_line(f"Unloaded {name}.")

    def _rebuild_plot_buttons(self) -> None:
        # Clear existing plot buttons.
        for btn in self._plot_buttons:
            btn.setParent(None)
            btn.deleteLater()
        self._plot_buttons.clear()
        # Drop entries in the dynamic-label registry that belong to plot buttons
        # we just deleted, so _refresh_dynamic_labels doesn't dereference dead Qt objects.
        self._dynamic_label_buttons = [
            (b, lbl) for (b, lbl) in self._dynamic_label_buttons
            if b is self._btn_summarize or b is self._btn_pairwise
        ]
        self._plots_empty.setVisible(False)

        if self._exp is None:
            self._plots_empty.setVisible(True)
            return

        tt_name = self._exp.parameters.get_tracking_type().name
        entries = _PLOT_BUTTONS.get(tt_name, [])
        if not entries:
            self._plots_empty.setText(
                f"No plots registered for tracking type {tt_name}."
            )
            self._plots_empty.setVisible(True)
            return

        for label, flat_method, facet_method in entries:
            icon_name = _PLOT_ICON_BY_LABEL.get(label, "plot")
            btn = ActionButton(label, Category.PLOTS, icon_name=icon_name)
            btn.clicked.connect(
                lambda _=False, lbl=label, fm=flat_method, fa=facet_method:
                    self._render_dynamic_plot(lbl, fm, fa)
            )
            self._plots_card.add_body(btn)
            self._plot_buttons.append(btn)
            self._dynamic_label_buttons.append((btn, label))

    def _render_plot(self, method_name: str, kwargs: dict, title: str) -> None:
        """Run an Arena plot method, capture its figure, and add a PlotDock tab.

        Routes through ``self._exp.arena`` rather than ``self._exp`` so we
        bypass any Experiment wrappers that save-and-close the figure (which
        would leave nothing for ``capture_figures`` to grab).
        """
        if self._exp is None:
            return
        fn = getattr(self._exp.arena, method_name, None)
        if fn is None:
            self._log_issue(f"Unknown plot method: arena.{method_name}")
            return
        self._log.append_line(f"[plot] arena.{method_name}({_fmt_kwargs(kwargs)})")

        figs: list = []

        def _do() -> str:
            # ``capture_figures`` swaps the module-global ``plt.show``; running
            # it on the GUI thread meant a plot click during an analysis had
            # two threads fighting over that global — the worker's figure was
            # swallowed, its PNG never written, and plt.show left corrupted.
            # Going through TaskWorker serialises the two (the Plots card is
            # also disabled while busy, see _set_busy).
            with capture_figures() as captured:
                fn(**kwargs)
            figs.extend(captured)
            return f"Plot complete: {title}."

        def _on_ok(msg: str) -> None:
            if not figs:
                self._log_issue(f"[plot] {method_name} produced no figures.")
                return
            if self._discard_figures([(title, f) for f in figs], "plot"):
                self._log.append_line(msg)
                return
            for i, fig in enumerate(figs):
                tab_title = title if len(figs) == 1 else f"{title} ({i+1})"
                self._plot_dock.add_figure(tab_title, fig, interactive=False)
            self._log.append_line(msg)

        def _on_fail(msg: str) -> None:
            self._log_issue(msg)
            self._warn(f"Plot failed: {title} — see the Errors tab.")

        self._spawn_task_with_callbacks(f"Plot {title}", _do, _on_ok, _on_fail)

    def _render_dynamic_plot(
        self, base_label: str, flat_method: str, facet_method: str
    ) -> None:
        """Dispatch a Plots-card click to either the flat or faceted Arena method."""
        if self._exp is None:
            return
        if self._is_facet_active():
            method = facet_method
            kwargs = {"cutoffs": tuple(self._exp.facet_cutoffs)}
            title = f"{base_label} (facet)"
        else:
            method = flat_method
            kwargs = {}
            title = base_label
        self._render_plot(method, kwargs, title)

    # ------------------------------------------------------------------
    # Facet checkbox plumbing
    # ------------------------------------------------------------------

    def _is_facet_active(self) -> bool:
        return (
            self._facet_checkbox.isEnabled()
            and self._facet_checkbox.isChecked()
            and self._exp is not None
            and getattr(self._exp, "facet_cutoffs", None) is not None
        )

    def _on_facet_toggled(self, _checked: bool = False) -> None:
        self._refresh_dynamic_labels()

    def _refresh_dynamic_labels(self) -> None:
        suffix = " (facet)" if self._is_facet_active() else ""
        for btn, base in self._dynamic_label_buttons:
            try:
                btn.setText(base + suffix)
            except RuntimeError:
                # Underlying C++ widget was deleted; ignore.
                pass

    # ==================================================================
    # Behaviour — analysis tasks (threaded)
    # ==================================================================

    def _prompt_run_notes(self, exp) -> None:
        """Collect optional user notes for the run about to start.

        Prefilled with any existing notes so they can be revised; OK saves
        (blank clears), Cancel keeps the existing notes. Saved notes are
        rendered near the top of the report.
        """
        try:
            existing = exp.read_run_notes()
        except Exception:  # noqa: BLE001 — a broken notes file must not block a run
            existing = ""
        text, ok = QInputDialog.getMultiLineText(
            self, "Run notes",
            "Notes for this run (optional) — shown at the top of the report.\n"
            "Leave blank to clear saved notes. Cancel keeps them unchanged.",
            existing,
        )
        if not ok:
            return
        try:
            exp.write_run_notes(text)
        except OSError as err:
            QMessageBox.warning(self, "Run notes",
                                f"Could not save the notes:\n{err}")

    def _run_full_analysis(self) -> None:
        if self._exp is None:
            return
        exp = self._exp
        self._prompt_run_notes(exp)
        self._spawn_task(
            "Run analysis",
            lambda: (exp.run_analysis(), exp.create_report(), "Analysis + report complete.")[-1],
        )

    def _run_qc_only(self) -> None:
        if self._exp is None:
            return
        exp = self._exp
        self._spawn_task("QC", lambda: exp.qc() or "QC complete.")

    def _run_create_report(self) -> None:
        if self._exp is None:
            return
        exp = self._exp
        self._prompt_run_notes(exp)
        self._spawn_task("PDF report", lambda: exp.create_report())

    def _run_summarize(self) -> None:
        if self._exp is None:
            return
        exp = self._exp
        if self._is_facet_active():
            cutoffs = tuple(exp.facet_cutoffs)

            def _do() -> str:
                exp.save_summary(cutoffs=cutoffs)
                return "Summarize (facet) complete."

            self._spawn_task("Summarize (facet)", _do)
        else:
            def _do() -> str:
                # Pass cutoffs=None so save_summary writes only the flat CSV.
                exp.save_summary(cutoffs=None)
                return "Summarize complete."

            self._spawn_task("Summarize", _do)

    def _run_pairwise(self) -> None:
        if self._exp is None:
            return
        exp = self._exp
        if self._is_facet_active():
            cutoffs = tuple(exp.facet_cutoffs)

            def _do() -> str:
                exp.stats(cutoffs=cutoffs, save=True)
                return "Pairwise comparisons (facet) complete."

            self._spawn_task("Pairwise (facet)", _do)
        else:
            def _do() -> str:
                _run_pairwise_flat(exp)
                return "Pairwise comparisons complete."

            self._spawn_task("Pairwise", _do)

    def _spawn_task(self, task_name: str, fn: Callable[[], object]) -> None:
        self._spawn_task_with_callbacks(task_name, fn, self._on_task_ok, self._on_task_failed)

    def _spawn_task_with_callbacks(
        self,
        task_name: str,
        fn: Callable[[], object],
        on_ok: Callable[[str], None],
        on_fail: Callable[[str], None],
        surface_artifacts: bool = True,
    ) -> bool:
        ## A panel closes on a click and nothing else: launching a task used to
        ## close it, which pulled the rest of the card away from a user working
        ## down it. The busy state greys the cards in place instead, and the
        ## output is one click on the background away.
        if self._worker is not None and self._worker.isRunning():
            self._warn("Another task is already running.")
            return False
        self._progress.setVisible(True)
        self._set_busy(True)
        # Whether ``Saved: <path>`` lines from this task should appear as
        # PlotDock tabs. Disabled for Load + QC since the QC viewer is the
        # canonical surface for those artifacts.
        self._surface_artifacts = surface_artifacts
        worker = TaskWorker(task_name, fn)
        worker.log_text.connect(self._on_worker_log)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        worker.finished.connect(lambda: self._on_task_finished(worker))
        self._worker = worker
        worker.start()
        return True

    def _discard_figures(self, titled_figures: list, source: str) -> bool:
        """When tabs are suppressed, close *titled_figures* and say so.

        Returns True when they were discarded, so the caller skips its
        add_figure loop. Closing matters: a figure that never becomes a tab
        has no widget to own it, and pyplot would hold it for the life of the
        process — over a Batch Run that is exactly the leak the switch is
        meant to avoid.
        """
        if not self._tabs_suppressed() or not titled_figures:
            return False
        from ..ui.widgets import _close_figure

        for _title, figure in titled_figures:
            _close_figure(figure)
        self._log.append_line(
            f"[{source}] {len(titled_figures)} figure(s) not shown — "
            "'Suppress new plot / output tabs' is on (Batch card).")
        return True

    def _tabs_suppressed(self) -> bool:
        """The Batch card's 'Suppress new plot / output tabs' switch.

        Read through ``getattr`` because the log starts flowing before the
        cards are built, and a task can outlive the panel that owns the box.
        """
        box = getattr(self, "_chk_suppress_tabs", None)
        return box is not None and box.isChecked()

    def _on_worker_log(self, text: str) -> None:
        """Mirror worker stdout to the Output tab and (when enabled for the
        current task) surface ``Saved: <path>`` artifacts as PlotDock tabs."""
        ## Raw stdout: no line discipline, so the streaming path.
        self._log.append_stream(text)
        if not getattr(self, "_surface_artifacts", True):
            return
        for line in text.splitlines():
            m = _SAVED_RE.match(line)
            if not m:
                continue
            path_str = m.group(1).strip()
            if not path_str:
                continue
            try:
                path = Path(path_str)
            except Exception:  # noqa: BLE001
                continue
            self._add_artifact_tab(path)

    def _add_artifact_tab(self, path: Path) -> None:
        """Add a zoomable PlotDock tab for *path* (PNG / TXT / CSV)."""
        ## Gated here rather than only at the call sites, so no future caller
        ## can bypass the switch. The file itself is already on disk.
        if self._tabs_suppressed():
            return
        if not path.exists():
            return
        key = str(path.resolve())
        cached = self._artifact_tabs.get(key)
        if cached is not None:
            try:
                idx = self._plot_dock.indexOf(cached)
            except RuntimeError:
                # Belt and braces: the C++ side is normally pruned by the
                # destroyed handler below, but this slot is queued, so never
                # let a dead wrapper raise here — PyQt escalates an exception
                # in a queued slot to qFatal() and the whole Hub goes down
                # mid-analysis.
                idx = -1
            if idx >= 0:
                self._plot_dock.setCurrentIndex(idx)
                return
            # Tab was closed by the user — drop the stale entry and re-add below.
            self._artifact_tabs.pop(key, None)

        suffix = path.suffix.lower()
        title = path.stem
        if suffix == ".png":
            view = ZoomableImageView(path)
            if view.is_empty():
                return
            tab_icon = icon("plots", category=Category.PLOTS)
        elif suffix in (".txt", ".csv"):
            view = ZoomableTextView(path)
            tab_icon = icon("csv", category=Category.ANALYZE)
        else:
            return  # PDFs / other formats: just leave them on disk.

        self._artifact_tabs[key] = view
        # Closing the tab (or "Clear Analysis Tabs") calls deleteLater; prune the
        # mapping when the widget actually goes away so the next "Saved:" line
        # for the same path can't reach a deleted C++ object.
        # Everything the slot needs is bound as a default argument: a plain
        # closure over ``self`` breaks when the signal fires from the garbage
        # collector, which clears the enclosing cell first (NameError inside a
        # Qt slot is fatal).
        view.destroyed.connect(
            lambda *_args, hub=self, k=key, w=view: hub._forget_artifact_tab(k, w)
        )
        self._plot_dock.add_widget(title, view, tab_icon)

    def _forget_artifact_tab(self, key: str, widget: QWidget) -> None:
        """Drop *key* from the artifact map, but only if it still maps to *widget*.

        Guards the case where the tab was re-added before the old widget's
        deferred deletion was processed.
        """
        if self._artifact_tabs.get(key) is widget:
            del self._artifact_tabs[key]

    def _on_task_ok(self, msg: str) -> None:
        self._log.append_line(msg)

    def _on_task_failed(self, msg: str) -> None:
        # An Errors-tab badge alone left a failed run looking like a finished
        # one; _load_experiment already pops a dialog, so do the same here.
        self._log_issue(msg)
        headline = msg.splitlines()[0] if msg else "The task failed."
        QMessageBox.warning(
            self, "PyTrackingAnalysis",
            f"{headline}\n\nSee the Errors tab for the full traceback.",
        )

    def _on_task_finished(self, worker: TaskWorker) -> None:
        is_load = getattr(worker, "task_name", None) in self._LOAD_TASKS
        reveal = (self._reveal_after_load
                  if is_load and self._load_ready and self._exp is not None
                  else None)
        if is_load:
            self._reveal_after_load = None
            self._load_ready = False
        if self._worker is worker:
            self._worker = None
        self._progress.setVisible(False)
        self._set_busy(False)
        ## Any finished task may have changed replicate artifacts (a Run
        ## Analysis on the loaded experiment included), so the project view's
        ## status table refreshes here — the one GUI-thread point every task
        ## passes through. A Batch Run changes every Project, so the batch
        ## table refreshes for the same reason.
        ## A finished task may have written reports or analysis into the
        ## batch tree, so the cached walk is stale.
        self._invalidate_batch_scan()
        self._refresh_project_view()
        self._refresh_batch_view()
        self._refresh_tiles()
        if reveal == "analyze":
            self._open_panel("analyze")
        elif reveal == "group":
            self._expand_experiment()

    def _set_busy(self, busy: bool) -> None:
        for btn in (
            self._btn_run_analysis,
            self._btn_run_qc,
            self._btn_create_report,
            self._btn_summarize,
            self._btn_pairwise,
        ):
            btn.setEnabled((not busy) and self._exp is not None)
        # The AI summary additionally needs a provider key (checked once at
        # card build; presence-gated per ADR-0004).
        self._btn_ai_summary.setEnabled(
            (not busy) and self._exp is not None and self._ai_available
        )
        # Plot rendering, script runs, and Batch Runs are workloads too;
        # leaving their cards live during a task let a second one start
        # behind the first.
        for key in ("plots", "scripts", "batch"):
            card = self._cards.get(key)
            if card is None:
                continue
            card.setEnabled(not busy)

    # ==================================================================
    # Behaviour — tools
    # ==================================================================

    def _yaml_validation_targets(self) -> list[Path]:
        """Every YAML worth checking for the current selection.

        At a Project that is its ``project.yaml`` plus one
        ``tracking_config.yaml`` per replicate — validating only the loaded
        replicate left the rest of a Project unchecked, which is the case
        where a design mismatch actually hides. Elsewhere it is the single
        experiment config in hand.
        """
        from .. import project as prj

        root = self._project_root()
        if root is not None and prj.is_project_dir(root):
            targets = [Path(root) / prj.PROJECT_FILENAME]
            for entry in sorted(Path(root).iterdir()):
                config = entry / _CANONICAL_CONFIG
                if entry.is_dir() and config.is_file():
                    targets.append(config)
            return targets
        path = self._config_path()
        return [path] if path is not None else []

    def _validate_yaml(self) -> None:
        # Parsing cleanly is not the same as being usable — check the semantics
        # too, so an unknown rig or a missing movie calibration is reported here
        # rather than surfacing mid-analysis or silently falling back to defaults.
        from .. import project as prj
        from ..config_validation import validate_config_file

        targets = self._yaml_validation_targets()
        if not targets:
            self._log_issue(
                "[validate] No YAML here — open a Project (or load an "
                "experiment) first.")
            return

        total = 0
        for path in targets:
            if path.name == prj.PROJECT_FILENAME:
                problems = self._validate_project_file(path)
            else:
                problems = validate_config_file(str(path))
            total += len(problems)
            if problems:
                self._log_issue(
                    f"[validate] {path}: {len(problems)} problem(s) found:")
                for problem in problems:
                    self._log_issue(f"[validate]   • {problem}")
            else:
                self._log.append_line(f"[validate] {path} is valid.")
        summary = (f"[validate] {len(targets)} file(s) checked, "
                   f"{total} problem(s) found.")
        if total:
            self._log_issue(summary)
        else:
            self._log.append_line(summary)

    def _validate_project_file(self, path: Path) -> list[str]:
        """Problems in a ``project.yaml``: it parses, and the Project it
        describes actually loads — which is where design mismatches between
        replicates surface."""
        import yaml

        from .. import project as prj

        try:
            with open(path, encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as err:
            return [f"YAML syntax error: {err}"]
        except OSError as err:
            return [f"Could not read the file: {err}"]
        if data is not None and not isinstance(data, dict):
            return ["project.yaml must be a mapping "
                    f"(found {type(data).__name__})."]

        problems: list[str] = []
        try:
            project = prj.Project(str(path.parent))
        except Exception as err:  # noqa: BLE001
            ## A Project that will not load is the loudest problem this file
            ## can have — a design mismatch raises exactly here.
            return [f"{type(err).__name__}: {err}"]
        problems.extend(project.warnings)
        from ..script_editor.project_actions import project_validation_issues
        from ..script_editor.runner import ScriptError, load_scripts

        try:
            scripts = load_scripts(str(path))
        except ScriptError as err:
            problems.append(f"scripts: {err}")
        else:
            if not scripts:
                problems.append(
                    "no Project Script — a Batch Run cannot run this project "
                    "(the Script Editor can add one).")
            for script in scripts:
                for index, issue in project_validation_issues(
                        script.get("steps") or []):
                    problems.append(
                        f"script '{script.get('name')}' step {index + 1}: "
                        f"{issue}")
        return problems

    def _clear_mpl_cache(self) -> None:
        """Drop matplotlib's cache directory.

        Run on the way out rather than from a button: a stale font cache
        shows up as a figure that will not render, and by the time anyone
        goes looking for a button to press they have already lost the run.
        Never raises — this must not be able to stop the window closing.
        """
        try:
            import matplotlib as mpl

            cache = Path(mpl.get_cachedir())
            if cache.exists():
                shutil.rmtree(cache, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def _launch_subapp(self, which: str, directory=None) -> None:
        """Launch a child app in a separate process.

        The child outlives the Hub on purpose, so the user can close the Hub and
        keep editing. We keep the handles only so a child that dies immediately
        (bad install, import error) gets reported instead of vanishing silently.
        *directory* overrides the selected directory (the Plot Editor is
        project-level, so its button passes the effective Project root even
        when a replicate is the current directory).
        """
        args = [sys.executable, "-m", "pytrackinganalysis", which]
        target = directory if directory is not None else self._project_dir
        if target:
            args.append(str(target))
        try:
            process = subprocess.Popen(args, close_fds=True)
        except Exception as err:  # noqa: BLE001
            self._warn(f"Failed to launch {which}: {err}")
            return

        self._subapps.append((which, process))
        self._log.append_line(f"[tools] launched pytrack-{which} (pid {process.pid})")
        QTimer.singleShot(2000, lambda: self._check_subapp(which, process))
        self._start_reaper()

    def _start_reaper(self) -> None:
        """Keep polling the child apps so exited ones don't stay zombies.

        A QC viewer is spawned on every successful load and each handle used to
        be polled exactly once, 2 s in; anything the user closed later was
        never waited on and lingered as a defunct process for the life of the
        Hub.
        """
        timer = getattr(self, "_subapp_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(3000)
            timer.timeout.connect(self._reap_subapps)
            self._subapp_timer = timer
        if not timer.isActive():
            timer.start()

    def _reap_subapps(self) -> None:
        """Collect the exit status of every finished child app."""
        alive: list[tuple[str, object]] = []
        for which, process in self._subapps:
            try:
                code = process.poll()
            except Exception:  # noqa: BLE001
                continue
            if code is None:
                alive.append((which, process))
            else:
                self._log.append_line(
                    f"[tools] pytrack-{which} (pid {process.pid}) exited with code {code}."
                )
        self._subapps = alive
        timer = getattr(self, "_subapp_timer", None)
        if not alive and timer is not None:
            timer.stop()

    def _check_subapp(self, which: str, process) -> None:
        """Report a child app that exited right after launch."""
        code = process.poll()
        if code not in (None, 0):
            self._log_issue(
                f"[tools] pytrack-{which} exited immediately with code {code}. "
                "Run it from a terminal to see the error."
            )
        if code is not None:
            self._subapps = [entry for entry in self._subapps if entry[1] is not process]

    # ==================================================================
    # Theme toggle
    # ==================================================================

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        new_mode = "dark" if resolved_mode() == "light" else "light"
        apply_theme(app, mode=new_mode)
        self._restyle_chrome()
        ui_settings.set_value("theme", new_mode)
        self._btn_theme.setIcon(
            icon("theme_dark" if resolved_mode() == "light" else "theme_light")
        )

    # ==================================================================
    # Helpers
    # ==================================================================

    def _log_issue(self, msg: str) -> None:
        """Log *msg* to the Errors tab as well as the chronological Output tab."""
        self._log.append_line(msg)
        self._err_log.append_line(msg)

    def _warn(self, msg: str) -> None:
        self._err_log.append_line(f"[warning] {msg}")
        QMessageBox.warning(self, "PyTrackingAnalysis", msg)

    # ==================================================================
    # Shutdown
    # ==================================================================

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        ## The click-away filter was installed app-wide; drop it explicitly
        ## (its parenting also removes it when the window is destroyed).
        app = QApplication.instance()
        if app is not None and getattr(self, "_click_filter", None) is not None:
            app.removeEventFilter(self._click_filter)
        """Never let a running task's QThread be destroyed with the window.

        Qt aborts the process in that case; an analysis is exactly the kind of
        long task a user is tempted to close out from under.
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            answer = QMessageBox.question(
                self, "PyTrackingAnalysis",
                f"'{worker.task_name}' is still running.\n\n"
                "Wait for it to finish and then close?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                event.ignore()
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                finished = shutdown_worker(worker)
            finally:
                QApplication.restoreOverrideCursor()
            if not finished:
                QMessageBox.warning(
                    self, "PyTrackingAnalysis",
                    f"'{worker.task_name}' is still running. Closing now would "
                    "abort the application — try again once it finishes.",
                )
                event.ignore()
                return
        self._worker = None
        timer = getattr(self, "_subapp_timer", None)
        if timer is not None:
            timer.stop()
        # Child apps are meant to outlive the Hub, but collect any that have
        # already exited so they aren't left defunct.
        self._reap_subapps()
        ## Last thing before the window goes: the next session starts with a
        ## cache it built itself.
        self._clear_mpl_cache()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _wrap_layout(layout) -> QWidget:
    host = QWidget()
    host.setLayout(layout)
    return host


def _fmt_kwargs(kwargs: dict) -> str:
    if not kwargs:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items())


def _run_pairwise_flat(exp) -> None:
    """Non-facet counterpart to ``Experiment.stats`` — runs over the full range
    and writes ``<exp>_Stats_flat.txt`` to ``analysis_path``."""
    import io as _io
    import sys as _sys

    metrics = exp._stats_metrics()
    if not metrics:
        print("No comparison metrics defined for this tracking type.")
        return

    buf = _io.StringIO()
    saved_stdout = _sys.stdout
    _sys.stdout = buf
    try:
        for metric in metrics:
            try:
                exp.arena.run_pairwise_comparisons(metric=metric)
            except Exception as err:  # noqa: BLE001
                print(f"Warning: could not run comparison for '{metric}': {err}")
    finally:
        _sys.stdout = saved_stdout

    text = buf.getvalue()
    print(text)

    path = os.path.join(
        exp.analysis_path, f"{exp.arena.experiment_name}_Stats_flat.txt"
    )
    with open(path, "w") as f:
        f.write(text)
    print(f"Saved: {path}")


class ProjectInfoDialog(QDialog):
    """Create or edit a Project's ``project.yaml`` (ADR-0005): the directory
    that will hold the replicate experiments, the project's display name, and
    free-text notes (rendered near the top of the Project Report)."""

    def __init__(self, parent=None, start_dir: str | None = None, *,
                 initialize_existing: bool = False):
        super().__init__(parent)
        ## Two of the three ways into a Project share this dialog: creating
        ## the directory outright, and initializing one that is already on
        ## disk. The design half is identical; only the directory and the
        ## name differ, so the mode is a flag, not a subclass.
        self._initialize = bool(initialize_existing)
        self.setWindowTitle("Initialize existing directory"
                            if self._initialize else "Create project")
        self.setMinimumWidth(560)
        self.saved_dir: str | None = None

        from .. import project as prj
        self._prj = prj

        outer = QVBoxLayout(self)
        intro_row = QHBoxLayout()
        intro = QLabel(
            "Turn a directory you already have into a Project: it keeps its "
            "own name, its subdirectories become the replicates, and the "
            "design below is inferred from the first one that has a config. "
            "This writes project.yaml into it."
            if self._initialize else
            "A Project is a directory whose subdirectories are replicate "
            "experiments of one design. This writes its project.yaml — "
            "choosing a directory that already is a Project edits it instead."
        )
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        intro_row.addWidget(
            HelpButton("project_yaml", tooltip="project.yaml and shared design")
        )
        outer.addLayout(intro_row)

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(start_dir or "")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(dir_row)
        form.addRow("Directory:", holder)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("defaults to the directory name")
        if self._initialize:
            ## The directory IS the Project, so it names it — shown rather
            ## than hidden so the user can see what it will be called.
            self.name_edit.setReadOnly(True)
            self.name_edit.setToolTip(
                "The chosen directory's own name — initializing in place "
                "does not rename it.")
        form.addRow("Project name:", self.name_edit)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "Optional notes — shown near the top of the Project Report.")
        self.notes_edit.setMaximumHeight(80)
        form.addRow("Notes:", self.notes_edit)
        outer.addLayout(form)

        # ---- shared design (the authority every replicate must match) ----
        from .. import experiment_types as _et

        design_box = QGroupBox("Shared design (enforced on every replicate)")
        dform = QFormLayout(design_box)
        dform.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.type_combo = QComboBox()
        for t in _et.available_experiment_types():
            self.type_combo.addItem(t.display_name, t.name)
        ## A design-bearing Project is almost always typed: default to the
        ## first concrete type rather than Custom.
        for i in range(self.type_combo.count()):
            if self.type_combo.itemData(i) != "Custom":
                self.type_combo.setCurrentIndex(i)
                break
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        dform.addRow("Experiment type:", self.type_combo)

        self.factors_table = QTableWidget(0, 2)
        self.factors_table.setHorizontalHeaderLabels(
            ["Factor", "Levels (comma-separated)"])
        self.factors_table.horizontalHeader().setStretchLastSection(True)
        self.factors_table.verticalHeader().setVisible(False)
        self.factors_table.setMaximumHeight(110)
        dform.addRow("Design factors:", self.factors_table)
        frow = QHBoxLayout()
        add_f = QPushButton("Add factor")
        add_f.clicked.connect(lambda: self.factors_table.insertRow(
            self.factors_table.rowCount()))
        rm_f = QPushButton("Remove selected")
        rm_f.clicked.connect(self._remove_factor_row)
        frow.addWidget(add_f)
        frow.addWidget(rm_f)
        frow.addStretch()
        fholder = QWidget()
        fholder.setLayout(frow)
        dform.addRow("", fholder)

        self.cutoffs_edit = QLineEdit()
        self.cutoffs_edit.setPlaceholderText("e.g. 10, 70")
        dform.addRow("Facet cutoffs (min):", self.cutoffs_edit)
        self.labels_edit = QLineEdit()
        self.labels_edit.setPlaceholderText(
            "e.g. Acclimation, Experiment, Cooldown")
        dform.addRow("Phase names:", self.labels_edit)
        self.min_transitions_edit = QLineEdit()
        self.min_transitions_edit.setMaximumWidth(80)
        dform.addRow("min_transitions:", self.min_transitions_edit)
        self.min_movement_edit = QLineEdit()
        self.min_movement_edit.setMaximumWidth(80)
        dform.addRow("min_movement (mm/min):", self.min_movement_edit)
        self.counting_edit = QLineEdit()
        self.counting_edit.setPlaceholderText("e.g. Light, NoLight")
        self.counting_edit.setToolTip(
            "Counting-region NAMES, in order — enforced on every replicate; "
            "each replicate keeps its own aliases.")
        dform.addRow("Counting regions:", self.counting_edit)
        outer.addWidget(design_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.dir_edit.textChanged.connect(self._prefill_from_dir)
        self._prefill_from_dir()
        self._on_type_changed()   # seed type defaults into empty fields

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose the existing directory to initialize"
            if self._initialize else "Choose (or create) the Project directory",
            self.dir_edit.text() or os.getcwd())
        if chosen:
            self.dir_edit.setText(chosen)

    def _prefill_from_dir(self) -> None:
        """When the directory already is a Project, edit it: prefill name and
        notes from its project.yaml and say so in the title."""
        directory = self.dir_edit.text().strip()
        if self._initialize:
            ## Name follows the directory, always. The design is still worth
            ## inferring: an initialized directory usually already holds the
            ## experiments the Project is being wrapped around.
            self.name_edit.setText(
                os.path.basename(os.path.normpath(directory))
                if directory else "")
            if directory and os.path.isdir(directory):
                inferred = self._design_from_experiments(directory)
                if inferred:
                    self._load_design(inferred)
            return
        if directory and self._prj.is_project_dir(directory):
            import yaml as _yaml
            try:
                with open(os.path.join(directory, self._prj.PROJECT_FILENAME),
                          encoding="utf-8") as handle:
                    meta = _yaml.safe_load(handle) or {}
            except Exception:  # noqa: BLE001
                meta = {}
            self.name_edit.setText(str(meta.get("name") or ""))
            self.notes_edit.setPlainText(str(meta.get("notes") or ""))
            self._load_design(dict(meta.get("design") or {})
                              or self._design_from_experiments(directory))
            self.setWindowTitle("Edit project")
        else:
            if directory and os.path.isdir(directory):
                inferred = self._design_from_experiments(directory)
                if inferred:
                    self._load_design(inferred)
            self.setWindowTitle("Create project")

    def _remove_factor_row(self) -> None:
        rows = {i.row() for i in self.factors_table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self.factors_table.removeRow(r)

    def _on_type_changed(self) -> None:
        """Prefill type-derived defaults into empty design fields."""
        from .. import experiment_types as _et

        t = _et.get_experiment_type(self.type_combo.currentData())
        if not self.cutoffs_edit.text().strip() and t.facet_cutoffs:
            self.cutoffs_edit.setText(
                ", ".join(str(c) for c in t.facet_cutoffs))
        if not self.labels_edit.text().strip() and t.phase_labels:
            self.labels_edit.setText(", ".join(t.phase_labels))
        if not self.min_transitions_edit.text().strip() \
                and t.default_min_transitions is not None:
            self.min_transitions_edit.setText(str(t.default_min_transitions))
        if not self.min_movement_edit.text().strip() \
                and t.default_min_movement is not None:
            self.min_movement_edit.setText(f"{t.default_min_movement:g}")
        if not self.counting_edit.text().strip() \
                and t.required_counting_regions:
            self.counting_edit.setText(
                ", ".join(t.required_counting_regions))

    def _design_from_experiments(self, directory) -> dict:
        """Infer a design from the first experiment subdirectory (migration:
        wrapping existing replicates into a Project)."""
        import yaml as _yaml

        from .. import experiment_types as _et

        try:
            for entry in sorted(os.listdir(directory)):
                cfg_path = os.path.join(directory, entry,
                                        "tracking_config.yaml")
                if not os.path.isfile(cfg_path):
                    continue
                with open(cfg_path, encoding="utf-8") as handle:
                    cfg = _yaml.safe_load(handle) or {}
                g = cfg.get("global") or {}
                t = _et.get_experiment_type(g.get("experiment_type"))
                design_global = {"experiment_type": t.name}
                cutoffs = t.resolve_facet_cutoffs(g)
                if cutoffs:
                    design_global["facet_cutoffs"] = list(cutoffs)
                labels = list(g.get("facet_labels") or t.phase_labels or [])
                if labels:
                    design_global["facet_labels"] = labels
                factors = g.get("experimental_design_factors") or {}
                if factors:
                    design_global["experimental_design_factors"] = factors
                if t.resolve_min_transitions(g) is not None:
                    design_global["min_transitions"] = \
                        t.resolve_min_transitions(g)
                if t.resolve_min_movement(g) is not None:
                    value = t.resolve_min_movement(g)
                    design_global["min_movement"] = \
                        int(value) if value == int(value) else value
                design = {"global": design_global}
                counting = list((cfg.get("counting_regions") or {}).keys())
                if counting:
                    design["counting_regions"] = counting
                return design
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _load_design(self, design: dict) -> None:
        g = dict((design or {}).get("global") or {})
        idx = self.type_combo.findData(
            __import__("pytrackinganalysis.experiment_types",
                       fromlist=["get_experiment_type"]).get_experiment_type(
                g.get("experiment_type")).name)
        self.type_combo.setCurrentIndex(max(idx, 0))
        self.factors_table.setRowCount(0)
        for factor, levels in (g.get("experimental_design_factors")
                               or {}).items():
            r = self.factors_table.rowCount()
            self.factors_table.insertRow(r)
            self.factors_table.setItem(r, 0, QTableWidgetItem(str(factor)))
            self.factors_table.setItem(
                r, 1, QTableWidgetItem(", ".join(str(l) for l in levels)))
        cutoffs = g.get("facet_cutoffs") or []
        self.cutoffs_edit.setText(", ".join(str(c) for c in cutoffs))
        self.labels_edit.setText(
            ", ".join(str(l) for l in (g.get("facet_labels") or [])))
        self.min_transitions_edit.setText(
            str(g["min_transitions"]) if "min_transitions" in g else "")
        self.min_movement_edit.setText(
            str(g["min_movement"]) if "min_movement" in g else "")
        self.counting_edit.setText(
            ", ".join(str(n) for n in (design.get("counting_regions") or [])))

    def _build_design(self) -> dict | None:
        """The design dict from the widgets, or None after showing an error."""
        g: dict = {"experiment_type": self.type_combo.currentData()}
        factors: dict = {}
        for r in range(self.factors_table.rowCount()):
            name_item = self.factors_table.item(r, 0)
            level_item = self.factors_table.item(r, 1)
            factor = (name_item.text().strip() if name_item else "")
            if not factor:
                continue
            levels = [l.strip() for l in
                      (level_item.text() if level_item else "").split(",")
                      if l.strip()]
            if not levels:
                QMessageBox.warning(self, self.windowTitle(),
                                    f"Factor '{factor}' needs at least one "
                                    "level.")
                return None
            factors[factor] = levels
        if factors:
            g["experimental_design_factors"] = factors
        text = self.cutoffs_edit.text().strip()
        if text:
            try:
                cutoffs = [float(v) for v in text.split(",") if v.strip()]
            except ValueError:
                QMessageBox.warning(self, self.windowTitle(),
                                    "Facet cutoffs must be numbers, "
                                    "comma-separated.")
                return None
            g["facet_cutoffs"] = [int(c) if c == int(c) else c
                                  for c in cutoffs]
            labels = [l.strip() for l in self.labels_edit.text().split(",")
                      if l.strip()]
            if labels and len(labels) != len(cutoffs) + 1:
                QMessageBox.warning(
                    self, self.windowTitle(),
                    f"{len(cutoffs)} cutoffs create {len(cutoffs) + 1} "
                    f"phases — give {len(cutoffs) + 1} names or none.")
                return None
            if labels:
                g["facet_labels"] = labels
        for key, edit in (("min_transitions", self.min_transitions_edit),
                          ("min_movement", self.min_movement_edit)):
            text = edit.text().strip()
            if text:
                try:
                    value = float(text)
                except ValueError:
                    QMessageBox.warning(self, self.windowTitle(),
                                        f"{key} must be a number.")
                    return None
                g[key] = int(value) if value == int(value) else value
        design: dict = {"global": g}
        counting = [n.strip() for n in self.counting_edit.text().split(",")
                    if n.strip()]
        if counting:
            design["counting_regions"] = counting
        return design

    def _resolve_existing_directory(self, directory: str) -> str | None:
        """The directory to initialize, or None after explaining why not.

        The three ways into a Project stay disjoint: this one is for a
        directory that exists and has no project.yaml. A missing directory is
        Create project's job; one that already has a project.yaml is Open
        Project's."""
        path = os.path.abspath(os.path.expanduser(directory))
        name = os.path.basename(os.path.normpath(path))
        if not os.path.isdir(path):
            QMessageBox.warning(
                self, self.windowTitle(),
                f"'{directory}' is not a directory on disk.\n\n"
                "Initializing works on a directory you already have; use "
                "Create project to make a new one.")
            return None
        if self._prj.is_project_dir(path):
            QMessageBox.information(
                self, self.windowTitle(),
                f"'{name}' already has a {self._prj.PROJECT_FILENAME} — it is "
                "a Project already.\n\nUse Open Project to work in it, or "
                "Edit config… to change its design.")
            return None
        ## A project.yaml written beside a tracking_config.yaml makes a
        ## Project whose only experiment is its own root — zero replicates and
        ## nothing to load. The Project belongs on the parent.
        if self._prj.is_experiment_dir(path):
            parent = os.path.dirname(os.path.normpath(path))
            resp = QMessageBox.question(
                self, self.windowTitle(),
                f"'{name}' is an experiment, not a Project.\n\n"
                f"Initialize '{os.path.basename(parent)}' instead, so "
                f"'{name}' becomes one of its replicates?")
            if resp != QMessageBox.StandardButton.Yes:
                return None
            if self._prj.is_project_dir(parent):
                QMessageBox.information(
                    self, self.windowTitle(),
                    f"'{os.path.basename(parent)}' is already a Project — "
                    "use Open Project to work in it.")
                return None
            ## Show the retarget without re-running the prefill: the design
            ## in the widgets may already have been edited by hand.
            self.dir_edit.blockSignals(True)
            self.dir_edit.setText(parent)
            self.dir_edit.blockSignals(False)
            self.name_edit.setText(os.path.basename(parent))
            path = parent
        return path

    def _save(self) -> None:
        directory = self.dir_edit.text().strip()
        if not directory:
            QMessageBox.warning(
                self, self.windowTitle(),
                "Choose the directory to initialize."
                if self._initialize else "Choose the Project directory.")
            return
        if self._initialize:
            directory = self._resolve_existing_directory(directory)
            if directory is None:
                return
        design = self._build_design()
        if design is None:
            return
        try:
            os.makedirs(directory, exist_ok=True)
            self._prj.create_project_file(
                directory,
                ## In place: the directory names the Project, even when the
                ## confirmation above moved the target up to the parent.
                os.path.basename(os.path.normpath(directory))
                if self._initialize
                else self.name_edit.text().strip() or None,
                self.notes_edit.toPlainText().strip(),
                design=design)
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, self.windowTitle(),
                                 f"Could not write project.yaml:\n{err}")
            return
        self.saved_dir = directory
        self.accept()


class ExperimentConfigsDialog(QDialog):
    """Create or edit the per-experiment ``tracking_config.yaml`` files of a
    Project.

    ``project.yaml`` holds the shared design; the tracking configs live one
    level down, one per experiment directory — so a Project directory itself
    has no config to select. This is where those files are made: every
    immediate subdirectory is listed with its config status, missing ones are
    scaffolded from the design (conformant by construction), and existing ones
    open in the Config Editor for that experiment.
    """

    def __init__(self, hub: "HubWindow", project) -> None:
        super().__init__(hub)
        self._hub = hub
        self._project = project
        self._rows: list[tuple[str, bool]] = []   # (directory name, has config)
        self.setWindowTitle("PyTrackingAnalysis — Experiment configs")
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        header_row = QHBoxLayout()
        intro = QLabel(
            f"Each experiment directory in <b>{project.name}</b> carries its "
            f"own {_CANONICAL_CONFIG} — region treatments and rig are "
            "per-recording. Missing ones are created from the project design "
            f"({project.experiment_type.display_name}), so they match it by "
            "construction; edit one to assign its regions."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        header_row.addWidget(intro, 1)
        header_row.addWidget(
            HelpButton("experiment_create",
                       tooltip="Creating and finishing replicate "
                               "tracking_config.yaml files"))
        outer.addLayout(header_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ["Directory", "Config", "Data files"])
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._table.itemDoubleClicked.connect(self._on_double_click)
        outer.addWidget(self._table)

        row = QHBoxLayout()
        self._btn_create = ActionButton("Create config", Category.TOOLS,
                                        icon_name="new")
        self._btn_create.clicked.connect(self._create_selected)
        self._btn_create_all = ActionButton("Create all missing",
                                            Category.TOOLS, icon_name="batch")
        self._btn_create_all.clicked.connect(self._create_all_missing)
        self._btn_edit = ActionButton("Edit config…", Category.TOOLS,
                                      icon_name="config")
        self._btn_edit.setToolTip(
            "Open this experiment's config in the Config Editor.")
        self._btn_edit.clicked.connect(self._edit_selected)
        row.addWidget(self._btn_create)
        row.addWidget(self._btn_create_all)
        row.addWidget(self._btn_edit)
        outer.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._reload()

    # ---- table ------------------------------------------------------------

    def _reload(self) -> None:
        ## Re-read the Project: a config written a moment ago turns a listed
        ## folder into a replicate, and the design validation runs again.
        project = self._hub._current_project()
        if project is not None:
            self._project = project
        project = self._project
        root = Path(project.project_directory)

        self._rows = [(name, True) for name in project.experiment_names]
        self._rows += [(name, False) for name in project.unconfigured_dirs()]
        self._rows.sort(key=lambda item: item[0].lower())

        self._table.setRowCount(0)
        for name, has_config in self._rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            data = root / name / "data"
            try:
                count = sum(1 for entry in data.iterdir() if entry.is_file())
                files = f"{count} file(s)" if count else "empty"
            except OSError:
                files = "no data/ folder"
            for col, value in enumerate(
                    (name, "yes" if has_config else "missing", files)):
                self._table.setItem(r, col, QTableWidgetItem(value))
        if self._rows:
            self._table.selectRow(0)
        self._sync_buttons()

    def _selected(self) -> tuple[str, bool] | None:
        rows = {i.row() for i in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _sync_buttons(self) -> None:
        selected = self._selected()
        self._btn_create.setEnabled(selected is not None and not selected[1])
        self._btn_edit.setEnabled(selected is not None and selected[1])
        self._btn_create_all.setEnabled(
            any(not has_config for _name, has_config in self._rows))

    # ---- actions ----------------------------------------------------------

    def _on_double_click(self, _item) -> None:
        selected = self._selected()
        if selected is None:
            return
        if selected[1]:
            self._edit_selected()
        else:
            self._create_selected()

    def _create_selected(self) -> None:
        selected = self._selected()
        if selected is None or selected[1]:
            return
        if self._hub._create_replicate_config(selected[0]) is not None:
            self._reload()

    def _create_all_missing(self) -> None:
        missing = [name for name, has_config in self._rows if not has_config]
        if not missing:
            return
        resp = QMessageBox.question(
            self, self.windowTitle(),
            f"Create a {_CANONICAL_CONFIG} from the project design in "
            f"{len(missing)} directory(ies)?\n\n" + ", ".join(missing))
        if resp != QMessageBox.StandardButton.Yes:
            return
        for name in missing:
            self._hub._create_replicate_config(name)
        self._reload()

    def _edit_selected(self) -> None:
        selected = self._selected()
        if selected is None or not selected[1]:
            return
        self._hub._launch_subapp(
            "config", Path(self._project.project_directory) / selected[0])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # Wayland uses the desktop-file name as the app id for taskbar icons;
    # setWindowIcon covers X11/Windows/macOS and window title bars.
    app.setDesktopFileName("pytrack-hub")
    app.setWindowIcon(app_icon())
    mode = ui_settings.get("theme", "auto")
    apply_theme(app, mode=mode)

    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = HubWindow(initial_project=initial)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
