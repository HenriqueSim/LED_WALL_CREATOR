"""Elements tab: build the visual elements of a song (a sunset sun, one hexagon, a single beam...).

Left: the elements of this project (top = front). The checkbox = available in this project: checked
elements get their own folder of lanes in the Pattern Editor, where notes bring them in and out of
the scene. Right: the settings of the selected element - each type has its own.
In the preview above (pulse mode): click an element to select it, drag to move, wheel to resize."""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from ..model import ELEMENT_LABELS, SHAPE_KINDS, SHAPE_LABELS, element_defaults, new_id, new_shape, preset_element
from .band_name_panel import BandNamePanel
from .common import roomy, ColorButton, LabeledSlider, add_slider_row, hint

LOGO_ID = "__logo__"     # list entry for the band name layer

PRESETS = [
    ("custom", "Custom (build from shapes)"), ("custom_rays", "Custom example: rising rays"),
    ("sun", "Sunset sun"), ("beam", "Horizontal beam"), ("hexagon", "Hexagon"), ("polygon", "Polygon"),
    ("ring", "Ring / circle"), ("bar", "Single bar"), ("bars", "Vertical blocks"), ("shafts", "Light shafts"),
    ("horizon", "Horizon lights"), ("haze", "Haze (soft wash of light)"),
]

INT_ATTRS = {"sides", "count", "lines", "cuts", "dashes"}
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
    "custom": [("size", "Scale", 0.02, 1.5, 0.005, "{:.3f}"), X, Y, ROT, SPIN],
    "haze": [("size", "Spread", 0.05, 1.5, 0.01, "{:.2f}"), ("height", "Reach", 0.05, 1.2, 0.01, "{:.2f}"), X,
             ("y", "Rises from", 0, 1.2, 0.01, "{:.2f}")],
}

# settings of one shape inside a custom element, per shape kind
_SX = ("x", "Offset X", -3, 3, 0.01, "{:.2f}")
_SY = ("y", "Offset Y", -3, 3, 0.01, "{:.2f}")
_SROT = ("rot", "Angle", -180, 180, 1, "{:.0f}°")
_STH = ("thickness", "Line thickness", 0.5, 30, 0.5, "{:.1f}px")
_SFILL = ("filled",)
SHAPE_EDIT_FIELDS = {
    "line": [("size", "Length", 0.02, 6, 0.01, "{:.2f}"), _SX, _SY, _SROT, _STH],
    "dashes": [("size", "Length", 0.02, 6, 0.01, "{:.2f}"), ("dashes", "Dashes", 1, 40, 1, "{:.0f}"), _SX, _SY, _SROT, _STH],
    "rect": [("size", "Width", 0.01, 6, 0.01, "{:.2f}"), ("h", "Height", 0.01, 6, 0.01, "{:.2f}"), _SFILL, _SX, _SY, _SROT, _STH],
    "circle": [("size", "Radius", 0.01, 3, 0.01, "{:.2f}"), _SFILL, _SX, _SY, _STH],
    "arc": [("size", "Radius", 0.01, 3, 0.01, "{:.2f}"), ("sweep", "Sweep", 1, 360, 1, "{:.0f}°"), _SFILL, _SX, _SY,
            ("rot", "Start angle", -180, 180, 1, "{:.0f}°"), _STH],
    "polygon": [("sides", "Sides", 3, 12, 1, "{:.0f}"), ("size", "Radius", 0.01, 3, 0.01, "{:.2f}"), _SFILL, _SX, _SY, _SROT, _STH],
    "rays": [("dashes", "Number of rays", 1, 48, 1, "{:.0f}"), ("size", "Outer radius", 0.05, 6, 0.01, "{:.2f}"),
             ("h", "Inner radius", 0, 6, 0.01, "{:.2f}"), ("sweep", "Spread", 0, 360, 1, "{:.0f}°"), _SX, _SY,
             ("rot", "Start angle", -180, 180, 1, "{:.0f}°"), _STH],
}
LIGHT_FIELDS = {
    "haze": [("glow", "Strength", 0, 8, 0.05, "{:.2f}"), ("music", "Music pulse", 0, 4, 0.05, "{:.2f}")],
    "sun": [("glow", "Halo", 0, 3, 0.05, "{:.2f}"), ("idle", "Resting brightness", 0, 1, 0.01, "{:.2f}"),
            ("react_scale", "Breathing", 0, 0.1, 0.002, "{:.3f}")],
}
DEFAULT_LIGHT = [("glow", "Glow", 0, 3, 0.05, "{:.2f}"), ("idle", "Idle glow in scene", 0, 1, 0.01, "{:.2f}")]
PARTS_HINT = {
    "sun": "One part: lit by the brightest of its lanes.",
    "haze": "No parts: put notes on its Haze amount lane in a pattern to bring it in (velocity = how much "
            "haze). It brightens while the other elements are lit and breathes with the music.",
    "polygon": "Each side is a part.",
    "custom": "Each shape is a part, shared out between the members in list order.",
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
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Pattern"))
        self.combo_pattern = QComboBox()
        self.combo_pattern.setToolTip("The ticked elements are the ones this pattern uses")
        self.combo_pattern.currentIndexChanged.connect(self._on_pattern_combo)
        prow.addWidget(self.combo_pattern, 1)
        ll.addLayout(prow)
        self.list = roomy(QListWidget())
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
        self.chk_pulse = QCheckBox("Preview: the selected element, pulsing")
        self.chk_pulse.setChecked(True)
        self.chk_pulse.setToolTip("Unchecked: the preview shows the song at the playhead instead")
        self.chk_pulse.toggled.connect(self.previewModeChanged.emit)
        ll.addWidget(self.chk_pulse)
        ll.addWidget(hint("Ticked = used in the selected pattern: it gets a folder of lanes in that pattern, "
                          "where notes bring it in and out of the scene. The settings are shared: an element looks "
                          "the same in every pattern that uses it. Click an element (or the band name) in the "
                          "preview to select it, drag to move, wheel to resize. Double-click a name to rename."))

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
        # custom elements: the list of shapes and the selected shape's settings
        self.grp_shapes = QGroupBox("Shapes (each one is a part)")
        gs = QHBoxLayout(self.grp_shapes)
        sl_col = QVBoxLayout()
        self.shape_list = QListWidget()
        self.shape_list.setMaximumWidth(230)
        self.shape_list.currentRowChanged.connect(self._on_shape_row)
        sl_col.addWidget(self.shape_list, 1)
        srow = QHBoxLayout()
        b_add = QPushButton("Add shape")
        smenu = QMenu(self)
        for kind, label in SHAPE_KINDS:
            smenu.addAction(label).triggered.connect(lambda _=False, k=kind: self.add_shape(k))
        b_add.setMenu(smenu)
        srow.addWidget(b_add)
        for text, fn in (("Copy", self.duplicate_shape), ("Delete", self.delete_shape)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            srow.addWidget(b)
        sl_col.addLayout(srow)
        srow2 = QHBoxLayout()
        for text, d in (("▲", -1), ("▼", 1)):
            b = QPushButton(text)
            b.setToolTip("Move up / down (changes which member lights it)")
            b.clicked.connect(lambda _=False, dd=d: self.move_shape(dd))
            srow2.addWidget(b)
        sl_col.addLayout(srow2)
        sl_col.addWidget(hint("Click a shape in the preview to pick it, drag to move, wheel to resize."))
        gs.addLayout(sl_col)
        self.shape_box = QWidget()
        self.shape_box.setLayout(QVBoxLayout())
        self.shape_box.layout().setContentsMargins(0, 0, 0, 0)
        self.shape_box.inner = None
        gs.addWidget(self.shape_box, 1)
        pv.addWidget(self.grp_shapes)
        self.selected_shape: int | None = None
        self.shape_sliders: dict = {}
        self._shape_built_for = None
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
        self.band_panel = BandNamePanel(ctl)
        bscroll = QScrollArea()
        bscroll.setWidget(self.band_panel)
        bscroll.setWidgetResizable(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(scroll)
        self.stack.addWidget(bscroll)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(left)
        lay.addWidget(self.stack, 1)
        ctl.changed.connect(self._on_changed)
        self.reload()

    # ---- model helpers ------------------------------------------------------------------
    @property
    def element(self):
        return self.ctl.song.element(self.selected_id) if self.selected_id and self.selected_id != LOGO_ID else None

    @property
    def logo_selected(self) -> bool:
        return self.selected_id == LOGO_ID

    def select_logo(self):
        self.select(LOGO_ID)

    def select(self, eid: str | None):
        if eid != self.selected_id:
            self.selected_shape = None
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
        pat = self.ctl.current_pattern          # new elements go into the selected pattern only
        for p in self.ctl.song.patterns:
            if p.elements is None:
                p.elements = [e.id for e in self.ctl.song.elements if e.id != el.id]
        if pat is not None:
            pat.elements.append(el.id)
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
        for p in self.ctl.song.patterns:      # the copy starts with the same lanes / notes, in the same patterns
            p.notes += [type(n)(n.lane.replace(f"e:{src.id}|", f"e:{el.id}|", 1), n.start, n.length, n.velocity)
                        for n in p.notes if n.lane.startswith(f"e:{src.id}|")]
            if p.elements is not None and src.id in p.elements:
                p.elements.append(el.id)
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

    def _set(self, attr, value, refresh_list=False, reset=False):
        el = self.element
        if self._updating or not el:
            return
        setattr(el, attr, value)
        self._commit(f"{el.id}:{attr}" + (":reset" if reset else ""))
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
        if self.selected_id and self.selected_id != LOGO_ID and not self.ctl.song.element(self.selected_id):
            self.selected_id = None
        if not self.selected_id:
            self.selected_id = els[0].id if els else LOGO_ID
        pat = self.ctl.current_pattern
        self.combo_pattern.blockSignals(True)
        self.combo_pattern.clear()
        for p in self.ctl.song.patterns:
            self.combo_pattern.addItem(p.name, p.id)
        self.combo_pattern.setCurrentIndex(max(0, self.combo_pattern.findData(pat.id if pat else None)))
        self.combo_pattern.blockSignals(False)
        it = QListWidgetItem("★ Band name")
        it.setData(Qt.ItemDataRole.UserRole, LOGO_ID)
        it.setToolTip("The band name layer: text, font, layout and timing")
        it.setForeground(QColor(255, 215, 140))
        f = QFont(it.font())
        f.setBold(True)
        it.setFont(f)
        self.list.addItem(it)
        if self.selected_id == LOGO_ID:
            self.list.setCurrentItem(it)
        for el in els:
            it = QListWidgetItem(el.name)
            it.setData(Qt.ItemDataRole.UserRole, el.id)
            it.setToolTip(ELEMENT_LABELS.get(el.type, el.type))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
            used = pat is not None and pat.uses(el.id)
            it.setCheckState(Qt.CheckState.Checked if used else Qt.CheckState.Unchecked)
            if not used:
                it.setForeground(QColor(130, 130, 130))
            self.list.addItem(it)
            if el.id == self.selected_id:
                self.list.setCurrentItem(it)
        self._updating = False
        self.load_fields()

    def _on_select(self, cur, _prev):
        if self._updating or cur is None:
            return
        if cur.data(Qt.ItemDataRole.UserRole) != self.selected_id:
            self.selected_shape = None
        self.selected_id = cur.data(Qt.ItemDataRole.UserRole)
        self.load_fields()

    def _on_item_changed(self, it):
        if self._updating:
            return
        el = self.ctl.song.element(it.data(Qt.ItemDataRole.UserRole))
        if not el:
            return
        use = it.checkState() == Qt.CheckState.Checked
        name = it.text().strip()
        pat = self.ctl.current_pattern
        if pat is not None and use != pat.uses(el.id):
            if pat.elements is None:
                pat.elements = [e.id for e in self.ctl.song.elements]
            if use:
                pat.elements.append(el.id)
            else:
                pat.elements.remove(el.id)
            self._commit()
            return
        if name and name != el.name:
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
                sl.valueChanged.connect(lambda v, a=attr, w=sl: self._set(a, int(round(v)) if a in INT_ATTRS else v,
                                                                          reset=w.resetting))
                add_slider_row(form, label, sl)
                self.sliders[attr] = sl
        self._built_for = (el.id, el.type)

    def load_fields(self):
        if self.logo_selected:
            self.stack.setCurrentIndex(1)
            self.band_panel.reload()
            return
        self.stack.setCurrentIndex(0)
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
            defaults = element_defaults(el)
            for attr, sl in self.sliders.items():
                sl.set_value(getattr(el, attr))
                sl.set_default(getattr(defaults, attr))
            n = {"sun": 1, "polygon": el.sides}.get(el.type, el.count)
            m = len(self.ctl.song.members)
            share = PARTS_HINT.get(el.type) or (
                f"{n} part{'s' if n != 1 else ''} shared between {m} members"
                + (" — each part lights when any of its members does." if n < m else "."))
            self.lbl_parts.setText(share)
        self._updating = False
        self._load_shapes(el if has and el.type == "custom" else None)

    def _on_pattern_combo(self, i):
        pid = self.combo_pattern.itemData(i)
        if pid and pid != self.ctl.current_pattern_id:
            self.ctl.set_current_pattern(pid)

    # ---- custom element shapes ----------------------------------------------------------------
    def _load_shapes(self, el):
        self.grp_shapes.setVisible(el is not None)
        if el is None:
            return
        self._updating = True
        self.shape_list.clear()
        for i, sh in enumerate(el.shapes):
            self.shape_list.addItem(f"{i + 1}. {SHAPE_LABELS.get(sh.get('kind'), sh.get('kind'))}")
        if self.selected_shape is not None and not (0 <= self.selected_shape < len(el.shapes)):
            self.selected_shape = None
        if self.selected_shape is None and el.shapes:
            self.selected_shape = 0
        if self.selected_shape is not None:
            self.shape_list.setCurrentRow(self.selected_shape)
        self._updating = False
        self._load_shape_fields()

    def _on_shape_row(self, row):
        if self._updating:
            return
        self.selected_shape = row if row >= 0 else None
        self._load_shape_fields()

    def select_shape(self, idx: int | None):
        self.selected_shape = idx
        self._load_shapes(self.element)

    @property
    def shape(self):
        el = self.element
        if el and el.type == "custom" and self.selected_shape is not None and 0 <= self.selected_shape < len(el.shapes):
            return el.shapes[self.selected_shape]
        return None

    def _build_shape_form(self, el, idx, sh):
        if self.shape_box.inner is not None:
            self.shape_box.inner.setParent(None)
            self.shape_box.inner.deleteLater()
        self.shape_box.inner = QWidget()
        self.shape_box.layout().addWidget(self.shape_box.inner)
        form = QFormLayout(self.shape_box.inner)
        self.shape_sliders, self.chk_shape_fill = {}, None
        for spec in SHAPE_EDIT_FIELDS.get(sh.get("kind"), []):
            if spec[0] == "filled":
                cb = QCheckBox("Filled")
                cb.toggled.connect(lambda v: self._set_shape("filled", v))
                form.addRow(cb)
                self.chk_shape_fill = cb
                continue
            attr, label, lo, hi, step, fmt = spec
            sl = LabeledSlider(lo, hi, lo, step, fmt)
            sl.valueChanged.connect(lambda v, a=attr, w=sl: self._set_shape(a, int(round(v)) if a in INT_ATTRS else v,
                                                                            reset=w.resetting))
            add_slider_row(form, label, sl)
            self.shape_sliders[attr] = sl
        self._shape_built_for = (el.id, idx, sh.get("kind"))

    def _load_shape_fields(self):
        el, sh, idx = self.element, self.shape, self.selected_shape
        self.shape_box.setVisible(sh is not None)
        if sh is None:
            return
        self._updating = True
        if self._shape_built_for != (el.id, idx, sh.get("kind")):
            self._build_shape_form(el, idx, sh)
        defaults = new_shape(sh.get("kind"))
        for attr, sl in self.shape_sliders.items():
            sl.set_value(sh.get(attr, defaults[attr]))
            sl.set_default(defaults[attr])
        if self.chk_shape_fill is not None:
            self.chk_shape_fill.setChecked(bool(sh.get("filled")))
        self._updating = False

    def _set_shape(self, attr, value, reset=False):
        el, sh = self.element, self.shape
        if self._updating or sh is None:
            return
        sh[attr] = value
        self._commit(f"{el.id}:shape{self.selected_shape}:{attr}" + (":reset" if reset else ""))

    def add_shape(self, kind):
        el = self.element
        if el is None or el.type != "custom":
            return
        el.shapes.append(new_shape(kind))
        self.selected_shape = len(el.shapes) - 1
        self._commit()

    def duplicate_shape(self):
        el, sh = self.element, self.shape
        if sh is None:
            return
        c = copy.deepcopy(sh)
        c["x"] = c.get("x", 0.0) + 0.1
        el.shapes.insert(self.selected_shape + 1, c)
        self.selected_shape += 1
        self._commit()

    def delete_shape(self):
        el, sh = self.element, self.shape
        if sh is None:
            return
        del el.shapes[self.selected_shape]
        self.selected_shape = min(self.selected_shape, len(el.shapes) - 1) if el.shapes else None
        self._commit()

    def move_shape(self, d):
        el, i = self.element, self.selected_shape
        if self.shape is None or not (0 <= i + d < len(el.shapes)):
            return
        el.shapes[i], el.shapes[i + d] = el.shapes[i + d], el.shapes[i]
        self.selected_shape = i + d
        self._commit()

    def _on_changed(self, kind):
        if kind in ("all", "elements", "band", "patterns"):
            self.reload()
        if kind in ("all", "look") and self.logo_selected:
            self.band_panel.reload()
