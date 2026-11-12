"""Band setup: members (order = position on stage, left to right) and each element's groups."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..model import Member, new_group, new_id, part_owners
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

        g_grp = QGroupBox("Groups  (each element has its own)")
        gl = QHBoxLayout(g_grp)
        left = QVBoxLayout()
        left.addWidget(QLabel("Element:"))
        self.cmb_element = QComboBox()
        self.cmb_element.currentIndexChanged.connect(lambda *_: self._load_groups())
        left.addWidget(self.cmb_element)
        left.addWidget(QLabel("Its groups:"))
        self.groups = QListWidget()
        self.groups.currentItemChanged.connect(lambda *_: self._load_group_parts())
        self.groups.itemChanged.connect(self._rename_group)
        left.addWidget(self.groups, 1)
        row2 = QHBoxLayout()
        self.btn_add_group = QPushButton("Add group")
        self.btn_add_group.clicked.connect(self.add_group)
        self.btn_rm_group = QPushButton("Remove")
        self.btn_rm_group.clicked.connect(self.remove_group)
        row2.addWidget(self.btn_add_group)
        row2.addWidget(self.btn_rm_group)
        left.addLayout(row2)
        right = QVBoxLayout()
        self.lbl_parts = QLabel("Segments in this group:")
        right.addWidget(self.lbl_parts)
        self.group_parts = QListWidget()
        self.group_parts.itemChanged.connect(self._toggle_group_part)
        right.addWidget(self.group_parts, 1)
        row3 = QHBoxLayout()
        for text, fn in (("All", lambda: self._set_all_parts(True)), ("None", lambda: self._set_all_parts(False)),
                         ("Odd", lambda: self._set_pattern_parts(0)), ("Even", lambda: self._set_pattern_parts(1))):
            b = QPushButton(text)
            b.setProperty("compact", True)
            b.clicked.connect(fn)
            row3.addWidget(b)
        right.addLayout(row3)
        right.addWidget(hint("A group is a row inside its element's folder in the pattern editor that lights the "
                             "segments you tick here (e.g. Left half, Outer bars, Every other side). The list "
                             "follows the element's number of segments (Count / sides / shapes in the Elements "
                             "tab); each line shows the member that segment belongs to."))
        gl.addLayout(left, 1)
        gl.addLayout(right, 1)

        lay = QHBoxLayout(self)
        lay.addWidget(g_mem, 1)
        lay.addWidget(g_grp, 2)
        ctl.changed.connect(lambda kind: self.reload() if kind in ("all", "band", "elements") else None)
        self.reload()

    # ---- loading ---------------------------------------------------------------------
    def reload(self):
        self._updating = True
        cur_m = self.members.currentRow()
        self.members.clear()
        for m in self.ctl.song.members:
            it = QListWidgetItem(m.name)
            it.setData(Qt.ItemDataRole.UserRole, m.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.members.addItem(it)
        self.members.setCurrentRow(min(max(cur_m, 0), self.members.count() - 1))
        cur_e = self.cmb_element.currentData()
        self.cmb_element.clear()
        for el in self.ctl.song.elements:
            if el.part_count():          # haze has no segments to group
                n = el.part_count()
                self.cmb_element.addItem(f"{el.name}  ({n} segment{'' if n == 1 else 's'})", el.id)
        i = self.cmb_element.findData(cur_e)
        self.cmb_element.setCurrentIndex(i if i >= 0 else 0)
        self._updating = False
        self._load_groups()

    def _current_element(self):
        return self.ctl.song.element(self.cmb_element.currentData() or "")

    def _load_groups(self):
        if self._updating:
            return
        self._updating = True
        el = self._current_element()
        cur = self.groups.currentItem().data(Qt.ItemDataRole.UserRole) if self.groups.currentItem() else None
        self.groups.clear()
        for g in (el.groups if el else []):
            it = QListWidgetItem(g["name"])
            it.setData(Qt.ItemDataRole.UserRole, g["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.groups.addItem(it)
        row = next((i for i in range(self.groups.count())
                    if self.groups.item(i).data(Qt.ItemDataRole.UserRole) == cur), 0)
        self.groups.setCurrentRow(min(row, self.groups.count() - 1))
        self.btn_add_group.setEnabled(el is not None)
        self.btn_rm_group.setEnabled(self.groups.count() > 0)
        self._updating = False
        self._load_group_parts()

    def _current_group(self) -> dict | None:
        el, it = self._current_element(), self.groups.currentItem()
        if not el or not it:
            return None
        gid = it.data(Qt.ItemDataRole.UserRole)
        return next((g for g in el.groups if g["id"] == gid), None)

    def _load_group_parts(self):
        if self._updating:
            return
        self._updating = True
        self.group_parts.clear()
        el, g = self._current_element(), self._current_group()
        n = el.part_count() if el else 0
        members = self.ctl.song.members
        owners = part_owners(n, len(members)) if n else []
        word = {"polygon": "Side", "custom": "Shape", "bars": "Bar", "shafts": "Shaft", "ring": "Arc",
                "sun": "Sun"}.get(el.type if el else "", "Segment")
        self.lbl_parts.setText(f"{word}s in this group ({n}, left → right):" if el else "Segments in this group:")
        for i in range(n):
            who = ", ".join(members[o].name for o in owners[i])
            it = QListWidgetItem(f"{word} {i + 1}" + (f"   ·  {who}" if who else ""))
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if g and i in g["parts"] else Qt.CheckState.Unchecked)
            if not g:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.group_parts.addItem(it)
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
        el = self._current_element()
        if not el:
            return
        el.groups.append(new_group(f"Group {len(el.groups) + 1}"))
        self.ctl.commit("band")
        self.groups.setCurrentRow(self.groups.count() - 1)

    def remove_group(self):
        el, g = self._current_element(), self._current_group()
        if el and g:
            self.ctl.song.remove_group(el, g["id"])
            self.ctl.commit("band")

    def _rename_group(self, it):
        if self._updating:
            return
        g = self._current_group()
        if g and g["id"] == it.data(Qt.ItemDataRole.UserRole) and it.text().strip() and it.text() != g["name"]:
            g["name"] = it.text().strip()
            self.ctl.commit("band")

    def _toggle_group_part(self, it):
        if self._updating:
            return
        g = self._current_group()
        if not g:
            return
        i = it.data(Qt.ItemDataRole.UserRole)
        on = it.checkState() == Qt.CheckState.Checked
        parts = set(g["parts"])
        parts = parts | {i} if on else parts - {i}
        g["parts"] = sorted(parts)
        self.ctl.commit("band", f"group:{g['id']}")

    def _set_parts(self, keep):
        el, g = self._current_element(), self._current_group()
        if el and g:
            g["parts"] = [i for i in range(el.part_count()) if keep(i)]
            self.ctl.commit("band")

    def _set_all_parts(self, on: bool):
        self._set_parts(lambda i: on)

    def _set_pattern_parts(self, r: int):
        self._set_parts(lambda i: i % 2 == r)
