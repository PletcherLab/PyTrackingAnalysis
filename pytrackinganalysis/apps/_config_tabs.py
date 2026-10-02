"""Form-based editors for each top-level YAML section.

Ported verbatim from ``config_ui.py`` so the visual layout matches the
legacy editor.  The wrapping :class:`~pytrackinganalysis.apps.config_editor.ConfigEditorWindow`
is responsible for the surrounding pyflic-style chrome (TopBar, Cards,
YAML preview, Script Editor launcher).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import Parameters as _ParametersMod
from .. import experiment_types as _et
from ..help import HelpButton


# (display_label, yaml/enum_value). The dropdowns show the label; we read and
# write the value so the YAML stays unchanged.
TRACKING_TYPES: list[tuple[str, str]] = [
    ("Tracker", "TRACKER"),
    ("Two Choice Tracker", "TWOCHOICETRACKER"),
    ("X Choice Tracker", "XCHOICETRACKER"),
    ("D-Drop Tracker", "DDROPTRACKER"),
    ("Pairwise Interaction Tracker", "PAIRWISEINTERACTIONTRACKER"),
    ("Centrophobism Tracker", "CENTROPHOBISMTRACKER"),
    ("Counter", "COUNTER"),
    ("Two Choice Counter", "TWOCHOICECOUNTER"),
    ("Pairwise Interaction Counter", "PAIRWISEINTERACTIONCOUNTER"),
]

TRACKING_RIGS: list[tuple[str, str]] = [
    ("Small Arena", "small_arena"),
    ("Arena Max", "arena_max"),
    ("Colosseum", "colosseum"),
    ("Obscura", "obscura"),
    ("Movie", "movie"),
]

_RIG_LABELS: dict[str, str] = {value: label for label, value in TRACKING_RIGS}

# mm_per_pixel calibrations, read from the single source of truth in Parameters so
# the editor's placeholders cannot drift from what the analysis actually applies.
# A movie has no preset — the user must supply its calibration.
RIG_MM_PER_PIXEL: dict[str, float | None] = {
    **_ParametersMod.RIG_MM_PER_PIXEL,
    "movie": None,
}


#: Widest a Parameter-override field gets. Every value there is a few
#: characters; the placeholders ("required for 'movie' rig") set the floor.
_OVERRIDE_FIELD_WIDTH = 200


def _parse_cutoffs(text: str) -> list[int]:
    """Parse the facet-cutoff field into whole minutes.

    ``validation_errors`` and ``dump`` must agree on what is acceptable. They
    used to disagree — ``float()`` in the first, ``int()`` in the second — so a
    fractional cutoff passed validation and was then silently discarded, taking
    the previously saved value with it. Raises ``ValueError`` on bad input.
    """
    return [int(part.strip()) for part in text.split(",") if part.strip()]


def _parse_labels(text: str) -> list[str]:
    """Parse the phase-names field into a list of names (blank entries dropped)."""
    return [part.strip() for part in text.split(",") if part.strip()]


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    font = QFont()
    font.setBold(True)
    lbl.setFont(font)
    return lbl


def _find_data(combo: QComboBox, value: str) -> int:
    """Locate an item by its userData (case-insensitive fallback for legacy YAML)."""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            return i
    needle = str(value).lower()
    for i in range(combo.count()):
        if str(combo.itemData(i)).lower() == needle:
            return i
    return -1


class _NoScrollComboBox(QComboBox):
    """QComboBox that ignores wheel events so the surrounding table scrolls
    instead of accidentally changing the selection when the user spins the
    wheel over the regions grid."""

    def wheelEvent(self, event):  # noqa: N802 — Qt API
        event.ignore()


def _style_label_combo(combo: QComboBox) -> None:
    """Make a combo's closed text left-aligned and wide enough for its longest item.

    qdarktheme's style draws non-editable combo text in a way that ignores
    ``text-align: left`` and right-justifies long values. Switching to an
    editable + read-only combo lets the internal QLineEdit honour alignment.
    """
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.setEditable(True)
    le = combo.lineEdit()
    le.setReadOnly(True)
    le.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    # Strip the line-edit chrome so the combo still looks like a combo.
    le.setStyleSheet("background: transparent; border: none;")
    le.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class GlobalTab(QWidget):
    # Emitted whenever the experimental-design factors change (name/levels
    # edited, row added/removed). Payload: {factor_name: [levels]}.
    factorsChanged = pyqtSignal(dict)
    # Emitted when the selected Experiment Type changes (including on load).
    experimentTypeChanged = pyqtSignal()
    # Emitted (with the canonical rig name) whenever the rig selection changes.
    rigChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # Calibration keys the user (or the file) supplied explicitly, as opposed
        # to values this tab auto-filled for a rig. Changing the rig dropdown may
        # only discard the latter — see _on_rig_changed.
        self._explicit_overrides: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        help_row = QHBoxLayout()
        help_row.addStretch(1)
        help_row.addWidget(
            HelpButton("config_global", tooltip="Global settings help")
        )
        outer.addLayout(help_row)

        # Experiment type selector (Custom + registered types). Choosing a type
        # constrains the rig and fixes+hides the fields the type owns (ADR-0001).
        # The signal is connected at the end of __init__, after the widgets it
        # manipulates exist, so populating the combo here does not fire it early.
        self.experiment_type = QComboBox()
        for _t in _et.available_experiment_types():
            self.experiment_type.addItem(_t.display_name, _t.name)
        _style_label_combo(self.experiment_type)
        et_row = QHBoxLayout()
        et_row.addWidget(QLabel("Experiment type:"))
        et_row.addWidget(self.experiment_type, 1)
        outer.addLayout(et_row)
        self._type_summary = QLabel("")
        self._type_summary.setWordWrap(True)
        self._type_summary.setStyleSheet("color: palette(mid); font-style: italic;")
        outer.addWidget(self._type_summary)
        outer.addSpacing(8)

        self.tracking_type = QComboBox()
        for label, value in TRACKING_TYPES:
            self.tracking_type.addItem(label, value)
        _style_label_combo(self.tracking_type)

        self.tracking_rig = QComboBox()
        for label, value in TRACKING_RIGS:
            self.tracking_rig.addItem(label, value)
        self.tracking_rig.currentIndexChanged.connect(
            lambda _i: self._on_rig_changed(self.tracking_rig.currentData())
        )
        _style_label_combo(self.tracking_rig)

        # Type on the left, rig on the right, sharing one row.
        type_rig_row = QHBoxLayout()
        type_rig_row.addWidget(QLabel("Tracking type:"))
        type_rig_row.addWidget(self.tracking_type, 1)
        type_rig_row.addSpacing(24)
        type_rig_row.addWidget(QLabel("Tracking rig:"))
        type_rig_row.addWidget(self.tracking_rig, 1)
        outer.addLayout(type_rig_row)

        outer.addSpacing(12)
        outer.addWidget(_section_label("Experimental design factors"))

        self.factors_table = QTableWidget(0, 2)
        self.factors_table.setHorizontalHeaderLabels(["Factor name", "Levels (comma-separated)"])
        self.factors_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.factors_table.setMaximumHeight(160)
        self.factors_table.itemChanged.connect(self._emit_factors_changed)
        m = self.factors_table.model()
        m.rowsInserted.connect(self._emit_factors_changed)
        m.rowsRemoved.connect(self._emit_factors_changed)
        outer.addWidget(self.factors_table)

        btn_row = QHBoxLayout()
        add_factor_btn = QPushButton("Add factor")
        add_factor_btn.clicked.connect(self._add_factor_row)
        rm_factor_btn = QPushButton("Remove selected")
        rm_factor_btn.clicked.connect(self._remove_factor_row)
        btn_row.addWidget(add_factor_btn)
        btn_row.addWidget(rm_factor_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        outer.addSpacing(12)
        outer.addWidget(_section_label("Facet cutoffs (optional)"))

        facet_row = QHBoxLayout()
        self.use_facets = QCheckBox("Enable faceted analysis")
        self.use_facets.stateChanged.connect(self._on_facet_toggled)
        self.facet_cutoffs = QLineEdit()
        self.facet_cutoffs.setPlaceholderText("e.g. 10, 70")
        self.facet_cutoffs.setEnabled(False)
        facet_row.addWidget(self.use_facets)
        facet_row.addWidget(QLabel("Cutoffs (minutes):"))
        facet_row.addWidget(self.facet_cutoffs)
        outer.addLayout(facet_row)

        # Phase names: one per window (cutoffs + 1). Only meaningful when
        # faceting is on, so the field follows the checkbox's enabled state.
        label_row = QHBoxLayout()
        self._facet_labels_caption = QLabel("Phase names (optional):")
        self.facet_labels = QLineEdit()
        self.facet_labels.setPlaceholderText("e.g. Acclimation, Experiment, Cooldown")
        self.facet_labels.setEnabled(False)
        label_row.addWidget(self._facet_labels_caption)
        label_row.addWidget(self.facet_labels)
        outer.addLayout(label_row)

        # Quality criteria (ADR-0003): only shown for a type that has them
        # (Valence). Kept next to the facets because both are measured over
        # facet windows (the Primary Phase / the first phase).
        outer.addSpacing(12)
        self._exclusion_section = _section_label("Quality criteria")
        outer.addWidget(self._exclusion_section)
        excl_row = QHBoxLayout()
        self._min_transitions_caption = QLabel(
            "min_transitions (fewer during the primary phase excludes the fly; 0 = off):")
        self.min_transitions = QLineEdit()
        self.min_transitions.setPlaceholderText("e.g. 5")
        self.min_transitions.setMaximumWidth(80)
        excl_row.addWidget(self._min_transitions_caption)
        excl_row.addWidget(self.min_transitions)
        excl_row.addStretch()
        outer.addLayout(excl_row)

        flag_row = QHBoxLayout()
        self._min_movement_caption = QLabel(
            "min_movement (mm/min; slower flies in the first phase are flagged, not removed; 0 = off):")
        self.min_movement = QLineEdit()
        self.min_movement.setPlaceholderText("e.g. 140")
        self.min_movement.setMaximumWidth(80)
        flag_row.addWidget(self._min_movement_caption)
        flag_row.addWidget(self.min_movement)
        flag_row.addStretch()
        outer.addLayout(flag_row)

        outer.addSpacing(12)
        outer.addWidget(_section_label("Parameter overrides (leave blank to use rig defaults)"))

        ## Two columns of narrow fields, not one column of full-width ones:
        ## every value here is a handful of characters, and a text box the
        ## width of the window said otherwise while pushing the section to
        ## twice the height it needs.
        left = QFormLayout()
        right = QFormLayout()
        for form in (left, right):
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.fps = QLineEdit()
        self.fps.setPlaceholderText("required for 'movie' rig")
        # textEdited (not textChanged) fires only for typing, so programmatic
        # setText during load/rig-change does not falsely mark an override.
        self.fps.textEdited.connect(lambda t: self._mark_override("fps", t))
        left.addRow("fps:", self.fps)

        self.mm_per_pixel = QLineEdit()
        self.mm_per_pixel.setPlaceholderText("required for 'movie' rig")
        self.mm_per_pixel.textEdited.connect(
            lambda t: self._mark_override("mm_per_pixel", t)
        )
        left.addRow("mm_per_pixel:", self.mm_per_pixel)

        self.speed_window = QLineEdit()
        self.speed_window.setPlaceholderText("default 1")
        left.addRow("speed_window_seconds:", self.speed_window)

        self.micromove_min = QLineEdit()
        self.micromove_min.setPlaceholderText("default 0.2")
        self.micromove_max = QLineEdit()
        self.micromove_max.setPlaceholderText("default 2")
        mmrow = QHBoxLayout()
        mmrow.setContentsMargins(0, 0, 0, 0)
        mmrow.addWidget(self.micromove_min)
        mmrow.addWidget(QLabel("–"))
        mmrow.addWidget(self.micromove_max)
        left.addRow("micromove_speed_mm_sec [min–max]:", mmrow)

        self.walking_speed = QLineEdit()
        self.walking_speed.setPlaceholderText("default 2")
        right.addRow("walking_speed_mm_sec:", self.walking_speed)

        self.sleep_threshold = QLineEdit()
        self.sleep_threshold.setPlaceholderText("default 5")
        right.addRow("sleep_threshold_min:", self.sleep_threshold)

        self.interaction_distances = QLineEdit()
        self.interaction_distances.setPlaceholderText("e.g. 8  (pairwise only)")
        right.addRow("interaction_distances (mm):", self.interaction_distances)

        for field in (self.fps, self.mm_per_pixel, self.speed_window,
                      self.walking_speed, self.sleep_threshold,
                      self.interaction_distances):
            field.setMaximumWidth(_OVERRIDE_FIELD_WIDTH)
        for field in (self.fps, self.mm_per_pixel,
                      self.interaction_distances):
            ## These three carry a placeholder that says something ("0.145
            ## (Arena Max)"), so they get the room to say it.
            field.setMinimumWidth(_OVERRIDE_FIELD_WIDTH)
        for field in (self.micromove_min, self.micromove_max):
            ## Two to a row, so half the width each (less the dash).
            field.setMaximumWidth(_OVERRIDE_FIELD_WIDTH // 2)

        pcols = QHBoxLayout()
        pcols.addLayout(left)
        pcols.addSpacing(24)
        pcols.addLayout(right)
        ## The columns keep their natural width; the leftover goes here rather
        ## than stretching the fields back out.
        pcols.addStretch(1)
        outer.addLayout(pcols)
        outer.addStretch()

        # Apply initial placeholders for the default-selected rig
        # (currentIndexChanged does not fire for the initial selection).
        self._on_rig_changed(self.tracking_rig.currentData())

        # Now that every widget exists, wire the experiment-type selector and
        # apply the initial locking for the default (Custom) selection.
        self.experiment_type.currentIndexChanged.connect(
            lambda _i: self._on_experiment_type_changed()
        )
        self._on_experiment_type_changed()

    def current_experiment_type(self):
        """The selected ``ExperimentType`` instance."""
        return _et.get_experiment_type(self.experiment_type.currentData())

    def _set_rig_options(self, rigs) -> None:
        """Rebuild the rig combo to *rigs* (canonical names), keeping the current
        selection if it is still allowed."""
        keep = self.tracking_rig.currentData()
        self.tracking_rig.blockSignals(True)
        self.tracking_rig.clear()
        for value in rigs:
            self.tracking_rig.addItem(_RIG_LABELS.get(value, value), value)
        idx = _find_data(self.tracking_rig, keep)
        self.tracking_rig.setCurrentIndex(idx if idx >= 0 else 0)
        self.tracking_rig.blockSignals(False)
        self._on_rig_changed(self.tracking_rig.currentData())

    def _on_experiment_type_changed(self) -> None:
        """Apply the selected type's constraints: fix+disable owned fields,
        constrain the rig, and show a one-line type summary. Custom restores the
        full, unconstrained form."""
        t = self.current_experiment_type()
        custom = t.is_custom

        # Tracking type: fixed and disabled for a typed experiment.
        if not custom:
            idx = _find_data(self.tracking_type, t.tracking_type.name)
            if idx >= 0:
                self.tracking_type.setCurrentIndex(idx)
        self.tracking_type.setEnabled(custom)

        # Rig: constrained to the type's allowed set, else the full list.
        allowed = list(t.allowed_rigs) if t.allowed_rigs is not None \
            else [value for _label, value in TRACKING_RIGS]
        self._set_rig_options(allowed)

        # Facets: a type may fix them (disabled) or only default them (editable).
        facets_owned = "facet_cutoffs" in t.owned_keys()
        if t.facet_cutoffs is not None:
            self.use_facets.setChecked(True)
            self.facet_cutoffs.setText(", ".join(str(c) for c in t.facet_cutoffs))
            if t.phase_labels:
                self.facet_labels.setText(", ".join(t.phase_labels))
        self.use_facets.setEnabled(not facets_owned)
        self.facet_cutoffs.setEnabled(not facets_owned and self.use_facets.isChecked())
        self.facet_labels.setEnabled(self.use_facets.isChecked())

        # Quality criteria: each row visible only for a type that has the
        # criterion, pre-filled with the type default when empty.
        has_exclusion = t.default_min_transitions is not None
        has_flag = t.default_min_movement is not None
        self._exclusion_section.setVisible(has_exclusion or has_flag)
        for w in (self._min_transitions_caption, self.min_transitions):
            w.setVisible(has_exclusion)
        for w in (self._min_movement_caption, self.min_movement):
            w.setVisible(has_flag)
        if has_exclusion and not self.min_transitions.text().strip():
            self.min_transitions.setText(str(t.default_min_transitions))
        elif not has_exclusion:
            self.min_transitions.clear()
        if has_flag and not self.min_movement.text().strip():
            self.min_movement.setText(f"{t.default_min_movement:g}")
        elif not has_flag:
            self.min_movement.clear()

        # Calibration overrides: disabled when the type forbids them.
        if not custom and not t.allow_calibration_override:
            for w in (self.fps, self.mm_per_pixel):
                w.clear()
                w.setEnabled(False)

        # Summary chip for the owned/derived fields the user can no longer edit.
        if custom:
            self._type_summary.setText("")
        else:
            phases = " · ".join(t.phase_labels) if t.phase_labels else "—"
            self._type_summary.setText(
                f"Fixed by {t.display_name}: tracking type "
                f"{t.tracking_type.name}; rig one of "
                f"{', '.join(t.allowed_rigs)}. Default phases: {phases}."
            )

        self.experimentTypeChanged.emit()

    def _mark_override(self, key: str, text: str) -> None:
        """Record (or forget) a calibration the user typed by hand."""
        if text.strip():
            self._explicit_overrides.add(key)
        else:
            self._explicit_overrides.discard(key)

    def _on_rig_changed(self, rig: str) -> None:
        movie = rig == "movie"
        if not movie:
            # Only a value this tab auto-filled may be discarded. A calibration
            # the user typed — or that came out of the file — is a deliberate
            # override: clearing it here dropped the key on the next save and
            # silently reverted the analysis to the rig preset.
            for key, widget in (("fps", self.fps), ("mm_per_pixel", self.mm_per_pixel)):
                if key not in self._explicit_overrides:
                    widget.clear()
        # A surviving override stays editable so the user can still revise or
        # remove it without switching the rig back to Movie.
        self.fps.setEnabled(movie or bool(self.fps.text().strip()))
        self.mm_per_pixel.setEnabled(movie or bool(self.mm_per_pixel.text().strip()))

        rig_label = _RIG_LABELS.get(rig, rig)
        if movie:
            self.fps.setPlaceholderText(f"required for '{rig_label}' rig")
            self.mm_per_pixel.setPlaceholderText(f"required for '{rig_label}' rig")
        else:
            self.fps.setPlaceholderText("auto-detected from data")
            mm = RIG_MM_PER_PIXEL.get(rig)
            self.mm_per_pixel.setPlaceholderText(
                f"{mm} ({rig_label} default)" if mm is not None else ""
            )

        self.rigChanged.emit(rig or "")

    def _on_facet_toggled(self, state) -> None:
        self.facet_cutoffs.setEnabled(bool(state))
        # Names only make sense when there are phases to name; the cutoffs may
        # be fixed by the type, but the names always stay the user's to edit.
        self.facet_labels.setEnabled(bool(state))

    def _add_factor_row(self) -> None:
        r = self.factors_table.rowCount()
        self.factors_table.insertRow(r)

    def _remove_factor_row(self) -> None:
        rows = {idx.row() for idx in self.factors_table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self.factors_table.removeRow(r)

    def get_factors(self) -> dict[str, list[str]]:
        """Return the current factor → levels mapping (in row order)."""
        result: dict[str, list[str]] = {}
        for r in range(self.factors_table.rowCount()):
            name_item = self.factors_table.item(r, 0)
            levels_item = self.factors_table.item(r, 1)
            name = name_item.text().strip() if name_item else ""
            if not name:
                continue
            levels = [
                lvl.strip()
                for lvl in (levels_item.text() if levels_item else "").split(",")
                if lvl.strip()
            ]
            result[name] = levels
        return result

    def _emit_factors_changed(self, *_args) -> None:
        self.factorsChanged.emit(self.get_factors())

    def load(self, config: dict) -> None:
        g = config.get("global", {})

        # Experiment type first — setting it applies the field constraints, so
        # the owned fields below are already fixed/disabled for a typed config.
        t = _et.get_experiment_type(g.get("experiment_type"))
        idx = _find_data(self.experiment_type, t.name)
        self.experiment_type.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_experiment_type_changed()  # idempotent; covers "index unchanged"

        # Tracking type: from the yaml only for a Custom experiment; a typed one
        # takes it from the type (already set above, and disabled).
        if t.tracking_type is None:
            tt = g.get("tracking_type", "TRACKER")
            self.tracking_type.setCurrentIndex(max(_find_data(self.tracking_type, tt), 0))

        # A calibration present in the file is an explicit choice, whatever the
        # rig says; record it before the rig change can decide to clear it.
        self._explicit_overrides = {
            key for key in ("fps", "mm_per_pixel")
            if str(g.get(key, "")).strip()
        }

        rig = g.get("tracking_rig", "")
        self.tracking_rig.setCurrentIndex(max(_find_data(self.tracking_rig, rig), 0))

        self.factors_table.setRowCount(0)
        for name, levels in g.get("experimental_design_factors", {}).items():
            r = self.factors_table.rowCount()
            self.factors_table.insertRow(r)
            self.factors_table.setItem(r, 0, QTableWidgetItem(name))
            self.factors_table.setItem(r, 1, QTableWidgetItem(", ".join(str(l) for l in levels)))

        # Facets: read from the yaml unless the type fixes them (owns the key).
        # A type may still supply a default when the yaml has none (e.g. Valence).
        if "facet_cutoffs" not in t.owned_keys():
            cutoffs = g.get("facet_cutoffs")
            if cutoffs is None and t.facet_cutoffs is not None:
                cutoffs = list(t.facet_cutoffs)
            if cutoffs:
                self.use_facets.setChecked(True)
                self.facet_cutoffs.setText(", ".join(str(c) for c in cutoffs))
            else:
                self.use_facets.setChecked(False)
                self.facet_cutoffs.clear()

        # Quality criteria: the yaml's values, else the type defaults.
        if t.default_min_transitions is not None:
            self.min_transitions.setText(
                str(g.get("min_transitions", t.default_min_transitions)))
        else:
            self.min_transitions.clear()
        if t.default_min_movement is not None:
            self.min_movement.setText(
                f"{float(g.get('min_movement', t.default_min_movement)):g}")
        else:
            self.min_movement.clear()

        # Phase names: the yaml's, else the type's defaults when they name every
        # window of the effective cutoffs; else cleared (never carried over from
        # a previously loaded file).
        labels = g.get("facet_labels")
        if labels:
            self.facet_labels.setText(", ".join(str(l) for l in labels))
        else:
            effective = g.get("facet_cutoffs")
            if effective is None and t.facet_cutoffs is not None:
                effective = list(t.facet_cutoffs)
            if (self.use_facets.isChecked() and effective and t.phase_labels
                    and len(t.phase_labels) == len(effective) + 1):
                self.facet_labels.setText(", ".join(t.phase_labels))
            else:
                self.facet_labels.clear()

        self.fps.setText(str(g["fps"]) if "fps" in g else "")
        self.mm_per_pixel.setText(str(g["mm_per_pixel"]) if "mm_per_pixel" in g else "")
        self.speed_window.setText(str(g["speed_window_seconds"]) if "speed_window_seconds" in g else "")
        mm = g.get("micromove_speed_mm_sec")
        if mm and len(mm) == 2:
            self.micromove_min.setText(str(mm[0]))
            self.micromove_max.setText(str(mm[1]))
        self.walking_speed.setText(str(g["walking_speed_mm_sec"]) if "walking_speed_mm_sec" in g else "")
        self.sleep_threshold.setText(str(g["sleep_threshold_min"]) if "sleep_threshold_min" in g else "")
        idist = g.get("interaction_distances")
        if idist:
            self.interaction_distances.setText(", ".join(str(d) for d in idist))

        # Refresh the calibration placeholders / enabled state last, so it sees
        # the values just loaded rather than the previous file's. Doing it here
        # also covers the case where the rig index did not change and
        # currentIndexChanged therefore never fired.
        self._on_rig_changed(self.tracking_rig.currentData())

    # Keys this tab owns. The window uses these to tell "the user cleared this
    # field" apart from "this key was hand-added and is none of our business".
    _OWNED_KEYS = (
        "experiment_type",
        "tracking_type",
        "tracking_rig",
        "experimental_design_factors",
        "facet_cutoffs",
        "facet_labels",
        "min_transitions",
        "min_movement",
        "fps",
        "mm_per_pixel",
        "speed_window_seconds",
        "walking_speed_mm_sec",
        "sleep_threshold_min",
        "micromove_speed_mm_sec",
        "interaction_distances",
    )

    def owned_keys(self) -> tuple[str, ...]:
        """The ``global:`` keys this tab is responsible for."""
        return self._OWNED_KEYS

    def validation_errors(self) -> list[str]:
        """Field-level problems that must be fixed before the config can be saved.

        A malformed number used to be dropped on the floor by ``dump()``, so the
        value silently vanished from the saved file and the analysis quietly ran
        with a rig default instead.
        """
        errors: list[str] = []

        def _check_number(label, text, *, positive=False):
            text = text.strip()
            if not text:
                return
            try:
                value = float(text)
            except ValueError:
                errors.append(f"{label}: '{text}' is not a number")
                return
            if positive and value <= 0:
                errors.append(f"{label}: must be greater than zero")

        _check_number("FPS", self.fps.text())
        _check_number("mm per pixel", self.mm_per_pixel.text(), positive=True)
        _check_number("Speed window (s)", self.speed_window.text(), positive=True)
        _check_number("Walking speed (mm/s)", self.walking_speed.text())
        _check_number("Sleep threshold (min)", self.sleep_threshold.text())

        mn, mx = self.micromove_min.text().strip(), self.micromove_max.text().strip()
        if bool(mn) != bool(mx):
            errors.append("Micro-move speed: set both the minimum and the maximum, or neither")
        elif mn and mx:
            try:
                if float(mn) >= float(mx):
                    errors.append("Micro-move speed: the minimum must be below the maximum")
            except ValueError:
                errors.append(f"Micro-move speed: '{mn}, {mx}' is not a pair of numbers")

        if self.use_facets.isChecked():
            text = self.facet_cutoffs.text().strip()
            if not text:
                errors.append("Facet cutoffs: enter at least one minute boundary, or untick faceting")
            else:
                try:
                    # dump() writes whole minutes, so validation has to accept
                    # exactly what dump() can serialise. Parsing with float()
                    # here let '10.5' through, and dump() then dropped the key.
                    values = _parse_cutoffs(text)
                except ValueError:
                    errors.append(
                        f"Facet cutoffs: '{text}' must be whole minutes, comma-separated (e.g. 10, 70)"
                    )
                else:
                    if sorted(values) != values:
                        errors.append("Facet cutoffs: values must increase from left to right")
                    if len(set(values)) != len(values):
                        errors.append("Facet cutoffs: values must be distinct")
                    names = _parse_labels(self.facet_labels.text())
                    if names and len(names) != len(values) + 1:
                        errors.append(
                            f"Phase names: {len(values)} cutoffs create "
                            f"{len(values) + 1} phases, so give {len(values) + 1} "
                            f"names (or none) — got {len(names)}")

        idist = self.interaction_distances.text().strip()
        if idist:
            try:
                [float(x.strip()) for x in idist.split(",") if x.strip()]
            except ValueError:
                errors.append(f"Interaction distances: '{idist}' is not a comma-separated list of numbers")

        t = self.current_experiment_type()
        if t.default_min_transitions is not None:
            text = self.min_transitions.text().strip()
            if text:
                try:
                    value = float(text)
                    if value < 0 or value != int(value):
                        raise ValueError
                except ValueError:
                    errors.append(
                        f"min_transitions: '{text}' must be a whole number ≥ 0 "
                        f"(0 turns the exclusion off)")
        if t.default_min_movement is not None:
            text = self.min_movement.text().strip()
            if text:
                try:
                    if float(text) < 0:
                        raise ValueError
                except ValueError:
                    errors.append(
                        f"min_movement: '{text}' must be a number ≥ 0 in mm/min "
                        f"(0 turns the flagging off)")

        if self.tracking_rig.currentData() == "movie":
            for label, widget in (("FPS", self.fps), ("mm per pixel", self.mm_per_pixel)):
                if not widget.text().strip():
                    errors.append(f"{label}: required when the rig is Movie (there is no preset to fall back on)")

        return errors

    def dump(self) -> dict:
        g = {
            "tracking_type": self.tracking_type.currentData(),
            "tracking_rig": self.tracking_rig.currentData(),
        }

        factors: dict[str, list[str]] = {}
        for r in range(self.factors_table.rowCount()):
            name_item = self.factors_table.item(r, 0)
            levels_item = self.factors_table.item(r, 1)
            if name_item and name_item.text().strip():
                levels = [
                    l.strip()
                    for l in (levels_item.text() if levels_item else "").split(",")
                    if l.strip()
                ]
                factors[name_item.text().strip()] = levels
        if factors:
            g["experimental_design_factors"] = factors

        if self.use_facets.isChecked() and self.facet_cutoffs.text().strip():
            # No `except ValueError: pass` here. validation_errors() uses the
            # same parser and blocks the save, so anything that reaches this
            # point parses; swallowing the error instead deleted the key.
            g["facet_cutoffs"] = _parse_cutoffs(self.facet_cutoffs.text())
            names = _parse_labels(self.facet_labels.text())
            if names:
                g["facet_labels"] = names

        def _float(w):
            t = w.text().strip()
            return float(t) if t else None

        for key, fn, widget in [
            ("fps", _float, self.fps),
            ("mm_per_pixel", _float, self.mm_per_pixel),
            ("speed_window_seconds", _float, self.speed_window),
            ("walking_speed_mm_sec", _float, self.walking_speed),
            ("sleep_threshold_min", _float, self.sleep_threshold),
        ]:
            v = fn(widget)
            if v is not None:
                g[key] = v

        mn, mx = self.micromove_min.text().strip(), self.micromove_max.text().strip()
        if mn and mx:
            try:
                g["micromove_speed_mm_sec"] = [float(mn), float(mx)]
            except ValueError:
                pass

        idist = self.interaction_distances.text().strip()
        if idist:
            try:
                g["interaction_distances"] = [
                    float(x.strip()) for x in idist.split(",") if x.strip()
                ]
            except ValueError:
                pass

        # Experiment Type: a typed config names its type and omits the fields the
        # type owns (they are derived, ADR-0001). A Custom config carries no
        # experiment_type key at all (absence == Custom), so existing files are
        # unchanged.
        t = self.current_experiment_type()
        if t.is_custom:
            g.pop("experiment_type", None)
        else:
            g["experiment_type"] = t.name
            for key in t.owned_keys():
                g.pop(key, None)

        ## Quality criteria: written only for a type that has them. An empty
        ## field falls back to the type default at load, so nothing is written
        ## for it.
        if t.default_min_transitions is not None:
            text = self.min_transitions.text().strip()
            if text:
                try:
                    g["min_transitions"] = int(float(text))
                except ValueError:
                    pass  # validation_errors() blocks the save first
        if t.default_min_movement is not None:
            text = self.min_movement.text().strip()
            if text:
                try:
                    value = float(text)
                    g["min_movement"] = int(value) if value == int(value) else value
                except ValueError:
                    pass  # validation_errors() blocks the save first

        return {"global": g}


class TrackingRegionsTab(QWidget):
    """Regions table with one dropdown column per experimental-design factor.

    Columns are rebuilt whenever ``GlobalTab.factorsChanged`` fires. The on-disk
    YAML format is unchanged (``experimental_factors`` is a comma-joined string);
    we map each token to whichever factor's levels it belongs to on load, and
    re-join in factor declaration order on dump.
    """

    _FIXED_LEFT = 1   # "Region name"
    _FIXED_RIGHT = 2  # "X multiplier", "Y multiplier"

    def __init__(self, global_tab: GlobalTab):
        super().__init__()
        self._global_tab = global_tab
        # Current factor → levels mapping driving the dynamic columns.
        self._factors: dict[str, list[str]] = {}
        # The tracking_regions section as loaded, keyed by region name. dump()
        # merges over these so per-region keys this tab does not render
        # (``notes:``, anything hand-added) survive a load/save round trip.
        self._loaded_regions: dict[str, dict] = {}

        layout = QVBoxLayout(self)

        info_row = QHBoxLayout()
        info = QLabel(
            "Each row is one tracking region (tube/well). "
            "Pick a level for every experimental-design factor defined on the Global tab."
        )
        info.setWordWrap(True)
        info_row.addWidget(info, 1)
        info_row.addWidget(
            HelpButton("config_regions", tooltip="Tracking and counting regions")
        )
        layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add region")
        add_btn.clicked.connect(lambda: self._add_row())
        rm_btn = QPushButton("Remove selected")
        rm_btn.clicked.connect(self._remove_row)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(24)
        bulk_btn = QPushButton("Generate N regions")
        bulk_btn.clicked.connect(self._bulk_generate)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        btn_row.addWidget(QLabel("N:"))
        btn_row.addWidget(self.count_spin)
        btn_row.addWidget(bulk_btn)
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, self._FIXED_LEFT + self._FIXED_RIGHT)
        layout.addWidget(self.table)

        # Pull initial factor list (will be empty until GlobalTab loads), then
        # listen for changes so columns track factor edits live.
        self._factors = self._global_tab.get_factors()
        self._apply_columns()
        self._global_tab.factorsChanged.connect(self._on_factors_changed)

    # ------------------------------------------------------------------
    # Column management
    # ------------------------------------------------------------------

    def _factor_names(self) -> list[str]:
        return list(self._factors.keys())

    def _x_col(self) -> int:
        return self._FIXED_LEFT + len(self._factors)

    def _y_col(self) -> int:
        return self._x_col() + 1

    def _apply_columns(self) -> None:
        """Set the table headers + resize modes for the current factor list."""
        headers = ["Region name"] + self._factor_names() + ["X multiplier", "Y multiplier"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for i in range(len(self._factors)):
            h.setSectionResizeMode(self._FIXED_LEFT + i, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self._x_col(), QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self._y_col(), QHeaderView.ResizeMode.ResizeToContents)

    def _on_factors_changed(self, factors: dict[str, list[str]]) -> None:
        """Rebuild columns + cells when the Global tab's factors change."""
        # Snapshot using the *old* factor list, then swap and re-render.
        snapshot = self._snapshot_rows()
        self._factors = factors
        self._apply_columns()
        self.table.setRowCount(0)
        for row in snapshot:
            self._add_row(
                name=row["name"],
                factor_values=row["factors"],
                x=row["x"],
                y=row["y"],
                extra_factors=row["extras"],
            )

    def _snapshot_rows(self) -> list[dict]:
        """Capture current row state, keyed by factor name (so renames drop cleanly)."""
        rows: list[dict] = []
        prev_factor_names = self._factor_names()
        prev_x = self._FIXED_LEFT + len(prev_factor_names)
        prev_y = prev_x + 1
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            name = name_item.text() if name_item else ""
            factor_values: dict[str, str] = {}
            for i, fname in enumerate(prev_factor_names):
                w = self.table.cellWidget(r, self._FIXED_LEFT + i)
                if w is not None:
                    factor_values[fname] = w.currentText()
            xw = self.table.cellWidget(r, prev_x)
            yw = self.table.cellWidget(r, prev_y)
            rows.append({
                "name": name,
                "factors": factor_values,
                "extras": list(name_item.data(Qt.ItemDataRole.UserRole) or []) if name_item else [],
                "x": xw.currentText() if xw else "1",
                "y": yw.currentText() if yw else "1",
            })
        return rows

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    def _add_row(
        self,
        name: str = "",
        factor_values: dict[str, str] | None = None,
        x: str | int = 1,
        y: str | int = 1,
        extra_factors: list[str] | None = None,
    ) -> None:
        ## None means a brand-new row (Add, Generate, a rig's plate): give it
        ## the design's only treatment when there is just one. A row being
        ## loaded or re-rendered passes a dict and keeps exactly what it had —
        ## a blank there may be a deliberately empty well.
        if factor_values is None:
            factor_values = _et.sole_treatment(self._factors)
        r = self.table.rowCount()
        self.table.insertRow(r)
        name_item = QTableWidgetItem(name or f"T_{r}")
        # Factor tokens with no column of their own ride along on the row so
        # they survive both a column rebuild and the save.
        name_item.setData(Qt.ItemDataRole.UserRole, list(extra_factors or []))
        self.table.setItem(r, 0, name_item)
        for i, (fname, levels) in enumerate(self._factors.items()):
            combo = _NoScrollComboBox()
            combo.addItem("")  # blank = unset
            combo.addItems([str(l) for l in levels])
            current = factor_values.get(fname, "")
            idx = combo.findText(current, Qt.MatchFlag.MatchFixedString)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.table.setCellWidget(r, self._FIXED_LEFT + i, combo)
        xc = _NoScrollComboBox()
        xc.addItems(["1", "-1"])
        xc.setCurrentText(str(x))
        yc = _NoScrollComboBox()
        yc.addItems(["1", "-1"])
        yc.setCurrentText(str(y))
        self.table.setCellWidget(r, self._x_col(), xc)
        self.table.setCellWidget(r, self._y_col(), yc)

    def _remove_row(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self.table.removeRow(r)

    def _bulk_generate(self) -> None:
        self.table.setRowCount(0)
        n = self.count_spin.value()
        for i in range(n):
            self._add_row(name=f"T_{i}")

    def region_names(self) -> list[str]:
        """The non-empty region names currently in the table, in order."""
        out = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text().strip():
                out.append(item.text().strip())
        return out

    def set_region_names(self, names, multipliers: dict | None = None) -> None:
        """Replace the table with one blank-treatment row per name in *names*
        (used to lay out an Experiment Type's fixed plate).

        *multipliers* maps a region name to its ``x_location_multiplier`` /
        ``y_location_multiplier`` — the plate's geometry, which is not all +1
        for every rig (Valence on Arena Max flips T_0..T_17). Without it every
        row would come out +1 and the laid-out plate would silently disagree
        with the one ``build_config`` writes."""
        self.table.setRowCount(0)
        for name in names:
            axes = (multipliers or {}).get(name) or {}
            self._add_row(name=name,
                          x=axes.get("x_location_multiplier", 1),
                          y=axes.get("y_location_multiplier", 1))

    # ------------------------------------------------------------------
    # Load / dump
    # ------------------------------------------------------------------

    def _split_factor_string(self, s: str) -> tuple[dict[str, str], list[str]]:
        """Split a comma-list into (factor → level, tokens matching no factor).

        Tokens that are not a level of any declared factor have no column to
        live in, so they used to be silently dropped on the next save
        (``'Starved, Female, Batch1'`` → ``'Starved, Female'``). They are
        returned instead and carried through to :meth:`dump` untouched.
        """
        out: dict[str, str] = {}
        extras: list[str] = []
        if not s:
            return out, extras
        tokens = [t.strip() for t in s.split(",") if t.strip()]
        for tok in tokens:
            for fname, levels in self._factors.items():
                if fname in out:
                    continue  # already filled, don't overwrite
                if tok in levels:
                    out[fname] = tok
                    break
            else:
                extras.append(tok)
        return out, extras

    def load(self, config: dict) -> None:
        # Re-sync factor columns from the current Global tab, in case load
        # order put us ahead of factorsChanged.
        self._factors = self._global_tab.get_factors()
        self._apply_columns()
        self.table.setRowCount(0)
        loaded = config.get("tracking_regions") or {}
        self._loaded_regions = {
            name: dict(data) for name, data in loaded.items() if isinstance(data, dict)
        }
        for name, data in loaded.items():
            if not isinstance(data, dict):
                continue
            ef = data.get("experimental_factors", "")
            factor_values, extras = self._split_factor_string(str(ef))
            self._add_row(
                name=name,
                factor_values=factor_values,
                x=data.get("x_location_multiplier", 1),
                y=data.get("y_location_multiplier", 1),
                extra_factors=extras,
            )

    def dump(self) -> dict:
        regions: dict[str, dict] = {}
        factor_names = self._factor_names()
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            if not (name_item and name_item.text().strip()):
                continue
            parts: list[str] = []
            for i, _fname in enumerate(factor_names):
                w = self.table.cellWidget(r, self._FIXED_LEFT + i)
                val = w.currentText().strip() if w else ""
                if val:
                    parts.append(val)
            parts.extend(name_item.data(Qt.ItemDataRole.UserRole) or [])
            xw = self.table.cellWidget(r, self._x_col())
            yw = self.table.cellWidget(r, self._y_col())
            name = name_item.text().strip()
            # Start from the entry as loaded so keys this tab does not render
            # survive, then overwrite the ones it owns.
            entry = dict(self._loaded_regions.get(name) or {})
            entry.update({
                "experimental_factors": ", ".join(parts),
                "x_location_multiplier": int(xw.currentText()) if xw else 1,
                "y_location_multiplier": int(yw.currentText()) if yw else 1,
            })
            regions[name] = entry
        # Always emit the key. Returning {} made config.update() a no-op, so
        # clearing every row silently resurrected the previous plate's regions.
        return {"tracking_regions": regions}


class CountingRegionsTab(QWidget):
    def __init__(self):
        super().__init__()
        # The counting_regions section as loaded, keyed by treatment label.
        # dump() merges over it so per-region keys this tab does not render
        # (``color:``, anything hand-added) survive a load/save round trip.
        self._loaded_regions: dict[str, dict] = {}

        layout = QVBoxLayout(self)

        info_row = QHBoxLayout()
        info = QLabel(
            "Each row defines a treatment and the strings that identify it in the "
            "tracking data file's counting_region column. Any value in that column "
            "matching one of the aliases (comma-separated) is mapped to the "
            "treatment label. Example: a row with label \"Light\" and aliases "
            "\"Light, LL, L\" assigns the \"Light\" treatment to every data row "
            "whose counting_region is Light, LL, or L."
        )
        info.setWordWrap(True)
        info_row.addWidget(info, 1)
        info_row.addWidget(
            HelpButton("config_regions", tooltip="Tracking and counting regions")
        )
        layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add region")
        add_btn.clicked.connect(self._add_row)
        rm_btn = QPushButton("Remove selected")
        rm_btn.clicked.connect(self._remove_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Treatment label", "Aliases (comma-separated)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

    def _add_row(self, label: str = "", aliases: str = "") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(label))
        self.table.setItem(r, 1, QTableWidgetItem(aliases))

    def labels(self) -> list[str]:
        """The non-empty treatment labels currently in the table."""
        out = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text().strip():
                out.append(item.text().strip())
        return out

    def add_region(self, label: str, aliases: str = "") -> None:
        """Append a counting-region row (used to seed a type's required regions)."""
        self._add_row(label=label, aliases=aliases)

    def _remove_row(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        for r in sorted(rows, reverse=True):
            self.table.removeRow(r)

    def load(self, config: dict) -> None:
        self.table.setRowCount(0)
        loaded = config.get("counting_regions") or {}
        self._loaded_regions = {
            label: dict(data) for label, data in loaded.items() if isinstance(data, dict)
        }
        for label, data in loaded.items():
            if not isinstance(data, dict):
                continue
            self._add_row(label=label, aliases=data.get("alias", ""))

    def dump(self) -> dict:
        regions: dict[str, dict] = {}
        for r in range(self.table.rowCount()):
            label_item = self.table.item(r, 0)
            aliases_item = self.table.item(r, 1)
            if label_item and label_item.text().strip():
                label = label_item.text().strip()
                # Start from the entry as loaded so keys this tab does not
                # render survive, then overwrite the one it owns.
                entry = dict(self._loaded_regions.get(label) or {})
                entry["alias"] = aliases_item.text().strip() if aliases_item else ""
                regions[label] = entry
        # Always emit the key — see TrackingRegionsTab.dump.
        return {"counting_regions": regions}
