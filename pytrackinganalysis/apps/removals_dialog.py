"""The removals window (ADR-0010): declare Removed Regions across a Project.

One checklist for every replicate of a Project — each region defined in its
``tracking_config.yaml``, ticked to remove, with a free-text reason. Saving
writes each replicate's ``removed_regions.yaml``; nothing else is touched.

Only regions the config declares are listed: the config's plate is the
addressable set, and reading it costs no raw-data parsing, so a Project of
eighty replicates opens instantly. Where a replicate has already been
analyzed, a region that produced no tracker at all is marked ``no data`` — an
empty well, which is exactly the row someone wants to tick. A region that is
merely excluded is NOT "no data": it had a fly, and the analysis says why it
left.
"""

from __future__ import annotations

import glob
import os

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import project as prj
from .. import removals
from ..help import HelpButton

#: Table columns.
_REMOVE, _EXPERIMENT, _REGION, _TREATMENT, _DATA, _REASON = range(6)


def project_regions(project_dir) -> list:
    """``[(experiment, region, treatment, has_data)]`` for a Project.

    Read straight from each replicate's ``tracking_config.yaml`` rather than
    through :class:`Project`, so a Project mid-migration — one whose design
    validation would raise — can still have its removals declared.
    """
    root = str(project_dir)
    try:
        entries = sorted(e for e in os.listdir(root)
                         if prj.is_experiment_dir(os.path.join(root, e)))
    except OSError:
        return []
    rows = []
    for name in entries:
        experiment_dir = os.path.join(root, name)
        config = _read_config(experiment_dir)
        regions = config.get("tracking_regions")
        if not isinstance(regions, dict):
            continue
        tracked = _tracked_regions(experiment_dir)
        for region, data in regions.items():
            treatment = ""
            if isinstance(data, dict):
                treatment = str(data.get("experimental_factors", "") or "")
            has_data = None if tracked is None else (str(region) in tracked)
            rows.append((name, str(region), treatment, has_data))
    return rows


def _read_config(experiment_dir) -> dict:
    from .. import layout

    ## The replicate's own config, or its recording's (ADR-0020).
    path = layout.config_path(experiment_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _tracked_regions(experiment_dir):
    """Regions that produced a tracker in the last analysis, or ``None`` when
    the replicate has not been analyzed (then nothing is claimed about it).

    The saved ``_Summary.csv`` is the *filtered* one — excluded flies are
    already dropped from it — so the exclusion audit has to be unioned back
    in. Without that, a region removed last week would read "no data" as if
    its well had been empty all along, and so would a fly the low-transition
    criterion caught.
    """
    import pandas as pd

    def regions_in(pattern, reject=None):
        matches = [p for p in glob.glob(os.path.join(str(experiment_dir),
                                                     "analysis", pattern))
                   if reject is None or not p.endswith(reject)]
        if not matches:
            return None
        try:
            frame = pd.read_csv(sorted(matches)[0])
        except Exception:  # noqa: BLE001
            return None
        if "TrackingRegion" not in frame.columns:
            return None
        return {str(r) for r in frame["TrackingRegion"]}

    analysed = regions_in("*_Summary.csv", reject="_Summary_Facet.csv")
    if analysed is None:
        return None
    return analysed | (regions_in("*_Excluded.csv") or set())


class RemovalsDialog(QDialog):
    """Declare Removed Regions for every replicate of one Project."""

    def __init__(self, project_dir, parent=None) -> None:
        super().__init__(parent)
        self.project_dir = str(project_dir)
        self.written: list[str] = []
        self.changed_experiments: list[str] = []
        self.setWindowTitle(
            f"Removed regions — {os.path.basename(self.project_dir)}")
        self.resize(860, 620)

        outer = QVBoxLayout(self)

        head = QHBoxLayout()
        intro = QLabel(
            "Tick every tracking region to remove from the analysis and say "
            "why — death, escape, an empty well. Removing a region removes "
            "every fly in it from figures, statistics and the summary CSVs; "
            "each is listed with its reason in the reports.")
        intro.setWordWrap(True)
        head.addWidget(intro, 1)
        head.addWidget(HelpButton("removed_regions",
                                  tooltip="Removed regions: declaring, effect, "
                                          "and reporting"))
        outer.addLayout(head)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("experiment or region…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 1)
        self.removed_only = QCheckBox("Show removed only")
        self.removed_only.toggled.connect(self._apply_filter)
        filter_row.addWidget(self.removed_only)
        outer.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Remove", "Experiment", "Region", "Treatment", "Data", "Reason"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_REASON, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        outer.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(mid); font-style: italic;")
        outer.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)

        self._load()

    # ------------------------------------------------------------------

    def _load(self) -> None:
        rows = project_regions(self.project_dir)
        #: experiment -> declaration as it stands on disk, so Save writes only
        #: the replicates that actually changed.
        self._declared: dict[str, dict] = {}
        self.table.setRowCount(0)
        for experiment, region, treatment, has_data in rows:
            declared = self._declared.get(experiment)
            if declared is None:
                declared = removals.read_removals(
                    os.path.join(self.project_dir, experiment))
                self._declared[experiment] = declared
            row = self.table.rowCount()
            self.table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if region in declared
                                else Qt.CheckState.Unchecked)
            self.table.setItem(row, _REMOVE, check)
            for col, text in ((_EXPERIMENT, experiment), (_REGION, region),
                              (_TREATMENT, treatment),
                              (_DATA, "" if has_data is None
                                      else ("" if has_data else "no data"))):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled
                              | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)
            self.table.setItem(row, _REASON,
                               QTableWidgetItem(declared.get(region, "")))

        experiments = len(self._declared)
        declared_total = sum(len(d) for d in self._declared.values())
        self.status.setText(
            f"{self.table.rowCount()} region(s) across {experiments} "
            f"replicate(s); {declared_total} currently removed.")
        if not self.table.rowCount():
            self.status.setText(
                "No replicate in this Project has a tracking_config.yaml with "
                "tracking regions yet.")

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        removed_only = self.removed_only.isChecked()
        for row in range(self.table.rowCount()):
            experiment = self.table.item(row, _EXPERIMENT).text().lower()
            region = self.table.item(row, _REGION).text().lower()
            checked = (self.table.item(row, _REMOVE).checkState()
                       == Qt.CheckState.Checked)
            hide = (text and text not in experiment and text not in region) \
                or (removed_only and not checked)
            self.table.setRowHidden(row, bool(hide))

    def collect(self) -> dict:
        """``{experiment: {region: reason}}`` as the table currently stands."""
        out: dict[str, dict] = {name: {} for name in self._declared}
        for row in range(self.table.rowCount()):
            if self.table.item(row, _REMOVE).checkState() \
                    != Qt.CheckState.Checked:
                continue
            experiment = self.table.item(row, _EXPERIMENT).text()
            region = self.table.item(row, _REGION).text()
            reason = self.table.item(row, _REASON).text().strip()
            out.setdefault(experiment, {})[region] = \
                reason or removals.DEFAULT_REASON
        return out

    def _save(self) -> None:
        wanted = self.collect()
        written, changed, failures = [], [], []
        for experiment, declared in wanted.items():
            if declared == self._declared.get(experiment, {}):
                continue
            experiment_dir = os.path.join(self.project_dir, experiment)
            try:
                path = removals.write_removals(experiment_dir, declared)
            except OSError as err:
                failures.append(f"{experiment}: {err}")
                continue
            changed.append(experiment)
            if path:
                written.append(path)
        if failures:
            QMessageBox.warning(
                self, "Removed regions",
                "Some declarations could not be saved:\n" + "\n".join(failures))
            return
        self.written = written
        self.changed_experiments = changed
        self.accept()
