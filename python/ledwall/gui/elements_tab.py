"""Elements tab: build the visual elements of a song (e.g. just one main hexagon).

Left: layer list (top = front) with visibility checkboxes. Right: properties of the selected
element. In the preview above, click an element to select it, drag to move, wheel to resize."""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..model import ELEMENT_LABELS, new_id, preset_element
from .common import ColorButton, LabeledSlider, hint

PRESETS = [
    ("sun", "Sunset sun"), ("hexagon", "Main hexagon"), ("polygon", "Polygon"), ("ring", "Ring / circle"),
    ("bars", "Vertical blocks"), ("shafts", "Light shafts"), ("horizon", "Horizon"), ("beam", "Beam / line"),
]

# which properties each element type uses
TYPE_FIELDS = {
    "sun":     {"x", "y", "size", "react_scale", "core", "softness", "cuts"},
    "polygon": {"fill", "sides", "x", "y", "size", "rotation", "spin", "thickness", "react_scale"},
    "ring":    {"fill", "x", "y", "size", "rotation", "spin", "thickness", "react_scale"},
    "bars":    {"fill", "x", "size", "height", "thickness", "slats"},
    "shafts":  {"x", "size", "height", "thickness"},
    "horizon": {"x", "y", "size", "height", "thickness"},
    "beam":    {"x", "y", "size", "rotation", "spin", "thickness", "react_scale"},
}
SIZE_LABEL = {"sun": "Radius", "polygon": "Radius", "ring": "Radius", "beam": "Length"}


class ElementsTab(QWidget):
    previewLitChanged = pyqtSignal(bool)

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False
        self.selected_id: str | None = ctl.song.elements[0].id if ctl.song.elements else None

        # ---- layer list ----------------------------------------------------------------
        left = QWidget()
        left.setFixedWidth(260)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addWidget(QLabel("<b>Elements</b>  (top of list = front)"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_item_changed)
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        menu = QMenu(self)
        for key, label in PRESETS:
            menu.addAction(label).triggered.connect(lambda _=False, k=key: self.add(k))
        self.btn_add.setMenu(menu)
        row.addWidget(self.btn_add)
        for text, fn in (("Duplicate", self.duplicate), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        row2 = QHBoxLayout()
        for text, d in (("▲ Forward", -1), ("▼ Back", 1)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, dd=d: self.move(dd))
            row2.addWidget(b)
        ll.addLayout(row2)
        self.chk_lit = QCheckBox("Preview with everything lit")
        self.chk_lit.setChecked(True)
        self.chk_lit.toggled.connect(self.previewLitChanged.emit)
        ll.addWidget(self.chk_lit)
        ll.addWidget(hint("In the preview: click an element to select it, drag to move it, mouse wheel to resize. "
                          "Double-click a name to rename. Uncheck to hide."))

        # ---- properties --------------------------------------------------------------------
        props = QWidget()
        self.form = QFormLayout(props)
        self.lbl_type = QLabel()
        self.form.addRow("Type", self.lbl_type)
        self.edit_name = QLineEdit()
        self.edit_name.textEdited.connect(lambda t: self._set("name", t, refresh_list=True))
        self.form.addRow("Name", self.edit_name)
        self.combo_driver = QComboBox()
        self.combo_driver.setToolTip("What lights this element. 'All members' splits it into one part per member "
                                     "(hexagon edges, ring arcs, block clusters, beam segments).")
        self.combo_driver.currentIndexChanged.connect(lambda i: self._set("driver", self.combo_driver.itemData(i)))
        self.form.addRow("Lit by", self.combo_driver)
        crow = QHBoxLayout()
        self.combo_color = QComboBox()
        for key, label in (("main", "Song colour"), ("accent", "Accent colour"), ("custom", "Custom")):
            self.combo_color.addItem(label, key)
        self.combo_color.currentIndexChanged.connect(self._on_color_mode)
        self.btn_custom = ColorButton((255, 255, 255))
        self.btn_custom.colorChanged.connect(lambda rgb: self._set("custom_rgb", tuple(rgb)))
        crow.addWidget(self.combo_color, 1)
        crow.addWidget(self.btn_custom, 1)
        self.form.addRow("Colour", crow)
        self.color_row = crow
        self.combo_fill = QComboBox()
        for key, label in (("outline", "Outline (neon tube)"), ("filled", "Filled"), ("gradient", "Gradient fill")):
            self.combo_fill.addItem(label, key)
        self.combo_fill.currentIndexChanged.connect(lambda i: self._set("fill", self.combo_fill.itemData(i)))
        self.form.addRow("Fill", self.combo_fill)
        self.spin_sides = QSpinBox()
        self.spin_sides.setRange(3, 12)
        self.spin_sides.valueChanged.connect(lambda v: self._set("sides", v))
        self.form.addRow("Sides", self.spin_sides)

        self.sliders = {}
        for attr, label, lo, hi, step, fmt in (
            ("x", "Horizontal position", 0, 1, 0.005, "{:.3f}"),
            ("y", "Vertical position", 0, 1, 0.005, "{:.3f}"),
            ("size", "Size", 0.02, 1.0, 0.005, "{:.3f}"),
            ("height", "Height", 0.1, 1.0, 0.01, "{:.2f}"),
            ("rotation", "Rotation", -180, 180, 1, "{:.0f}°"),
            ("spin", "Spin", -30, 30, 0.25, "{:.2f}°/b"),
            ("thickness", "Line thickness", 1, 30, 0.5, "{:.1f}px"),
            ("glow", "Glow", 0, 3, 0.05, "{:.2f}"),
            ("idle", "Idle visibility", 0, 1, 0.01, "{:.2f}"),
            ("react_scale", "Grow when lit", 0, 0.6, 0.01, "{:.2f}"),
            ("slats", "Slats per member", 1, 6, 1, "{:.0f}"),
            ("core", "Warm centre", 0, 1, 0.01, "{:.2f}"),
            ("softness", "Edge softness", 0, 0.3, 0.005, "{:.3f}"),
            ("cuts", "Horizon cuts", 0, 8, 1, "{:.0f}"),
        ):
            s = LabeledSlider(lo, hi, lo, step, fmt)
            s.valueChanged.connect(lambda v, a=attr: self._set(a, int(round(v)) if a in ("slats", "cuts") else v))
            self.form.addRow(label, s)
            self.sliders[attr] = s
        self.empty = QLabel("Add an element with  Add  — for example a single main hexagon.")
        self.empty.setObjectName("hint")
        self.form.addRow(self.empty)
        scroll = QScrollArea()
        scroll.setWidget(props)
        scroll.setWidgetResizable(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(left)
        lay.addWidget(scroll, 1)
        ctl.changed.connect(self._on_changed)
        self.reload()

    # ---- model helpers ------------------------------------------------------------------
    @property
    def element(self):
        return self.ctl.song.element(self.selected_id) if self.selected_id else None

    def select(self, eid: str | None):
        self.selected_id = eid
        self._updating = True
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == eid:
                self.list.setCurrentRow(i)
        self._updating = False
        self.load_fields()

    def _commit(self, key=None):
        self.ctl.commit("elements", key)

    def add(self, kind: str):
        el = preset_element(kind)
        self.ctl.song.elements.insert(0, el)
        self.selected_id = el.id
        self._commit()

    def duplicate(self):
        src = self.element
        if not src:
            return
        el = copy.deepcopy(src)
        el.id, el.name = new_id(), src.name + " copy"
        el.x = min(1.0, el.x + 0.05)
        els = self.ctl.song.elements
        els.insert(els.index(src), el)
        self.selected_id = el.id
        self._commit()

    def delete(self):
        el = self.element
        if not el:
            return
        els = self.ctl.song.elements
        i = els.index(el)
        els.remove(el)
        self.selected_id = els[min(i, len(els) - 1)].id if els else None
        self._commit()

    def move(self, d: int):
        el = self.element
        els = self.ctl.song.elements
        if el:
            i = els.index(el)
            if 0 <= i + d < len(els):
                els[i], els[i + d] = els[i + d], els[i]
                self._commit()

    def _set(self, attr, value, refresh_list=False):
        el = self.element
        if self._updating or not el:
            return
        setattr(el, attr, value)
        self._commit(f"{el.id}:{attr}")
        if refresh_list:
            it = self.list.currentItem()
            if it:
                self._updating = True
                it.setText(value)
                self._updating = False

    def _on_color_mode(self, i):
        mode = self.combo_color.itemData(i)
        self.btn_custom.setEnabled(mode == "custom")
        self._set("color", mode)

    # ---- list -----------------------------------------------------------------------------
    def reload(self):
        self._updating = True
        self.list.clear()
        els = self.ctl.song.elements
        if self.selected_id and not self.ctl.song.element(self.selected_id):
            self.selected_id = None
        if not self.selected_id and els:
            self.selected_id = els[0].id
        for el in els:
            it = QListWidgetItem(el.name)
            it.setData(Qt.ItemDataRole.UserRole, el.id)
            it.setToolTip(ELEMENT_LABELS.get(el.type, el.type))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if el.visible else Qt.CheckState.Unchecked)
            self.list.addItem(it)
            if el.id == self.selected_id:
                self.list.setCurrentItem(it)
        self._updating = False
        self._reload_drivers()
        self.load_fields()

    def _reload_drivers(self):
        self._updating = True
        self.combo_driver.clear()
        self.combo_driver.addItem("All members (one part each)", "members")
        for m in self.ctl.song.members:
            self.combo_driver.addItem(f"Member: {m.name}", f"m:{m.id}")
        for g in self.ctl.song.groups:
            self.combo_driver.addItem(f"Group: {g.name}", f"g:{g.id}")
        self.combo_driver.addItem("Always on", "always")
        self.combo_driver.addItem("Audio level", "audio")
        self._updating = False

    def _on_select(self, cur, _prev):
        if self._updating or cur is None:
            return
        self.selected_id = cur.data(Qt.ItemDataRole.UserRole)
        self.load_fields()

    def _on_item_changed(self, it):
        if self._updating:
            return
        el = self.ctl.song.element(it.data(Qt.ItemDataRole.UserRole))
        if not el:
            return
        vis = it.checkState() == Qt.CheckState.Checked
        name = it.text().strip()
        if vis != el.visible or (name and name != el.name):
            el.visible = vis
            if name:
                el.name = name
            self._commit()

    # ---- property panel ---------------------------------------------------------------------
    def load_fields(self):
        el = self.element
        self._updating = True
        has = el is not None
        fields = TYPE_FIELDS.get(el.type, set()) if el else set()
        self.empty.setVisible(not has)
        for w in (self.lbl_type, self.edit_name, self.combo_driver, self.color_row):
            self.form.setRowVisible(w, has)
        self.form.setRowVisible(self.combo_fill, "fill" in fields)
        self.form.setRowVisible(self.spin_sides, "sides" in fields)
        for attr, s in self.sliders.items():
            self.form.setRowVisible(s, has and (attr in fields or attr in ("glow", "idle")))
        if el:
            self.lbl_type.setText(ELEMENT_LABELS.get(el.type, el.type))
            if self.edit_name.text() != el.name:
                self.edit_name.setText(el.name)
            self.combo_driver.setCurrentIndex(max(0, self.combo_driver.findData(el.driver)))
            self.combo_color.setCurrentIndex(max(0, self.combo_color.findData(el.color)))
            self.btn_custom.set_rgb(el.custom_rgb)
            self.btn_custom.setEnabled(el.color == "custom")
            self.combo_fill.setCurrentIndex(max(0, self.combo_fill.findData(el.fill)))
            self.spin_sides.setValue(el.sides)
            for attr, s in self.sliders.items():
                s.set_value(getattr(el, attr))
            lab = self.form.labelForField(self.sliders["size"])
            if lab:
                lab.setText(SIZE_LABEL.get(el.type, "Width"))
            lab = self.form.labelForField(self.sliders["idle"])
            if lab:   # for the sun, idle is the resting brightness and 1 - idle is how far a pulse goes
                lab.setText("Resting brightness" if el.type == "sun" else "Idle visibility")
        self._updating = False

    def _on_changed(self, kind):
        if kind in ("all", "elements"):
            self.reload()
        elif kind == "band":
            self._reload_drivers()
            self.load_fields()
