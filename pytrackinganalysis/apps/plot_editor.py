"""Plot Editor — the fourth PyQt6 app (``pytrack-plots``).

Opens a project, renders publication figures (plotnine, ADR-0004) from the
project's Plot Specs and Styles, previews them live, and saves vector output
(SVG with editable text / PDF) into ``<project>/figures/``. Presentation only:
it writes ``plot_specs.yaml`` and never touches ``tracking_config.yaml``.

The preview is rendered from the SAME figure object the vector save uses, so
what you see is what Illustrator gets.
"""

from __future__ import annotations

import os
import sys
import time

from ..gui_env import sanitize_input_method_environment

sanitize_input_method_environment()

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import pubfigures as pf
from ..help import HelpButton, make_topbar_help_button
from ..ui import TopBar, app_icon, apply_theme
from ..ui import settings as ui_settings


class ColorButton(QPushButton):
    """A small swatch button; clicking opens a color dialog.

    The dialog exposes the alpha channel: full transparency stores the color
    ``"none"`` (matplotlib's transparent), partial alpha stores an
    ``#rrggbbaa`` hex — both render correctly in the figures."""

    changed = pyqtSignal()

    def __init__(self, color: str = "#2563eb", parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self._color = "#2563eb"
        self.set_color(color)
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def _qcolor(self) -> QColor:
        value = self._color
        if value in ("", "none"):
            return QColor(0, 0, 0, 0)
        if len(value) == 9:  # matplotlib #rrggbbaa
            return QColor(int(value[1:3], 16), int(value[3:5], 16),
                          int(value[5:7], 16), int(value[7:9], 16))
        return QColor(value)

    def set_color(self, color: str) -> None:
        self._color = str(color)
        if self._color in ("", "none"):
            self.setText("none")
            self.setStyleSheet(
                "QPushButton { background: palette(base); color: palette(mid);"
                " border: 1px dashed palette(mid); border-radius: 3px;"
                " font-size: 8pt; }")
            return
        self.setText("")
        c = self._qcolor()
        self.setStyleSheet(
            f"QPushButton {{ background: rgba({c.red()},{c.green()},"
            f"{c.blue()},{c.alpha()}); border: 1px solid palette(mid); "
            f"border-radius: 3px; }}")

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(
            self._qcolor(), self, "Pick color (alpha 0 = transparent)",
            QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if not chosen.isValid():
            return
        alpha = chosen.alpha()
        if alpha == 0:
            self.set_color("none")
        elif alpha < 255:
            self.set_color(f"#{chosen.red():02x}{chosen.green():02x}"
                           f"{chosen.blue():02x}{alpha:02x}")
        else:
            self.set_color(chosen.name())
        self.changed.emit()


# Wheel-transparent controls: ignoring the wheel makes Qt bubble the event up
# to the scroll area, so scrolling the options panel never edits whatever
# control the cursor happens to be over.
class _NoWheelSpin(QDoubleSpinBox):
    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        event.ignore()


class _NoWheelCombo(QComboBox):
    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        event.ignore()

    def minimumSizeHint(self):  # noqa: N802 (Qt override)
        ## AdjustToContents only raises the *preferred* size; a squeezed layout
        ## could still shrink the combo below its items and elide the text
        ## ("boxed" -> "box.."). Refusing to go below the content width fixes
        ## the closed box and its popup alike.
        return self.sizeHint()


class _NoWheelFontCombo(QFontComboBox):
    def wheelEvent(self, event):  # noqa: N802 (Qt override)
        event.ignore()


def _spin(lo, hi, step, decimals=1) -> QDoubleSpinBox:
    box = _NoWheelSpin()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    ## A uniform fixed width. Wide ranges (±10000) otherwise give spinboxes a
    ## minimum size hint sized for "-10000.00", which pushed form rows past
    ## the panel's fixed width and off the window.
    box.setFixedWidth(80)
    return box


def _combo(items=()) -> QComboBox:
    """A combo that always sizes to its longest item — squeezed layouts were
    clipping item text off the right edge."""
    box = _NoWheelCombo()
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    for item in items:
        box.addItem(item)
    return box


class PlotEditorWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.setWindowTitle("PyTrack Plot Editor")
        self.resize(1380, 860)

        self._experiment = None
        self._project = None          # a loaded Project (project mode)
        self._facet_frame = None      # combined facet frame (project mode)
        self._window_labels = None    # (windows, label_of) in project mode
        self._project_dir: str | None = None
        self._specs = pf.ProjectSpecs()
        self._specs.ensure_default_style()
        self._plot_id = "faceted_pi"
        self._data_cache: dict[str, object] = {}
        self._updating = False  # guard: loading controls must not re-render

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(250)
        self._render_timer.timeout.connect(self._render_preview)

        ## Edits reach plot_specs.yaml shortly after they are made, not only
        ## on a save button or on close: the Hub's project report and the
        ## render_publication_figures step read that file, so a ticked
        ## checkbox in an editor still open was invisible to them.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(lambda: self._persist_specs(quiet=True))

        self._build_ui(initial_path)
        if initial_path:
            self.open_project(initial_path)

    # ------------------------------------------------------------------ UI

    def _build_ui(self, initial_path) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        top_bar = TopBar("Plot Editor")
        top_bar.add_right(make_topbar_help_button(self, topic_id="plot_editor"))
        outer.addWidget(top_bar)

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 6)
        open_btn = QPushButton("Open project…")
        open_btn.clicked.connect(self._browse_project)
        bar.addWidget(open_btn)
        self._project_label = QLabel("No project loaded")
        bar.addWidget(self._project_label)
        bar.addSpacing(16)

        bar.addWidget(QLabel("Plot:"))
        self.plot_combo = _combo()
        for plot_id, info in pf.PLOT_TYPES.items():
            self.plot_combo.addItem(info["display"], plot_id)
        self.plot_combo.currentIndexChanged.connect(self._on_plot_switched)
        bar.addWidget(self.plot_combo)

        bar.addWidget(QLabel("Style:"))
        self.style_combo = _combo()
        self.style_combo.currentIndexChanged.connect(self._on_style_switched)
        bar.addWidget(self.style_combo)
        save_style_btn = QPushButton("Save style as…")
        save_style_btn.clicked.connect(self._save_style_as)
        bar.addWidget(save_style_btn)
        self.default_btn = QPushButton("Set as project default")
        self.default_btn.clicked.connect(self._set_default_style)
        bar.addWidget(self.default_btn)
        self.reset_btn = QPushButton("Restore defaults")
        self.reset_btn.setToolTip(
            "Reset this plot's content settings (labels, facets, treatments, "
            "limits) to the defaults. Shared styles are not touched.")
        self.reset_btn.clicked.connect(self._restore_defaults)
        bar.addWidget(self.reset_btn)

        bar.addStretch()
        bar.addWidget(
            HelpButton("plot_editor", tooltip="Plot Editor and publication figures")
        )
        save_svg = QPushButton("Save SVG…")
        save_svg.clicked.connect(lambda: self._save_figure("svg"))
        save_pdf = QPushButton("Save PDF…")
        save_pdf.clicked.connect(lambda: self._save_figure("pdf"))
        bar.addWidget(save_svg)
        bar.addWidget(save_pdf)
        outer.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_controls())
        self.preview = QLabel("Open a project to begin.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ## The pane also carries text (NoPlotData's reason): wrap it, and pin
        ## its color so the dark theme's light text is not lost on white.
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("QLabel { background: white; color: #333; }")
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self.preview)
        preview_scroll.setWidgetResizable(True)
        split.addWidget(preview_scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([430, 950])
        outer.addWidget(split, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")
        self._set_controls_enabled(False)

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)

        # ---- Style ------------------------------------------------------
        style_box = QGroupBox("Style (shared across plots)")
        form = QFormLayout(style_box)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.width_mm = _spin(30, 400, 5)
        self.height_mm = _spin(20, 300, 5)
        size_row = QHBoxLayout()
        size_row.addWidget(self.width_mm)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_mm)
        size_row.addWidget(QLabel("mm"))
        size_holder = QWidget()
        size_holder.setLayout(size_row)
        form.addRow("Figure size:", size_holder)
        self.facet_width = _spin(0.0, 200.0, 5.0)
        self.facet_width.setToolTip(
            "0 = off: the figure is the fixed width above and panels share "
            "it.\n> 0: the figure width becomes margin + this × shown facets, "
            "so each panel keeps the same width when facets are removed.")
        form.addRow("Facet width (mm):", self.facet_width)
        self.facet_height = _spin(0.0, 200.0, 5.0)
        self.facet_height.setToolTip(
            "0 = off: the figure is the fixed height above.\n> 0: the figure "
            "height becomes this panel height + margins that grow with the "
            "title / x label, so adding a label never squashes the panels.")
        form.addRow("Facet height (mm):", self.facet_height)

        self.theme_combo = _combo(["classic", "bw", "minimal"])
        form.addRow("Theme:", self.theme_combo)
        self.font_combo = _NoWheelFontCombo()
        # QFontComboBox sizes itself to the longest installed font name and
        # blew the panel width — its *minimum* hint ignores a max-width cap
        # inside QFormLayout, so the width must be fixed. The popup still
        # shows full names.
        self.font_combo.setFixedWidth(200)
        form.addRow("Font:", self.font_combo)
        self.base_pt = _spin(4, 24, 0.5)
        form.addRow("Base size (pt):", self.base_pt)
        self.text_color = ColorButton("#000000")
        self.text_color.setToolTip(
            "Color for all text: titles, axis labels, tick labels, strips")
        form.addRow("Text color:", self.text_color)
        self.p_value_pt = _spin(4, 14, 0.5)
        self.p_value_pt.setToolTip(
            "Font size for p-value bracket labels — independent of the base "
            "size so brackets stay discreet")
        form.addRow("P-value size (pt):", self.p_value_pt)
        self.point_size = _spin(0.2, 8, 0.2)
        form.addRow("Point size:", self.point_size)
        self.point_alpha = _spin(0.05, 1.0, 0.05, decimals=2)
        form.addRow("Point alpha:", self.point_alpha)
        self.jitter = _spin(0.0, 0.45, 0.02, decimals=2)
        form.addRow("Jitter width:", self.jitter)
        self.point_stroke = _spin(0.0, 3.0, 0.1)
        self.point_stroke.setToolTip("Black outline around each point; 0 = none")
        form.addRow("Point outline (pt):", self.point_stroke)
        self.mean_combo = _combo(["point+sem", "bar+sem",
                                  "point+95ci", "bar+95ci"])
        form.addRow("Mean style:", self.mean_combo)
        self.mean_color = ColorButton("#111111")
        form.addRow("Mean color:", self.mean_color)
        self.geom_combo = _combo(["dots", "box", "box+dots"])
        form.addRow("Geometry:", self.geom_combo)
        self.line_pt = _spin(0.2, 4.0, 0.1)
        form.addRow("Line weight (pt):", self.line_pt)

        strip_row = QHBoxLayout()
        self.strip_combo = _combo(["plain", "boxed"])
        self.strip_bg = ColorButton("#d9d9d9")
        strip_row.addWidget(self.strip_combo)
        strip_row.addWidget(self.strip_bg)
        strip_row.addStretch()
        strip_holder = QWidget()
        strip_holder.setLayout(strip_row)
        form.addRow("Facet strip:", strip_holder)

        panel_row = QHBoxLayout()
        self.panel_bg_check = QCheckBox("fill")
        self.panel_bg = ColorButton("#fafafa")
        panel_row.addWidget(self.panel_bg_check)
        panel_row.addWidget(self.panel_bg)
        panel_row.addStretch()
        panel_holder = QWidget()
        panel_holder.setLayout(panel_row)
        form.addRow("Panel background:", panel_holder)
        lay.addWidget(style_box)

        # ---- Spec -------------------------------------------------------
        spec_box = QGroupBox("This plot")
        sform = QFormLayout(spec_box)
        sform.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.title_edit = QLineEdit()
        sform.addRow("Title:", self.title_edit)
        self.x_label = QLineEdit()
        sform.addRow("X label:", self.x_label)
        self.y_label = QLineEdit()
        sform.addRow("Y label:", self.y_label)

        ylim_row = QHBoxLayout()
        self.ylim_check = QCheckBox("limits")
        self.ylim_lo = _spin(-10000, 10000, 0.5, decimals=2)
        self.ylim_hi = _spin(-10000, 10000, 0.5, decimals=2)
        ylim_row.addWidget(self.ylim_check)
        ylim_row.addWidget(self.ylim_lo)
        ylim_row.addWidget(QLabel("–"))
        ylim_row.addWidget(self.ylim_hi)
        ylim_holder = QWidget()
        ylim_holder.setLayout(ylim_row)
        sform.addRow("Y axis:", ylim_holder)
        self.free_y_check = QCheckBox("independent per facet")
        self.free_y_check.setToolTip(
            "Each facet scales its own y axis (ggplot scales='free_y') — "
            "right for unbounded metrics like movement or transitions.\n"
            "Y limits are ignored while this is on.")
        self.free_y_check.toggled.connect(
            lambda on: [w.setEnabled(not on) for w in
                        (self.ylim_check, self.ylim_lo, self.ylim_hi)])
        sform.addRow("Y scaling:", self.free_y_check)

        ref_row = QHBoxLayout()
        self.ref_check = QCheckBox("at")
        self.ref_value = _spin(-10000, 10000, 0.5, decimals=2)
        ref_row.addWidget(self.ref_check)
        ref_row.addWidget(self.ref_value)
        ref_row.addStretch()
        ref_holder = QWidget()
        ref_holder.setLayout(ref_row)
        sform.addRow("Reference line:", ref_holder)
        self.pvalues_check = QCheckBox("Welch / Tukey per facet")
        self.pvalues_check.setToolTip(
            "Two shown treatments: Welch's t-test; three or more: Tukey HSD. "
            "One shown treatment: PI is tested against 0 and Percentage "
            "against 0.5 (one-sample t-test), the p-value printed above the "
            "group.")
        sform.addRow("P-value brackets:", self.pvalues_check)
        self.mark_check = QCheckBox("point shape per replicate")
        self.mark_check.setToolTip(
            "Project mode only: each replicate experiment gets its own point "
            "shape, with a legend. Ignored for a single experiment.")
        sform.addRow("Mark experiments:", self.mark_check)
        lay.addWidget(spec_box)

        # ---- Facets -----------------------------------------------------
        facet_box = QGroupBox("Facets")
        flay = QVBoxLayout(facet_box)
        self.facet_table = QTableWidget(0, 2)
        self.facet_table.setHorizontalHeaderLabels(["Phase", "Label"])
        self.facet_table.horizontalHeader().setStretchLastSection(True)
        self.facet_table.verticalHeader().setVisible(False)
        self.facet_table.setMaximumHeight(120)
        flay.addWidget(self.facet_table)
        lay.addWidget(facet_box)

        # ---- Treatments -------------------------------------------------
        treat_box = QGroupBox("Treatments")
        tlay = QVBoxLayout(treat_box)
        self.treat_table = QTableWidget(0, 3)
        self.treat_table.setHorizontalHeaderLabels(
            ["Treatment", "Label", "Color"])
        self.treat_table.horizontalHeader().setStretchLastSection(False)
        self.treat_table.verticalHeader().setVisible(False)
        self.treat_table.setMaximumHeight(170)
        tlay.addWidget(self.treat_table)
        move_row = QHBoxLayout()
        up_btn = QPushButton("Move up")
        up_btn.clicked.connect(lambda: self._move_treatment(-1))
        down_btn = QPushButton("Move down")
        down_btn.clicked.connect(lambda: self._move_treatment(+1))
        move_row.addWidget(up_btn)
        move_row.addWidget(down_btn)
        move_row.addStretch()
        tlay.addLayout(move_row)
        lay.addWidget(treat_box)
        lay.addStretch()

        # Every control change flows through one funnel.
        for widget in (self.width_mm, self.height_mm, self.facet_width,
                       self.facet_height, self.base_pt, self.p_value_pt,
                       self.point_size, self.point_alpha, self.jitter,
                       self.point_stroke, self.line_pt, self.ylim_lo,
                       self.ylim_hi, self.ref_value):
            widget.valueChanged.connect(self._on_changed)
        for combo in (self.theme_combo, self.mean_combo, self.geom_combo,
                      self.strip_combo):
            combo.currentIndexChanged.connect(self._on_changed)
        self.font_combo.currentFontChanged.connect(self._on_changed)
        for edit in (self.title_edit, self.x_label, self.y_label):
            edit.textChanged.connect(self._on_changed)
        for check in (self.ylim_check, self.ref_check, self.pvalues_check,
                      self.panel_bg_check, self.free_y_check,
                      self.mark_check):
            check.toggled.connect(self._on_changed)
        for swatch in (self.mean_color, self.strip_bg, self.panel_bg,
                       self.text_color):
            swatch.changed.connect(self._on_changed)
        self.facet_table.itemChanged.connect(self._on_changed)
        self.treat_table.itemChanged.connect(self._on_changed)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        ## A fixed panel width with vertical-only scrolling: content used to
        ## overflow sideways (and the panel width wandered) when values or
        ## fonts changed.
        scroll.setFixedWidth(440)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _set_controls_enabled(self, on: bool) -> None:
        for name in ("plot_combo", "style_combo", "default_btn", "reset_btn"):
            getattr(self, name).setEnabled(on)

    # ------------------------------------------------------------ project

    def _browse_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open project directory")
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> None:
        """Open either directory kind: a Project (combined, pooled data) or a
        single Experiment (its own data) — the same editor serves both."""
        from .. import project as prj
        from ..Experiment import Experiment

        path = os.path.abspath(path)
        ## Publication figures are Project-level (plot_specs.yaml and figures/
        ## live at the project root). An experiment inside a Project redirects
        ## up to its parent; a standalone experiment is refused with guidance.
        if not prj.is_project_dir(path) and prj.is_experiment_dir(path):
            parent = os.path.dirname(path)
            if prj.is_project_dir(parent):
                path = parent
            else:
                QMessageBox.information(
                    self, "Project-level tool",
                    "Publication figures are managed at the Project level. "
                    "This is a standalone experiment directory — create a "
                    "Project around it first (Hub → Create project on the "
                    "parent folder), then open that Project here.")
                self.statusBar().showMessage(
                    "Not opened: publication figures are project-level.")
                return
        ## The current project's edits go to ITS plot_specs.yaml before the
        ## next project's file replaces them in memory.
        if self._loaded:
            self._save_timer.stop()
            self._read_controls()
            self._persist_specs()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage(f"Loading {path} …")
        QApplication.processEvents()
        try:
            if prj.is_project_dir(path):
                loaded = prj.Project(path)
                summary, facet, _excl, missing = loaded.combined_frames()
                if facet is None:
                    raise ValueError(
                        "No replicate has a saved analysis yet — run the "
                        "experiments first"
                        + (f" (missing: {', '.join(missing)})" if missing
                           else "") + ".")
                self._project, self._experiment = loaded, None
                self._facet_frame = facet
                self._window_labels = loaded.window_labels(facet)
                label = f"Project: {loaded.name} " \
                        f"({facet['Experiment'].nunique()} replicates)"
                if missing:
                    QMessageBox.warning(
                        self, "Replicates missing",
                        "Not yet analyzed (omitted from pooled data): "
                        + ", ".join(missing))
            else:
                self._experiment = Experiment(path)
                self._project, self._facet_frame = None, None
                self._window_labels = None
                label = os.path.basename(path)
        except Exception as err:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Could not load", str(err))
            self.statusBar().showMessage("Load failed")
            return
        QApplication.restoreOverrideCursor()

        self._project_dir = path
        self._data_cache.clear()
        self._specs = pf.load_project_specs(path)
        self._project_label.setText(label)
        try:
            ui_settings.add_recent_project(path)
        except Exception:  # noqa: BLE001
            pass

        # The project default style auto-loads with the app (ADR-0004).
        self._reload_style_combo()
        self._set_controls_enabled(True)
        self._load_controls()
        self._schedule_render()
        self.statusBar().showMessage(f"Loaded {os.path.basename(path)}")

    # ----------------------------------------------------- specs plumbing

    @property
    def _loaded(self) -> bool:
        return self._experiment is not None or self._project is not None

    def _region1(self) -> str:
        if self._experiment is not None:
            return pf.region1_name(self._experiment)
        if self._project is not None:
            first = self._project.experiment_names[0]
            counting = (self._project.configs[first].get("counting_regions")
                        or {})
            for name in counting:
                return str(name)
        return "region 1"

    def _current_spec(self) -> pf.PlotSpec:
        spec = self._specs.plots.get(self._plot_id)
        if spec is None:
            spec = pf.default_spec(self._plot_id, self._region1())
            spec.style = self._specs.default_style
            self._specs.plots[self._plot_id] = spec
        if spec.style not in self._specs.styles:
            spec.style = self._specs.default_style
        return spec

    def _current_style(self) -> pf.PlotStyle:
        return self._specs.style_for(self._current_spec())

    def _current_data(self):
        if not self._loaded:
            return None
        if self._plot_id not in self._data_cache:
            metric = pf.PLOT_TYPES[self._plot_id]["metric"]
            if self._project is not None:
                windows, label_of = self._window_labels
                self._data_cache[self._plot_id] = \
                    pf.faceted_data_from_combined(
                        self._facet_frame, metric,
                        [label_of[w] for w in windows],
                        lambda v: label_of[self._project.parse_window(v)])
            else:
                self._data_cache[self._plot_id] = pf.faceted_data(
                    self._experiment, metric)
        return self._data_cache[self._plot_id]

    def _reload_style_combo(self) -> None:
        self._updating = True
        try:
            self.style_combo.clear()
            for name in self._specs.styles:
                self.style_combo.addItem(name, name)
            current = self._current_spec().style
            idx = self.style_combo.findData(current)
            self.style_combo.setCurrentIndex(max(idx, 0))
        finally:
            self._updating = False

    def _persist_specs(self, quiet: bool = False) -> bool:
        """Write plot_specs.yaml; False when it could not be written.

        Never raises: an exception escaping a Qt slot aborts PyQt6, and the
        autosave runs on every edit — a read-only share or a project folder
        moved away must cost a message, not the editor. *quiet* (the
        autosave) reports in the status bar; anything the user asked for
        directly gets a dialog."""
        if not self._project_dir:
            return True
        try:
            pf.save_project_specs(self._project_dir, self._specs)
        except Exception as err:  # noqa: BLE001
            message = f"Could not save {pf.SPECS_FILENAME}: {err}"
            if quiet:
                self.statusBar().showMessage(message)
            else:
                QMessageBox.warning(self, "Not saved", message)
            return False
        return True

    # --------------------------------------------------- controls <-> model

    def _load_controls(self) -> None:
        """Push the current spec + style into the widgets (no re-render)."""
        spec, style = self._current_spec(), self._current_style()
        self._updating = True
        try:
            self.width_mm.setValue(style.width_mm)
            self.height_mm.setValue(style.height_mm)
            self.facet_width.setValue(style.facet_width_mm)
            self.facet_height.setValue(style.facet_height_mm)
            self.theme_combo.setCurrentText(style.theme)
            self.font_combo.setCurrentText(style.font_family)
            self.base_pt.setValue(style.base_pt)
            self.text_color.set_color(style.text_color)
            self.p_value_pt.setValue(style.p_value_pt)
            self.point_size.setValue(style.point_size)
            self.point_alpha.setValue(style.point_alpha)
            self.jitter.setValue(style.jitter_width)
            self.point_stroke.setValue(style.point_stroke)
            self.mean_combo.setCurrentText(style.mean_style)
            self.mean_color.set_color(style.mean_color)
            self.geom_combo.setCurrentText(style.geom)
            self.line_pt.setValue(style.line_pt)
            self.strip_combo.setCurrentText(style.strip_style)
            self.strip_bg.set_color(style.strip_bg or "#d9d9d9")
            self.panel_bg_check.setChecked(bool(style.panel_bg))
            if style.panel_bg:
                self.panel_bg.set_color(style.panel_bg)

            self.title_edit.setText(spec.title)
            self.x_label.setText(spec.x_label)
            self.y_label.setText(spec.y_label)
            self.ylim_check.setChecked(spec.y_limits is not None)
            if spec.y_limits is not None:
                self.ylim_lo.setValue(spec.y_limits[0])
                self.ylim_hi.setValue(spec.y_limits[1])
            self.ref_check.setChecked(spec.ref_line is not None)
            if spec.ref_line is not None:
                self.ref_value.setValue(spec.ref_line)
            self.pvalues_check.setChecked(bool(spec.p_values))
            self.free_y_check.setChecked(bool(spec.free_y))
            self.mark_check.setChecked(bool(spec.mark_experiments))

            self._load_facet_table(spec)
            self._load_treatment_table(spec, style)
        finally:
            self._updating = False

    def _load_facet_table(self, spec: pf.PlotSpec) -> None:
        data = self._current_data()
        ## The metric-missing frame has a plain Phase column; with no phases
        ## to list, the preview's NoPlotData message explains the empty plot.
        phases = (list(data["Phase"].cat.categories)
                  if data is not None
                  and data["Phase"].dtype.name == "category"
                  else [])
        self.facet_table.setRowCount(0)
        included = spec.facets if spec.facets else phases
        for phase in phases:
            row = self.facet_table.rowCount()
            self.facet_table.insertRow(row)
            item = QTableWidgetItem(phase)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if phase in included
                               else Qt.CheckState.Unchecked)
            self.facet_table.setItem(row, 0, item)
            self.facet_table.setItem(
                row, 1, QTableWidgetItem(str(spec.facet_labels.get(phase, phase))))

    def _load_treatment_table(self, spec: pf.PlotSpec, style: pf.PlotStyle) -> None:
        data = self._current_data()
        merged = pf.merged_treatments(spec, data) if data is not None else {}
        self.treat_table.setRowCount(0)
        for i, (name, entry) in enumerate(merged.items()):
            row = self.treat_table.rowCount()
            self.treat_table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entry.get("show", True)
                               else Qt.CheckState.Unchecked)
            self.treat_table.setItem(row, 0, item)
            self.treat_table.setItem(
                row, 1, QTableWidgetItem(str(entry.get("label", name))))
            swatch = ColorButton(style.color_for(name, i))
            swatch.changed.connect(self._on_changed)
            self.treat_table.setCellWidget(row, 2, swatch)

    def _read_controls(self) -> None:
        """Pull widget state back into the current spec + style objects."""
        if self._updating:
            return
        spec, style = self._current_spec(), self._current_style()
        style.width_mm = self.width_mm.value()
        style.height_mm = self.height_mm.value()
        style.facet_width_mm = self.facet_width.value()
        style.facet_height_mm = self.facet_height.value()
        style.theme = self.theme_combo.currentText()
        style.font_family = self.font_combo.currentText()
        style.base_pt = self.base_pt.value()
        style.text_color = self.text_color.color()
        style.p_value_pt = self.p_value_pt.value()
        style.point_size = self.point_size.value()
        style.point_alpha = self.point_alpha.value()
        style.jitter_width = self.jitter.value()
        style.point_stroke = self.point_stroke.value()
        style.mean_style = self.mean_combo.currentText()
        style.mean_color = self.mean_color.color()
        style.geom = self.geom_combo.currentText()
        style.line_pt = self.line_pt.value()
        style.strip_style = self.strip_combo.currentText()
        style.strip_bg = self.strip_bg.color()
        style.panel_bg = self.panel_bg.color() if self.panel_bg_check.isChecked() else ""

        spec.title = self.title_edit.text()
        spec.x_label = self.x_label.text()
        spec.y_label = self.y_label.text()
        spec.y_limits = ([self.ylim_lo.value(), self.ylim_hi.value()]
                         if self.ylim_check.isChecked() else None)
        spec.ref_line = (self.ref_value.value()
                         if self.ref_check.isChecked() else None)
        spec.p_values = self.pvalues_check.isChecked()
        spec.free_y = self.free_y_check.isChecked()
        spec.mark_experiments = self.mark_check.isChecked()

        facets, facet_labels = [], {}
        for row in range(self.facet_table.rowCount()):
            phase_item = self.facet_table.item(row, 0)
            label_item = self.facet_table.item(row, 1)
            if phase_item is None:
                continue
            phase = phase_item.text()
            if phase_item.checkState() == Qt.CheckState.Checked:
                facets.append(phase)
            label = (label_item.text().strip() if label_item else "") or phase
            if label != phase:
                facet_labels[phase] = label
        spec.facets = facets or None
        spec.facet_labels = facet_labels

        treatments = {}
        for row in range(self.treat_table.rowCount()):
            name_item = self.treat_table.item(row, 0)
            label_item = self.treat_table.item(row, 1)
            if name_item is None:
                continue
            name = name_item.text()
            treatments[name] = {
                "label": (label_item.text().strip() if label_item else "") or name,
                "show": name_item.checkState() == Qt.CheckState.Checked,
            }
            swatch = self.treat_table.cellWidget(row, 2)
            if isinstance(swatch, ColorButton):
                style.colors[name] = swatch.color()
        spec.treatments = treatments

    # ------------------------------------------------------------- events

    def _on_changed(self, *_args) -> None:
        if self._updating:
            return
        self._read_controls()
        self._schedule_render()
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._loaded:
            self._save_timer.start()

    def _on_plot_switched(self, _index: int) -> None:
        if self._updating:
            return
        self._read_controls()
        self._plot_id = self.plot_combo.currentData() or "faceted_pi"
        self._reload_style_combo()
        self._load_controls()
        self._schedule_render()

    def _on_style_switched(self, _index: int) -> None:
        if self._updating:
            return
        name = self.style_combo.currentData()
        if not name:
            return
        self._current_spec().style = name
        self._load_controls()
        self._schedule_render()
        self._schedule_save()

    def _move_treatment(self, delta: int) -> None:
        row = self.treat_table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.treat_table.rowCount()):
            return
        self._read_controls()
        spec = self._current_spec()
        names = list(spec.treatments)
        names[row], names[target] = names[target], names[row]
        spec.treatments = {n: spec.treatments[n] for n in names}
        self._load_controls()
        self.treat_table.selectRow(target)
        self._schedule_render()
        self._schedule_save()

    # ---------------------------------------------------------- rendering

    def _schedule_render(self) -> None:
        if self._loaded:
            self._render_timer.start()

    def _render_preview(self) -> None:
        data = self._current_data()
        if data is None:
            return
        spec, style = self._current_spec(), self._current_style()
        start = time.monotonic()
        try:
            g = pf.build_ggplot(data, spec, style)
            png = pf.render_png_bytes(g, style, dpi=130)
        except pf.NoPlotData as err:
            ## In the preview pane, not just the status bar: the reason is
            ## the whole content of the window until the user acts on it.
            self.preview.setText(str(err))
            self.statusBar().showMessage("Nothing to plot")
            return
        except Exception as err:  # noqa: BLE001
            self.statusBar().showMessage(f"Render failed: {err}")
            return
        pix = QPixmap()
        pix.loadFromData(png)
        self.preview.setPixmap(pix)
        elapsed = time.monotonic() - start
        self.statusBar().showMessage(
            f"{pf.PLOT_TYPES[self._plot_id]['display']} — {len(data)} points, "
            f"rendered in {elapsed:.2f}s")

    # -------------------------------------------------------------- saves

    def _save_figure(self, fmt: str) -> None:
        if not self._loaded or self._project_dir is None:
            return
        self._read_controls()
        figures_dir = os.path.join(self._project_dir, pf.FIGURES_DIRNAME)
        os.makedirs(figures_dir, exist_ok=True)
        suggested = os.path.join(figures_dir, f"{self._plot_id}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, f"Save {fmt.upper()}", suggested,
            f"{fmt.upper()} files (*.{fmt})")
        if not path:
            return
        if not path.lower().endswith(f".{fmt}"):
            path += f".{fmt}"
        spec, style = self._current_spec(), self._current_style()
        try:
            g = pf.build_ggplot(self._current_data(), spec, style)
            pf.save_ggplot(g, path, style)
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(err))
            return
        self._persist_specs()
        self.statusBar().showMessage(f"Saved: {path}")

    def _save_style_as(self) -> None:
        if not self._loaded:
            return
        self._read_controls()
        name, ok = QInputDialog.getText(
            self, "Save style as", "Style name (reusable across plots):")
        name = (name or "").strip()
        if not ok or not name:
            return
        self._specs.styles[name] = pf.PlotStyle.from_dict(
            self._current_style().to_dict())
        self._current_spec().style = name
        self._reload_style_combo()
        self._persist_specs()
        self.statusBar().showMessage(f"Style '{name}' saved")

    def _set_default_style(self) -> None:
        name = self.style_combo.currentData()
        if not name:
            return
        self._specs.default_style = name
        self._persist_specs()
        self.statusBar().showMessage(
            f"'{name}' is now the project default style")

    def _restore_defaults(self) -> None:
        """Reset the current plot's spec to its defaults (content only —
        shared styles are never touched), pointing it at the project default
        style."""
        if not self._loaded:
            return
        spec = pf.default_spec(self._plot_id, self._region1())
        spec.style = self._specs.default_style
        self._specs.plots[self._plot_id] = spec
        self._reload_style_combo()
        self._load_controls()
        self._schedule_render()
        self._schedule_save()
        self.statusBar().showMessage(
            f"{pf.PLOT_TYPES[self._plot_id]['display']} reset to defaults")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._save_timer.stop()
        if self._loaded:
            self._read_controls()
            self._persist_specs()
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setDesktopFileName("pytrack-plots")
    app.setWindowIcon(app_icon())
    mode = ui_settings.get("theme", "auto")
    apply_theme(app, mode=mode)

    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = PlotEditorWindow(initial_path=initial)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
