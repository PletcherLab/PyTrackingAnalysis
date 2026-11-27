"""The QC Viewer's removed regions: one replicate's, ticked while looking at
its data (ADR-0021).

Each tracking region of the replicate's config is a row: ticked when it is
removed, with the number of flies the recording has in it and a reason.
Saving writes only what was changed here into the replicate's
``removed_regions.yaml`` (:func:`removals.update_removals`), so a removal
declared meanwhile in another window is kept. The unit is the region, as
everywhere (ADR-0010): ticking it removes every fly in it.
"""

from __future__ import annotations

import yaml
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import layout, removals

_REMOVE, _REGION, _FLIES, _REASON = range(4)


def config_region_names(experiment_dir) -> list[str]:
    """The tracking regions the replicate's config declares, in its order:
    its own config, or its recording's (ADR-0020). Empty when it cannot be
    read."""
    try:
        with open(layout.config_path(experiment_dir), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return []
    regions = config.get("tracking_regions") if isinstance(config, dict) else None
    return [str(name) for name in regions] if isinstance(regions, dict) else []


class RemovedRegionsPanel(QWidget):
    """Tick the regions of one replicate to remove, say why, and save."""

    #: Emitted after a save with the declaration as it now stands on disk.
    saved = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dir: str | None = None
        self._refusal = ""
        self._regions: list[str] = []
        self._trackers: list[str] = []
        #: The declaration as read, which saving compares the table with.
        self._at_load: dict[str, str] = {}
        #: True while the table is being filled, so that is not an edit.
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Remove", "Region", "Flies", "Reason"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_REASON, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(150)
        self.table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.note = QLabel("(load a project to remove regions)")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(mid); font-size: 9pt;")
        row.addWidget(self.note, 1)
        self.save_button = QPushButton("Save removed regions")
        self.save_button.setToolTip(
            "Write the ticked regions and their reasons to this replicate's "
            "removed_regions.yaml. Every fly in a removed region leaves the "
            "analysis; re-run it for saved results to change.")
        self.save_button.clicked.connect(self.save)
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)
        outer.addLayout(row)

    # ------------------------------------------------------------------

    def load(self, experiment_dir, regions, trackers) -> None:
        """Show the replicate in *experiment_dir*: the *regions* its config
        declares, the *trackers* its recording has, and what it declares
        removed. A declared region its config does not have gets a row too,
        so nothing declared is out of sight."""
        self._dir = str(experiment_dir)
        self._refusal = removals.edit_refusal(self._dir)
        self._regions = [str(name) for name in regions]
        self._trackers = [str(name) for name in trackers]
        self._show(removals.read_removals(self._dir))

    def clear(self) -> None:
        self._dir, self._refusal = None, ""
        self._regions, self._trackers, self._at_load = [], [], {}
        self.table.setRowCount(0)
        self.note.setText("(load a project to remove regions)")
        self.save_button.setEnabled(False)

    def _show(self, declared: dict) -> None:
        self._at_load = dict(declared)
        names = self._regions + [name for name in declared if name not in self._regions]
        flies = removals.expand_regions(names, self._trackers)
        editable = not self._refusal
        self._filling = True
        try:
            self.table.setRowCount(0)
            for name in names:
                row = self.table.rowCount()
                self.table.insertRow(row)
                ticked = name in declared
                check = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsSelectable
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                check.setFlags(flags)
                check.setCheckState(Qt.CheckState.Checked if ticked
                                    else Qt.CheckState.Unchecked)
                self.table.setItem(row, _REMOVE, check)
                count = len(flies.get(name, []))
                if name not in self._regions:
                    data = "not in the config"
                else:
                    data = str(count) if count else "no data"
                for col, text in ((_REGION, name), (_FLIES, data)):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(row, col, item)
                self.table.setItem(row, _REASON, QTableWidgetItem(declared.get(name, "")))
                self._set_reason_editable(row)
        finally:
            self._filling = False
        self._update_state()

    def _set_reason_editable(self, row: int) -> None:
        """Only a ticked region has a reason to edit."""
        reason = self.table.item(row, _REASON)
        flags = Qt.ItemFlag.ItemIsSelectable
        if not self._refusal and self._ticked(row):
            flags |= Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable
        was = self._filling
        self._filling = True
        try:
            reason.setFlags(flags)
        finally:
            self._filling = was

    def _ticked(self, row: int) -> bool:
        item = self.table.item(row, _REMOVE)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        row = item.row()
        if item.column() == _REMOVE:
            reason = self.table.item(row, _REASON)
            name = self.table.item(row, _REGION).text()
            self._filling = True
            try:
                ## Ticked again: the reason it was read with comes back.
                reason.setText(self._at_load.get(name, "") if self._ticked(row) else "")
            finally:
                self._filling = False
            self._set_reason_editable(row)
        self._update_state()

    def _update_state(self) -> None:
        if self._dir is None:
            return
        self.save_button.setEnabled(self.has_unsaved_changes())
        if self._refusal:
            self.note.setText("Removed regions cannot be edited here. " + self._refusal)
            return
        removed = len(self.wanted())
        self.note.setText(
            f"{removed} of {self.table.rowCount()} region(s) removed"
            + (" (unsaved)" if self.has_unsaved_changes() else "")
            + f". Saved to {removals.removals_path(self._dir)}.")

    # ------------------------------------------------------------------

    def wanted(self) -> dict[str, str]:
        """Region → reason for every ticked row, as it would be saved."""
        out = {}
        for row in range(self.table.rowCount()):
            if self._ticked(row):
                out[self.table.item(row, _REGION).text()] = \
                    self.table.item(row, _REASON).text()
        return removals.normalized(out)

    def has_unsaved_changes(self) -> bool:
        return (self._dir is not None and not self._refusal
                and self.wanted() != removals.normalized(self._at_load))

    def save(self) -> bool:
        """Write the edits; True when the file now says what the table does.
        What failed is said in a dialog, never raised."""
        if not self.has_unsaved_changes():
            return True
        try:
            merged = removals.update_removals(self._dir, self._at_load, self.wanted())
        except (OSError, ValueError) as err:
            QMessageBox.warning(self, "Removed regions not saved", str(err))
            return False
        self._show(merged)
        self.saved.emit(dict(merged))
        return True

    def select_region_of(self, tracker_name: str) -> None:
        """Select the row of the region *tracker_name* belongs to."""
        for row in range(self.table.rowCount()):
            if removals.name_in_region(tracker_name, self.table.item(row, _REGION).text()):
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, _REGION))
                return
