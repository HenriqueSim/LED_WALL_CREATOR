"""Elements tab: build the visual elements of a song (a sunset sun, one hexagon, a single beam...).

Left: the elements of this project (top = front). The checkbox = available in this project: checked
elements get their own folder of lanes in the Pattern Editor, where notes bring them in and out of
the scene. Right: the settings of the selected element - each type has its own.
In the preview above (pulse mode): click an element to select it, drag to move, wheel to resize."""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..model import ELEMENT_LABELS, new_id, preset_element
from .common import ColorButton, LabeledSlider, hint

PRESETS = [
    ("sun", "Sunset sun"), ("beam", "Horizontal beam"), ("hexagon", "Hexagon"), ("polygon", "Polygon"),
    ("ring", "Ring / circle"), ("bar", "Single bar"), ("bars", "Vertical blocks"), ("shafts", "Light shafts"),
    ("horizon", "Horizon lights"),
]

INT_ATTRS = {"sides", "count", "lines", "cuts"}
X = ("x", "Horizontal position", 0, 1, 0.005, "{:.3f}")
Y = ("y", "Vertical position", 0, 1.2, 0.005, "{:.3f}")
ROT = ("rotation", "Rotation", -180, 180, 1, "{:.0f}°")
SPIN = ("spin", "Spin (°/beat)", -30, 30, 0.25, "{:.2f}°")
THICK = ("thickness", "Line thickness", 1, 30, 0.5, "{:.1f}px")
GROW = ("react_scale", "Grow when lit", 0, 0.6, 0.01, "{:.2f}")
FILL = ("fill",)

# shape settings per element type: (attr, label, lo, hi, step, fmt) or ("fill",)
SHAPE_FIELDS = {
    "sun": [("size", "Radius", 0.05, 1.3, 0.005, "{:.3f}"), X, ("y", "Horizon line", 0.3, 1.2, 0.005, "{:.3f}"),
            ("core", "Warm centre", 0, 1, 0.01, "{:.2f}"), ("softness", "Edge softness", 0, 0.3, 0.005, "{:.3f}"),
            ("cuts", "Horizon cuts", 0, 8, 1, "{:.0f}")],
    "polygon": [("sides", "Sides", 3, 12, 1, "{:.0f}"), ("size", "Radius", 0.02, 1.0, 0.005, "{:.3f}"), FILL, THICK,
                X, Y, ROT, SPIN],
    "ring": [("count", "Arcs", 1, 24, 1, "{:.0f}"), ("size", "Radius", 0.02, 1.0, 0.005, "{:.3f}"), FILL, THICK, X, Y, ROT, SPIN],
    "bars": [("count", "Number of bars", 1, 48, 1, "{:.0f}"), ("size", "Total width", 0.01, 1.0, 0.005, "{:.3f}"),
             ("height", "Max height", 0.1, 1.0, 0.01, "{:.2f}"), ("gap", "Gap between bars", 0, 3, 0.02, "{:.2f}"),
             FILL, ("thickness", "Outline", 0, 12, 0.5, "{:.1f}px"), X],
    "shafts": [("count", "Number of shafts", 1, 24, 1, "{:.0f}"), ("size", "Total width", 0.02, 1.0, 0.005, "{:.3f}"),
               ("height", "Max height", 0.1, 1.0, 0.01, "{:.2f}"), THICK, X],
    "horizon": [("count", "Number of lights", 1, 24, 1, "{:.0f}"), ("size", "Width", 0.02, 1.0, 0.005, "{:.3f}"),
                ("height", "Cone height", 0.1, 1.0, 0.01, "{:.2f}"), THICK, X, Y],
    "beam": [("count", "Segments", 1, 24, 1, "{:.0f}"), ("size", "Length", 0.02, 1.0, 0.005, "{:.3f}"),
             ("lines", "Parallel lines", 1, 8, 1, "{:.0f}"), ("spacing", "Line spacing", 0.005, 0.3, 0.005, "{:.3f}"),
             THICK, X, Y, ROT, SPIN],
}
LIGHT_FIELDS = {
    "sun": [("glow", "Halo", 0, 3, 0.05, "{:.2f}"), ("idle", "Resting brightness", 0, 1, 0.01, "{:.2f}"),
            ("react_scale", "Breathing", 0, 0.1, 0.002, "{:.3f}")],
}
DEFAULT_LIGHT = [("glow", "Glow", 0, 3, 0.05, "{:.2f}"), ("idle", "Idle glow in scene", 0, 1, 0.01, "{:.2f}")]
PARTS_HINT = {
    "sun": "One part: lit by the brightest of its lanes.",
    "polygon": "Each side is a part.",
}


def _light_fields(t):
    if t in LIGHT_FIELDS:
        return LIGHT_FIELDS[t]
    extra = [GROW] if t in ("polygon", "ring", "beam") else []
    return DEFAULT_LIGHT + extra


class ElementsTab(QWidget):
    previewModeChanged = pyqtSignal(bool)

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
        self.chk_pulse = QCheckBox("Preview: every element pulsing")
        self.chk_pulse.setChecked(True)
        self.chk_pulse.setToolTip("Unchecked: the preview shows the song at the playhead instead")
        self.chk_pulse.toggled.connect(self.previewModeChanged.emit)
        ll.addWidget(self.chk_pulse)
        ll.addWidget(hint("Checked = available in this project: it gets its own folder of lanes in the Pattern "
                          "Editor, where notes bring it in and out of the scene. Click an element in the preview "
                          "to select it, drag to move, mouse wheel to resize. Double-click a name to rename."))

        # ---- properties --------------------------------------------------------------------
        props = QWidget()
        pv = QVBoxLayout(props)
        top = QFormLayout()
        self.lbl_type = QLabel()
        top.addRow("Type", self.lbl_type)
        self.edit_name = QLineEdit()
        self.edit_name.textEdited.connect(lambda t: self._set("name", t, refresh_list=True))
        top.addRow("Name", self.edit_name)
        crow = QHBoxLayout()
        self.combo_color = QComboBox()
        for key, label in (("main", "Song colour"), ("accent", "Accent colour"), ("custom", "Custom")):
            self.combo_color.addItem(label, key)
        self.combo_color.currentIndexChanged.connect(self._on_color_mode)
        self.btn_custom = ColorButton((255, 255, 255))
        self.btn_custom.colorChanged.connect(lambda rgb: self._set("custom_rgb", tuple(rgb)))
        crow.addWidget(self.combo_color, 1)
        crow.addWidget(self.btn_custom, 1)
        top.addRow("Colour", crow)
        self.lbl_parts = hint("")
        top.addRow(self.lbl_parts)
        self.box_top = QWidget()
        self.box_top.setLayout(top)
        pv.addWidget(self.box_top)
        self.grp_shape = QGroupBox("Shape")
        self.grp_light = QGroupBox("Light")
        for grp in (self.grp_shape, self.grp_light):
            gl = QVBoxLayout(grp)
            gl.setContentsMargins(4, 4, 4, 4)
            grp.inner = None
        cols = QHBoxLayout()
        cols.addWidget(self.grp_shape, 1)
        cols.addWidget(self.grp_light, 1)
        pv.addLayout(cols)
        pv.addStretch(1)
        self.empty = QLabel("Add an element with  Add  — for example a sunset sun or a single beam.")
        self.empty.setObjectName("hint")
        pv.addWidget(self.empty)
        self.sliders: dict = {}
        self.combo_fill = None
        self._built_for = None      # (element id, type) the dynamic forms were built for
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
        for p in self.ctl.song.patterns:      # the copy starts with the same lanes / notes
            p.notes += [type(n)(n.lane.replace(f"e:{src.id}|", f"e:{el.id}|", 1), n.start, n.length, n.velocity)
                        for n in p.notes if n.lane.startswith(f"e:{src.id}|")]
        self.selected_id = el.id
        self._commit()

    def delete(self):
        el = self.element
        if not el:
            return
        els = self.ctl.song.elements
        i = els.index(el)
        self.ctl.song.remove_element(el.id)
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
        self.load_fields()

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
    def _build_forms(self, el):
        """(Re)build the Shape / Light forms for this element's type."""
        self.sliders, self.combo_fill = {}, None
        for grp, specs in ((self.grp_shape, SHAPE_FIELDS.get(el.type, [])), (self.grp_light, _light_fields(el.type))):
            if grp.inner is not None:
                grp.inner.setParent(None)
                grp.inner.deleteLater()
            grp.inner = QWidget()
            grp.layout().addWidget(grp.inner)
            form = QFormLayout(grp.inner)
            for spec in specs:
                if spec[0] == "fill":
                    cb = QComboBox()
                    for key, label in (("outline", "Outline (neon tube)"), ("filled", "Filled"), ("gradient", "Gradient fill")):
                        cb.addItem(label, key)
                    cb.currentIndexChanged.connect(lambda i, c=cb: self._set("fill", c.itemData(i)))
                    form.addRow("Fill", cb)
                    self.combo_fill = cb
                    continue
                attr, label, lo, hi, step, fmt = spec
                sl = LabeledSlider(lo, hi, lo, step, fmt)
                sl.valueChanged.connect(lambda v, a=attr: self._set(a, int(round(v)) if a in INT_ATTRS else v))
                form.addRow(label, sl)
                self.sliders[attr] = sl
        self._built_for = (el.id, el.type)

    def load_fields(self):
        el = self.element
        self._updating = True
        has = el is not None
        self.empty.setVisible(not has)
        for w in (self.box_top, self.grp_shape, self.grp_light):
            w.setVisible(has)
        if el:
            if self._built_for != (el.id, el.type):
                self._build_forms(el)
            self.lbl_type.setText(ELEMENT_LABELS.get(el.type, el.type))
            if self.edit_name.text() != el.name:
                self.edit_name.setText(el.name)
            self.combo_color.setCurrentIndex(max(0, self.combo_color.findData(el.color)))
            self.btn_custom.set_rgb(el.custom_rgb)
            self.btn_custom.setEnabled(el.color == "custom")
            if self.combo_fill is not None:
                self.combo_fill.setCurrentIndex(max(0, self.combo_fill.findData(el.fill)))
            for attr, sl in self.sliders.items():
                sl.set_value(getattr(el, attr))
            n = {"sun": 1, "polygon": el.sides}.get(el.type, el.count)
            m = len(self.ctl.song.members)
            share = PARTS_HINT.get(el.type) or (
                f"{n} part{'s' if n != 1 else ''} shared between {m} members"
                + (" — each part lights when any of its members does." if n < m else "."))
            self.lbl_parts.setText(share)
        self._updating = False

    def _on_changed(self, kind):
        if kind in ("all", "elements", "band"):
            self.reload()
