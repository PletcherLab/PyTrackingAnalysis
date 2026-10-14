"""The Directional Hypotheses editor, shared by the Config Editor's Global tab
and the Hub's project design dialog — the two places a design is written, so
a prediction can be declared from the app, not only by hand in yaml
(ADR-0017)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import comparisons

CAPTION = (
    "Predict a direction before the data exist and that comparison is tested "
    "one-sided — the Permutation Test, Welch and the mixed model alike. "
    "'Mutant < WT' = mutants prefer Light less. Covers FinalPI and "
    "FinalPercentage, within every level of the other factors; a single "
    "treatment can be stated against indifference ('WT > indifference'). "
    "Phases: blank = the Primary Phase. Everything else stays two-sided.")


def _phases_text(phases) -> str:
    if not phases:
        return ""
    if isinstance(phases, str):
        return phases
    return ", ".join(str(p) for p in phases)


class HypothesesEditor(QWidget):
    """Two-column table (prediction, phases) with Add / Remove buttons.

    :meth:`set_hypotheses` takes the yaml value; :meth:`hypotheses` gives it
    back in the canonical yaml shape (``{expect, phases?}`` per row, blank
    rows dropped) — an unparseable row is kept verbatim so validation, not
    the editor, decides what is wrong with it.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None, caption: bool = True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if caption:
            note = QLabel(CAPTION)
            note.setWordWrap(True)
            note.setStyleSheet("color: #666;")
            lay.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            ["Prediction (e.g. Mutant < WT)",
             "Phases (comma-separated; blank = Primary Phase)"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(120)
        self.table.itemChanged.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add prediction")
        add.clicked.connect(self.add_row)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        lay.addLayout(row)

    # ---- data -----------------------------------------------------------

    def add_row(self, expect: str = "", phases: str = "") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(expect)))
        self.table.setItem(r, 1, QTableWidgetItem(str(phases)))
        self.changed.emit()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.table.removeRow(r)
        if rows:
            self.changed.emit()

    def set_hypotheses(self, raw) -> None:
        """Fill from a yaml ``hypotheses:`` value (None/[] clears)."""
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for entry in raw or []:
                if isinstance(entry, dict):
                    expect, phases = entry.get("expect", ""), entry.get("phases")
                else:
                    expect, phases = entry, None
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 0, QTableWidgetItem(str(expect or "")))
                self.table.setItem(r, 1, QTableWidgetItem(_phases_text(phases)))
        finally:
            self.table.blockSignals(False)
        self.changed.emit()

    def hypotheses(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.table.rowCount()):
            expect_item = self.table.item(r, 0)
            phases_item = self.table.item(r, 1)
            expect = (expect_item.text() if expect_item else "").strip()
            phases = [p.strip() for p in
                      (phases_item.text() if phases_item else "").split(",")
                      if p.strip()]
            if not expect:
                continue
            hyp, problem = comparisons.parse_hypothesis({"expect": expect})
            if hyp is not None and problem is None:
                expect = f"{hyp.low} < {hyp.high}" \
                    if "<" in expect else f"{hyp.high} > {hyp.low}"
            entry: dict = {"expect": expect}
            if phases:
                entry["phases"] = phases
            out.append(entry)
        return out

    def problems(self, factors: dict | None, phase_labels: list[str]) -> list[str]:
        """Why the current predictions are not a valid design (empty = ok)."""
        return comparisons.validate_hypotheses(self.hypotheses(), factors,
                                               phase_labels)
