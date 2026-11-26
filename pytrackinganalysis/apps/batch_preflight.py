"""The Batch Run preflight (ADR-0011).

Recursive discovery means the folder you picked no longer tells you what will
run: Projects can sit at any depth, and a Batch Run rewrites analysis in every
one of them. This dialog is the one surface that states the target list, and
the last point at which it can be changed.

It shows, per Member, its relative-path key, how many replicates the run can
actually use, and every **Blocked Experiment** with the reason and the action
that clears it — filing an Unfiled Recording, or scaffolding a config through
the Project's existing Experiment configs… dialog. It also previews the
Removal Sheet: what each row would do, and one switch to decline it for this
run without touching the sheet or any standing declaration (ADR-0010).

Nothing here is a gate. A Member with blocked replicates still runs its
healthy ones, and the run is never refused — a stale folder must not stop ten
Projects at 2am.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .. import batch as batch_mod
from .. import layout as layout_mod
from ..ui import Category, icon

#: Roles on a tree row: which Member, and which Experiment Directory (if any).
_KEY_ROLE = Qt.ItemDataRole.UserRole
_DIR_ROLE = Qt.ItemDataRole.UserRole + 1

# Member keys are relative paths in recursive batches. Keep that first column
# bounded so a deep Project cannot force the Batch surfaces wider than their
# panels; Qt paints the hidden tail with an ellipsis.
PROJECT_COLUMN_MAX_WIDTH = 280


def blocked_color() -> QColor:
    """Red that stays legible on both themes — the light theme's red is
    unreadable on the dark surface and vice versa."""
    from ..ui.theme import resolved_mode

    return QColor("#f87171") if resolved_mode() == "dark" else QColor("#b91c1c")


class BatchPreflightDialog(QDialog):
    """Confirm (and repair) what a Batch Run is about to do.

    ``exec()`` returning ``Accepted`` means run; :attr:`selected_keys` and
    :attr:`apply_removals` then carry the confirmed target list and whether the
    Removal Sheet was declined.
    """

    def __init__(self, parent, root, *, checked=None, log=None) -> None:
        super().__init__(parent)
        self._root = str(root)
        self._log = log or (lambda _text: None)
        self._preferred = None if checked is None else {str(k) for k in checked}
        self._members: list = []
        self._skipped: list = []
        self._loading = False
        #: Keys the USER unchecked. A Member with nothing usable starts
        #: unchecked on its own, and on a batch of freshly-arrived recordings
        #: that is every Member — so "do not touch what I unchecked" has to
        #: mean the user's own act, or filing would refuse the one job it
        #: exists for.
        self._user_unchecked: set = set()
        #: Keys the user checked by hand — a Member nothing can run in still
        #: joins the run if they insist.
        self._user_checked: set = set()
        self._seeded = False
        #: Whether the user has taken a view on the removal sheet switch.
        self._sheet_choice = None
        self.setWindowTitle("Batch Run — review")
        self.setMinimumSize(760, 520)

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        self._heading = QLabel("")
        self._heading.setWordWrap(True)
        self._heading.setStyleSheet("font-weight: 600;")
        outer.addWidget(self._heading)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Project", "Replicates", "Status"])
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        header = self._tree.header()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(0, PROJECT_COLUMN_MAX_WIDTH)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        outer.addWidget(self._tree, 1)

        actions = QHBoxLayout()
        self._btn_fix = QPushButton("File data…")
        self._btn_fix.setIcon(icon("batch", category=Category.TOOLS))
        self._btn_fix.setToolTip(
            "Move the selected experiment's recording into data/ (and any "
            "other loose file into data/extra_files/). Never overwrites.")
        self._btn_fix.clicked.connect(self._fix_selected)
        self._btn_fix.setEnabled(False)
        actions.addWidget(self._btn_fix)
        self._btn_fix_all = QPushButton("File every unfiled recording")
        self._btn_fix_all.setToolTip(
            "File every experiment whose recording sits loose at its root. "
            "Ambiguous ones (two workbooks) are left alone and reported.")
        self._btn_fix_all.clicked.connect(self._fix_all)
        actions.addWidget(self._btn_fix_all)
        btn_rescan = QPushButton("Rescan")
        btn_rescan.setToolTip("Walk the batch folder again — for changes made "
                              "outside the app.")
        btn_rescan.clicked.connect(self.reload)
        actions.addWidget(btn_rescan)
        actions.addStretch(1)
        outer.addLayout(actions)

        self._sheet_box = QCheckBox("Apply the removal sheet before running")
        self._sheet_box.setChecked(True)
        self._sheet_box.toggled.connect(self._on_sheet_toggled)
        self._sheet_box.setToolTip(
            "Write the sheet's rows into each experiment's "
            "removed_regions.yaml. Unchecking skips it for this run only — "
            "the sheet and every standing declaration are left untouched.")
        outer.addWidget(self._sheet_box)
        self._sheet_label = QLabel("")
        self._sheet_label.setWordWrap(True)
        self._sheet_label.setStyleSheet("color: palette(mid);")
        outer.addWidget(self._sheet_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        self._btn_run = buttons.addButton(
            "Run batch", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_run.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._tree.currentItemChanged.connect(
            lambda *_a: self._sync_action_buttons())
        ## Checking a Member changes both which sheet rows are in scope and
        ## what "file everything" would touch.
        self._tree.itemChanged.connect(self._on_item_changed)
        self.reload()

    # ------------------------------------------------------------------
    # State the caller reads back
    # ------------------------------------------------------------------

    @property
    def selected_keys(self) -> list[str]:
        """Member keys the user left checked, in run order."""
        keys = []
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.checkState(0) == Qt.CheckState.Checked:
                keys.append(item.data(0, _KEY_ROLE))
        return keys

    @property
    def apply_removals(self) -> bool:
        return self._sheet_box.isChecked()

    @property
    def members(self) -> list:
        return list(self._members)

    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-walk the batch folder and rebuild the tree, preserving checks."""
        self._loading = True
        try:
            self._reload()
        finally:
            self._loading = False

    def _reload(self) -> None:
        found = batch_mod.discover(self._root)
        self._members = found["members"]
        self._skipped = found["skipped"]

        if self._preferred is not None and not self._seeded:
            ## The Hub's own check column, but only its EXPLICIT half: a row
            ## unchecked there because nothing in it could run is not the user
            ## saying "never run this", and treating it as such is what kept a
            ## Project excluded after the preflight had just repaired it.
            self._seeded = True
            for member in self._members:
                if member.runnable and member.key not in self._preferred:
                    self._user_unchecked.add(member.key)
                if not member.runnable and member.key in self._preferred:
                    self._user_checked.add(member.key)

        self._tree.clear()
        for member in self._members:
            item = QTreeWidgetItem(
                [member.key,
                 f"{len(member.usable)}/{len(member.experiments)}", ""])
            item.setData(0, _KEY_ROLE, member.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ## Derived fresh every time from what the user has actually said
            ## plus what the Member can do NOW — never from the previous check
            ## column. A Member with nothing usable starts unchecked (it can
            ## only produce a failure) and becomes checked the moment filing
            ## makes it runnable, which is the whole point of repairing it
            ## here (ADR-0011).
            checked = (member.key in self._user_checked
                       or (member.runnable
                           and member.key not in self._user_unchecked))
            item.setCheckState(
                0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            blocked = member.blocked
            if blocked:
                item.setText(2, f"{len(blocked)} blocked")
                item.setForeground(2, QBrush(blocked_color()))
            elif not member.experiments:
                item.setText(2, "no replicates")
            else:
                item.setText(2, "ok")
            for experiment in blocked:
                child = QTreeWidgetItem(
                    [experiment.name, "", experiment.status])
                child.setData(0, _KEY_ROLE, member.key)
                child.setData(0, _DIR_ROLE, experiment.directory)
                child.setForeground(2, QBrush(blocked_color()))
                child.setToolTip(0, experiment.detail)
                child.setToolTip(2, experiment.detail)
                item.addChild(child)
            self._tree.addTopLevelItem(item)
            ## After it joins the tree: Qt ignores setExpanded on an item that
            ## has no view yet, which left every blocked reason collapsed
            ## behind an arrow nobody clicks.
            item.setExpanded(bool(blocked))

        if found.get("truncated"):
            self._log("[batch] WARNING: the scan stopped early — this folder "
                      "is larger than a batch should be, and projects deeper "
                      "in it were not found.")
        self._refresh_heading()
        self._refresh_sheet()
        self._sync_action_buttons()

    def _refresh_heading(self) -> None:
        chosen = set(self.selected_keys)
        running = [m for m in self._members if m.key in chosen]
        replicates = sum(len(m.usable) for m in running)
        blocked = sum(len(m.blocked) for m in self._members)
        text = (f"{len(running)} of {len(self._members)} project(s), "
                f"{replicates} replicate(s) will run — {self._root}")
        if blocked:
            text += f" · {blocked} blocked experiment(s) listed below"
        self._heading.setText(text)
        for key, why in self._skipped:
            ## Each carries its own reason — a stray marker, a symlink, an
            ## unreadable folder — and a run that quietly found fewer projects
            ## than the user expects is the failure recursion introduces.
            self._log(f"[batch] {key} skipped — {why}")

    def _refresh_sheet(self) -> None:
        preview = batch_mod.preview_removal_sheet(
            self._root, members=self.selected_keys)
        sheet = preview.get("sheet")
        if sheet is None:
            self._sheet_box.setVisible(False)
            self._sheet_label.setText("No removal sheet in this batch folder.")
            return
        self._sheet_box.setVisible(True)
        if preview.get("error"):
            self._sheet_label.setText(
                f"{os.path.basename(sheet)} could not be read: "
                f"{preview['error']}")
            self._sheet_box.setChecked(False)
            self._sheet_box.setEnabled(False)
            return
        ## Re-armed once the sheet reads again — an earlier version latched it
        ## off, so repairing the sheet and rescanning silently ran without it.
        self._sheet_box.setEnabled(True)
        if self._sheet_choice is None:
            self._sheet_box.setChecked(True)
        counts = preview.get("counts", {})
        parts = [f"{status}: {n}" for status, n in sorted(counts.items())]
        skipped = preview.get("skipped") or 0
        if skipped:
            parts.append(f"{skipped} row(s) for projects not in this run")
        self._sheet_label.setText(
            f"{os.path.basename(sheet)} — " + (", ".join(parts) or "no rows"))
        self._sheet_label.setToolTip(
            "\n".join(result.describe()
                      for result in preview.get("results", [])[:40]))

    def _sync_action_buttons(self) -> None:
        item = self._tree.currentItem()
        directory = item.data(0, _DIR_ROLE) if item is not None else None
        fixable = False
        if directory:
            fixable = layout_mod.plan_filing(directory).possible
        self._btn_fix.setEnabled(bool(fixable))
        self._btn_fix_all.setEnabled(bool(self._unfiled()))

    def _unfiled(self) -> list:
        """Unfiled Recordings filing may touch.

        Everything listed EXCEPT what the user explicitly unchecked: moving
        files inside a colleague's Project that was deliberately excluded is
        the same violation as writing a Removal Sheet into one (ADR-0011),
        while refusing to file a Member that is unchecked only *because*
        nothing in it runs yet would break the common case — a batch of
        recordings straight off the rig, where nothing runs until it is filed.
        """
        return [experiment for member in self._members
                if member.key not in self._user_unchecked
                for experiment in member.blocked
                if experiment.fix == "file"]

    def _on_item_changed(self, item, column: int) -> None:
        if column or self._loading or item.parent() is not None:
            return
        key = item.data(0, _KEY_ROLE)
        if item.checkState(0) == Qt.CheckState.Checked:
            self._user_unchecked.discard(key)
            self._user_checked.add(key)
        else:
            self._user_checked.discard(key)
            self._user_unchecked.add(key)
        ## Checking a Member changes the sheet's scope, what filing would
        ## touch, and the replicate counts the heading states.
        self._refresh_heading()
        self._refresh_sheet()
        self._sync_action_buttons()

    def _on_sheet_toggled(self, on: bool) -> None:
        if not self._loading:
            self._sheet_choice = on

    def focus_member(self, key) -> None:
        """Select (and expand) Member *key* — used when the preflight is
        opened from a specific row's right-click."""
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.data(0, _KEY_ROLE) == key:
                item.setExpanded(True)
                self._tree.setCurrentItem(
                    item.child(0) if item.childCount() else item)
                self._tree.scrollToItem(item)
                return

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_double_click(self, item, _column: int) -> None:
        directory = item.data(0, _DIR_ROLE)
        if not directory:
            return
        experiment = layout_mod.classify(directory)
        if experiment.fix == "file":
            self._file(directory)
        elif experiment.fix == "config":
            self._scaffold(item.data(0, _KEY_ROLE))
        else:
            QMessageBox.information(
                self, "Blocked experiment",
                f"{experiment.name}: {experiment.detail or experiment.status}\n\n"
                "Nothing here can be fixed automatically.")

    def _fix_selected(self) -> None:
        item = self._tree.currentItem()
        if item is not None and item.data(0, _DIR_ROLE):
            self._file(item.data(0, _DIR_ROLE))

    def _fix_all(self) -> None:
        targets = self._unfiled()
        if not targets:
            return
        confirm = QMessageBox.question(
            self, "File every unfiled recording",
            f"Move the recording into data/ in {len(targets)} experiment "
            "director(ies) of the checked projects?\n\nEvery other loose "
            "file goes to data/extra_files/. YAML files and a removal sheet stay "
            "where they are, and nothing is overwritten.")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for experiment in targets:
            self._file(experiment.directory, reload=False)
        self.reload()

    def _file(self, directory, reload: bool = True) -> None:
        plan = layout_mod.file_recording(directory, log=self._log)
        name = os.path.basename(str(directory))
        if plan.refused:
            self._log(f"[file] {name}: {plan.refused}")
            QMessageBox.warning(self, "Cannot file this experiment",
                                f"{name}: {plan.refused}")
        else:
            self._log(f"[file] {name}: {plan.describe()}")
        for skipped_name, why in plan.skipped:
            self._log(f"[file] {name}: {skipped_name} skipped — {why}")
        if reload:
            self.reload()

    def _scaffold(self, key) -> None:
        """Hand off to the Project's own Experiment configs… dialog — the one
        design-aware scaffolding path (ADR-0011).

        It is parented to the Hub rather than to this dialog: it needs the Hub
        to launch the Config Editor, and loading the Project can raise (a
        design mismatch is exactly the kind of Project someone batches).
        """
        from .. import project as prj
        from .hub import ExperimentConfigsDialog

        directory = batch_mod.member_directory(self._root, key)
        try:
            project = prj.Project(str(directory))
        except Exception as err:  # noqa: BLE001
            QMessageBox.warning(
                self, "Experiment configs",
                f"{key} could not be opened as a Project:\n{err}")
            return
        ExperimentConfigsDialog(self.parent() or self, project).exec()
        self.reload()
