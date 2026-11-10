"""Band setup: members (order = position on stage, left to right) and groups."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..model import Group, Member, new_id
from .common import hint


class BandTab(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False

        g_mem = QGroupBox("Members  (top → bottom = stage left → right)")
        ml = QVBoxLayout(g_mem)
        self.members = QListWidget()
        self.members.itemChanged.connect(self._rename_member)
        ml.addWidget(self.members, 1)
        row = QHBoxLayout()
        for text, fn in (("Add", self.add_member), ("Remove", self.remove_member), ("▲", lambda: self.move(-1)), ("▼", lambda: self.move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        ml.addLayout(row)
        ml.addWidget(hint("Double-click a name to rename. Each element's parts (bars, sides, segments...) are shared out "
                          "between the members in this order. Every member gets a row inside each element's folder "
                          "in the pattern editor."))

        g_grp = QGroupBox("Groups")
        gl = QHBoxLayout(g_grp)
        left = QVBoxLayout()
        self.groups = QListWidget()
        self.groups.currentItemChanged.connect(lambda *_: self._load_group_members())
        self.groups.itemChanged.connect(self._rename_group)
        left.addWidget(self.groups, 1)
        row2 = QHBoxLayout()
        for text, fn in (("Add group", self.add_group), ("Remove", self.remove_group)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row2.addWidget(b)
        left.addLayout(row2)
        right = QVBoxLayout()
        right.addWidget(QLabel("Members in this group:"))
        self.group_members = QListWidget()
        self.group_members.itemChanged.connect(self._toggle_group_member)
        right.addWidget(self.group_members, 1)
        right.addWidget(hint("A group is one row in the pattern editor that lights all its members at once "
                             "(e.g. Rhythm section, Vocals, Left side)."))
        gl.addLayout(left, 1)
        gl.addLayout(right, 1)

        lay = QHBoxLayout(self)
        lay.addWidget(g_mem, 1)
        lay.addWidget(g_grp, 2)
        ctl.changed.connect(lambda kind: self.reload() if kind in ("all", "band") else None)
        self.reload()

    # ---- loading ---------------------------------------------------------------------
    def reload(self):
        self._updating = True
        cur_m = self.members.currentRow()
        cur_g = self.groups.currentRow()
        self.members.clear()
        for m in self.ctl.song.members:
            it = QListWidgetItem(m.name)
            it.setData(Qt.ItemDataRole.UserRole, m.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.members.addItem(it)
        self.groups.clear()
        for g in self.ctl.song.groups:
            it = QListWidgetItem(g.name)
            it.setData(Qt.ItemDataRole.UserRole, g.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.groups.addItem(it)
        self.members.setCurrentRow(min(max(cur_m, 0), self.members.count() - 1))
        self.groups.setCurrentRow(min(max(cur_g, 0), self.groups.count() - 1))
        self._updating = False
        self._load_group_members()

    def _current_group(self) -> Group | None:
        it = self.groups.currentItem()
        if not it:
            return None
        gid = it.data(Qt.ItemDataRole.UserRole)
        return next((g for g in self.ctl.song.groups if g.id == gid), None)

    def _load_group_members(self):
        self._updating = True
        self.group_members.clear()
        g = self._current_group()
        for m in self.ctl.song.members:
            it = QListWidgetItem(m.name)
            it.setData(Qt.ItemDataRole.UserRole, m.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if g and m.id in g.members else Qt.CheckState.Unchecked)
            if not g:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.group_members.addItem(it)
        self._updating = False

    # ---- members -----------------------------------------------------------------------
    def add_member(self):
        self.ctl.song.members.append(Member(new_id(), f"Member {len(self.ctl.song.members) + 1}"))
        self.ctl.commit("band")
        self.members.setCurrentRow(self.members.count() - 1)

    def remove_member(self):
        it = self.members.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "Remove member", f"Remove {it.text()} and all of their notes?") != QMessageBox.StandardButton.Yes:
            return
        self.ctl.song.remove_member(it.data(Qt.ItemDataRole.UserRole))
        self.ctl.commit("band")

    def move(self, d: int):
        i = self.members.currentRow()
        ms = self.ctl.song.members
        if 0 <= i < len(ms) and 0 <= i + d < len(ms):
            ms[i], ms[i + d] = ms[i + d], ms[i]
            self.ctl.commit("band")
            self.members.setCurrentRow(i + d)

    def _rename_member(self, it):
        if self._updating:
            return
        m = next((x for x in self.ctl.song.members if x.id == it.data(Qt.ItemDataRole.UserRole)), None)
        if m and it.text().strip() and it.text() != m.name:
            m.name = it.text().strip()
            self.ctl.commit("band")

    # ---- groups --------------------------------------------------------------------------
    def add_group(self):
        self.ctl.song.groups.append(Group(new_id(), f"Group {len(self.ctl.song.groups) + 1}", []))
        self.ctl.commit("band")
        self.groups.setCurrentRow(self.groups.count() - 1)

    def remove_group(self):
        g = self._current_group()
        if g:
            self.ctl.song.remove_group(g.id)
            self.ctl.commit("band")

    def _rename_group(self, it):
        if self._updating:
            return
        g = self._current_group()
        if g and g.id == it.data(Qt.ItemDataRole.UserRole) and it.text().strip() and it.text() != g.name:
            g.name = it.text().strip()
            self.ctl.commit("band")

    def _toggle_group_member(self, it):
        if self._updating:
            return
        g = self._current_group()
        if not g:
            return
        mid = it.data(Qt.ItemDataRole.UserRole)
        on = it.checkState() == Qt.CheckState.Checked
        if on and mid not in g.members:
            order = [m.id for m in self.ctl.song.members]
            g.members = sorted(g.members + [mid], key=order.index)
        elif not on and mid in g.members:
            g.members.remove(mid)
        self.ctl.commit("band", f"group:{g.id}")
