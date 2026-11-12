"""Band tab: the members and groups of one element. An element has one member per segment (bar, side,
shaft, arc, shape...), left to right; each is a row in its folder in the pattern editor, and groups
are named sets of them. Selecting a member or a group lights it alone in the preview."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..model import Member, new_group, new_id
from .common import hint

_COPIED_MEMBERS: dict = {}   # {"n": member count, "names": [...]}
_COPIED_GROUPS: dict = {}    # {"n": source member count, "groups": [group dicts]}


def map_parts(parts, n_src: int, n_dst: int) -> list:
    """A group's members on an element with n_src members, carried onto one with n_dst: the same
    members when the counts match, else the same stretch of the element (left half stays left half)."""
    if n_src == n_dst:
        return [i for i in parts if i < n_dst]
    src = set(parts)
    return [j for j in range(n_dst) if int(j * n_src / max(1, n_dst)) in src]


PART_WORD = {"polygon": "side", "custom": "shape", "bars": "bar", "shafts": "shaft", "ring": "arc", "sun": "piece"}


class BandTab(QWidget):
    highlightChanged = pyqtSignal()      # the member / group to light in the preview changed
    elementChosen = pyqtSignal(str)      # the element picked here (to follow in the Elements tab)

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False
        self._hl_source = None           # "member" | "group": what was clicked last

        top = QHBoxLayout()
        top.addWidget(QLabel("Element:"))
        self.cmb_element = QComboBox()
        self.cmb_element.setMinimumWidth(280)
        self.cmb_element.currentIndexChanged.connect(self._on_element_changed)
        top.addWidget(self.cmb_element)
        top.addStretch(1)
        top.addWidget(QLabel("People on stage (stage view):"))
        self.spin_people = QSpinBox()
        self.spin_people.setRange(1, 16)
        self.spin_people.setToolTip("How many band members the stage view draws in front of the wall.")
        self.spin_people.valueChanged.connect(self._on_people)
        top.addWidget(self.spin_people)

        # members of the element
        self.g_mem = QGroupBox("Members")
        ml = QVBoxLayout(self.g_mem)
        self.members = QListWidget()
        self.members.itemChanged.connect(self._rename_member)
        self.members.itemPressed.connect(lambda *_: self._set_source("member"))
        self.members.currentRowChanged.connect(lambda *_: self._set_source("member"))
        ml.addWidget(self.members, 1)
        row = QHBoxLayout()
        self.btn_add_member = QPushButton("Add")
        self.btn_add_member.clicked.connect(self.add_member)
        self.btn_rm_member = QPushButton("Remove")
        self.btn_rm_member.clicked.connect(self.remove_member)
        row.addWidget(self.btn_add_member)
        row.addWidget(self.btn_rm_member)
        ml.addLayout(row)
        row_c = QHBoxLayout()
        b = QPushButton("Copy members")
        b.setToolTip("Copy this element's members (how many, and their names) to paste onto another element.")
        b.clicked.connect(self.copy_members)
        row_c.addWidget(b)
        self.btn_paste_members = QPushButton("Paste members")
        self.btn_paste_members.setToolTip("Give this element the copied members: the same number (bars / sides...) "
                                          "and names.")
        self.btn_paste_members.clicked.connect(self.paste_members)
        row_c.addWidget(self.btn_paste_members)
        ml.addLayout(row_c)
        self.lbl_mem_hint = hint("")
        ml.addWidget(self.lbl_mem_hint)

        # groups of the element
        self.g_grp = QGroupBox("Groups")
        gl = QHBoxLayout(self.g_grp)
        left = QVBoxLayout()
        self.groups = QListWidget()
        self.groups.currentItemChanged.connect(lambda *_: self._on_group_changed())
        self.groups.itemPressed.connect(lambda *_: self._set_source("group"))
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
        row2c = QHBoxLayout()
        for text, tip, fn in (("Copy", "Copy the selected group", lambda: self.copy_groups(False)),
                              ("Copy all", "Copy all of this element's groups", lambda: self.copy_groups(True)),
                              ("Paste", "Add the copied groups to this element (an element with a different number "
                                        "of members gets the same stretch: a left half stays the left half)",
                               self.paste_groups)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            if text == "Paste":
                self.btn_paste_groups = b
            row2c.addWidget(b)
        left.addLayout(row2c)
        right = QVBoxLayout()
        right.addWidget(QLabel("Members in this group:"))
        self.group_parts = QListWidget()
        self.group_parts.itemChanged.connect(self._toggle_group_part)
        right.addWidget(self.group_parts, 1)
        row3 = QHBoxLayout()
        for text, fn in (("All", lambda: self._set_parts(lambda i: True)), ("None", lambda: self._set_parts(lambda i: False)),
                         ("Odd", lambda: self._set_parts(lambda i: i % 2 == 0)),
                         ("Even", lambda: self._set_parts(lambda i: i % 2 == 1))):
            b = QPushButton(text)
            b.setProperty("compact", True)
            b.clicked.connect(fn)
            row3.addWidget(b)
        right.addLayout(row3)
        right.addWidget(hint("A group is one row in the element's folder in the pattern editor that lights all the "
                             "members you tick here (e.g. Left half, Outer bars, Every other side)."))
        gl.addLayout(left, 1)
        gl.addLayout(right, 1)

        body = QHBoxLayout()
        body.addWidget(self.g_mem, 1)
        body.addWidget(self.g_grp, 2)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        ctl.changed.connect(lambda kind: self.reload() if kind in ("all", "band", "elements") else None)
        self.reload()

    # ---- selection / preview ---------------------------------------------------------
    def element(self):
        return self.ctl.song.element(self.cmb_element.currentData() or "")

    def select_element(self, eid: str | None):
        i = self.cmb_element.findData(eid) if eid else -1
        if i >= 0 and i != self.cmb_element.currentIndex():
            self.cmb_element.setCurrentIndex(i)

    def highlight(self):
        """(element id, member indices) to light alone in the preview, or None."""
        el = self.element()
        if el is None:
            return None
        if self._hl_source == "group":
            g = self._current_group()
            return el.id, tuple(g["parts"]) if g else ()
        i = self.members.currentRow()
        return el.id, (i,) if self._hl_source == "member" and i >= 0 else ()

    def _set_source(self, src):
        if not self._updating:
            self._hl_source = src
            self.highlightChanged.emit()

    # ---- loading ---------------------------------------------------------------------
    def reload(self):
        self._updating = True
        song = self.ctl.song
        self.spin_people.setValue(max(1, len(song.members)))
        cur = self.cmb_element.currentData()
        self.cmb_element.clear()
        for el in song.elements:
            n = el.part_count()
            if n:          # haze has no members
                self.cmb_element.addItem(f"{el.name}  ({n} member{'' if n == 1 else 's'})", el.id)
        i = self.cmb_element.findData(cur)
        self.cmb_element.setCurrentIndex(i if i >= 0 else 0)
        self._updating = False
        self._load_element()

    def _on_element_changed(self, *_):
        if self._updating:
            return
        self._hl_source = None
        self._load_element()
        el = self.element()
        if el is not None:
            self.elementChosen.emit(el.id)

    def _load_element(self):
        self._updating = True
        el = self.element()
        n = el.part_count() if el else 0
        word = PART_WORD.get(el.type, "segment") if el else "segment"
        self.g_mem.setTitle(f"Members of {el.name}  ({n}: one per {word}, left → right)" if el else "Members")
        self.g_grp.setTitle(f"Groups of {el.name}" if el else "Groups")
        cur_m = self.members.currentRow()
        self.members.clear()
        for i in range(n):
            it = QListWidgetItem(el.member_name(i))
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.members.addItem(it)
        self.members.setCurrentRow(min(cur_m, n - 1) if cur_m >= 0 else -1)
        can = bool(el and el.can_resize())
        self.btn_add_member.setEnabled(can)
        self.btn_rm_member.setEnabled(can and n > (3 if el.type == "polygon" else 1))
        if el is None:
            self.lbl_mem_hint.setText("Add an element in the Elements tab first.")
        elif el.type == "custom":
            self.lbl_mem_hint.setText("One member per shape: add or remove shapes in the Elements tab.")
        elif el.type == "sun":
            self.lbl_mem_hint.setText("The sun is one piece: one member.")
        else:
            self.lbl_mem_hint.setText(f"Each {word} is a member: a row in this element's folder in the pattern "
                                      f"editor. Add / Remove changes the number of {word}s. Double-click to "
                                      "rename. Select one to light it in the preview.")
        cur_g = self.groups.currentItem().data(Qt.ItemDataRole.UserRole) if self.groups.currentItem() else None
        self.groups.clear()
        for g in (el.groups if el else []):
            it = QListWidgetItem(g["name"])
            it.setData(Qt.ItemDataRole.UserRole, g["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.groups.addItem(it)
        row = next((i for i in range(self.groups.count())
                    if self.groups.item(i).data(Qt.ItemDataRole.UserRole) == cur_g), 0)
        self.groups.setCurrentRow(min(row, self.groups.count() - 1))
        self.btn_add_group.setEnabled(el is not None)
        self.btn_rm_group.setEnabled(self.groups.count() > 0)
        self.btn_paste_groups.setEnabled(el is not None and bool(_COPIED_GROUPS))
        self.btn_paste_members.setEnabled(bool(el and _COPIED_MEMBERS))
        self._updating = False
        self._load_group_parts()
        self.highlightChanged.emit()

    def _current_group(self) -> dict | None:
        el, it = self.element(), self.groups.currentItem()
        if not el or not it:
            return None
        gid = it.data(Qt.ItemDataRole.UserRole)
        return next((g for g in el.groups if g["id"] == gid), None)

    def _on_group_changed(self):
        self._load_group_parts()
        self._set_source("group")

    def _load_group_parts(self):
        if self._updating:
            return
        self._updating = True
        self.group_parts.clear()
        el, g = self.element(), self._current_group()
        for i in range(el.part_count() if el else 0):
            it = QListWidgetItem(el.member_name(i))
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if g and i in g["parts"] else Qt.CheckState.Unchecked)
            if not g:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.group_parts.addItem(it)
        self._updating = False

    # ---- members -----------------------------------------------------------------------
    def add_member(self):
        el = self.element()
        if el and self.ctl.song.add_part(el):
            self.ctl.commit("elements")
            self.members.setCurrentRow(self.members.count() - 1)
            self._set_source("member")

    def remove_member(self):
        el, i = self.element(), self.members.currentRow()
        if not el:
            return
        if i < 0:
            i = el.part_count() - 1
        if QMessageBox.question(self, "Remove member",
                                f"Remove {el.member_name(i)} from {el.name} and all of its notes?") != QMessageBox.StandardButton.Yes:
            return
        if self.ctl.song.remove_part(el, i):
            self.ctl.commit("elements")

    def copy_members(self):
        el = self.element()
        if el:
            _COPIED_MEMBERS.clear()
            _COPIED_MEMBERS.update(n=el.part_count(), names=[el.member_name(i) for i in range(el.part_count())])
            self._load_element()

    def paste_members(self):
        el = self.element()
        if not (el and _COPIED_MEMBERS):
            return
        n = _COPIED_MEMBERS["n"]
        song = self.ctl.song
        if el.can_resize() and n < el.part_count() and QMessageBox.question(
                self, "Paste members", f"{el.name} goes from {el.part_count()} to {n} members: the notes of the "
                f"last {el.part_count() - n} are removed. Continue?") != QMessageBox.StandardButton.Yes:
            return
        if el.can_resize():      # match the number of members (removing from the end drops their notes)
            while el.part_count() < n and song.add_part(el):
                pass
            while el.part_count() > n and song.remove_part(el, el.part_count() - 1):
                pass
        names = _COPIED_MEMBERS["names"][:el.part_count()]
        el.part_names = [nm if not nm.startswith("Member ") else "" for nm in names]
        self.ctl.commit("elements")

    def _rename_member(self, it):
        if self._updating:
            return
        el = self.element()
        i = it.data(Qt.ItemDataRole.UserRole)
        name = it.text().strip()
        if el and name and name != el.member_name(i):
            names = list(el.part_names) + [""] * (i + 1 - len(el.part_names))
            names[i] = name
            el.part_names = names
            self.ctl.commit("band")

    def _on_people(self, v):
        if self._updating:
            return
        ms = self.ctl.song.members
        while len(ms) < v:
            ms.append(Member(new_id(), f"Member {len(ms) + 1}"))
        del ms[v:]
        self.ctl.commit("band", "people")

    # ---- groups --------------------------------------------------------------------------
    def add_group(self):
        el = self.element()
        if not el:
            return
        el.groups.append(new_group(f"Group {len(el.groups) + 1}"))
        self.ctl.commit("band")
        self.groups.setCurrentRow(self.groups.count() - 1)

    def copy_groups(self, everything: bool):
        el = self.element()
        if not el:
            return
        gs = el.groups if everything else [g for g in [self._current_group()] if g]
        if gs:
            _COPIED_GROUPS.clear()
            _COPIED_GROUPS.update(n=el.part_count(), groups=[dict(g) for g in gs])
            self._load_element()

    def paste_groups(self):
        el = self.element()
        if not (el and _COPIED_GROUPS):
            return
        for g in _COPIED_GROUPS["groups"]:
            el.groups.append(new_group(g["name"], map_parts(g["parts"], _COPIED_GROUPS["n"], el.part_count())))
        self.ctl.commit("band")
        self.groups.setCurrentRow(self.groups.count() - 1)

    def remove_group(self):
        el, g = self.element(), self._current_group()
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
        parts = set(g["parts"])
        parts = parts | {i} if it.checkState() == Qt.CheckState.Checked else parts - {i}
        g["parts"] = sorted(parts)
        self._hl_source = "group"
        self.ctl.commit("band", f"group:{g['id']}")

    def _set_parts(self, keep):
        el, g = self.element(), self._current_group()
        if el and g:
            g["parts"] = [i for i in range(el.part_count()) if keep(i)]
            self._hl_source = "group"
            self.ctl.commit("band")
