"""Band name settings (shown in the Elements tab when "Band name" is selected): the text, the font,
the layout and its own fade / glow timing. The name is shown wherever the global "Band Name" lane
has notes."""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget,
)

from ..model import Look
from ..resources import load_bundled_fonts
from .common import LabeledSlider, add_slider_row, hint

_DEFAULTS = Look()
LAYOUTS = [("line", "One line"), ("stacked", "Stacked: 2nd line shifted right"), ("two_lines", "Two lines, centred")]
CASES = [("asis", "As typed"), ("lower", "lowercase"), ("upper", "UPPERCASE")]
COLORS = [("accent", "Accent colour"), ("main", "Song colour"), ("white", "White"),
          ("cutout", "Dark cut-out (silhouette)")]


class BandNamePanel(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False
        self._sliders: dict = {}

        g_text = QGroupBox("Band name")
        ft = QFormLayout(g_text)
        self.edit_text = QLineEdit()
        self.edit_text.setPlaceholderText("your band's name")
        self.edit_text.textEdited.connect(lambda t: self._set("logo_text", t))
        ft.addRow("Name", self.edit_text)
        frow = QHBoxLayout()
        self.combo_font = QFontComboBox()
        self.combo_font.currentFontChanged.connect(lambda f: self._set("logo_font", f.family()))
        frow.addWidget(self.combo_font, 1)
        self.combo_quick = QComboBox()        # the fonts that ship with the app (same on every computer)
        self.combo_quick.addItem("Bundled fonts…", "")
        for fam in load_bundled_fonts():
            self.combo_quick.addItem(fam, fam)
        self.combo_quick.currentIndexChanged.connect(self._on_quick_font)
        frow.addWidget(self.combo_quick)
        ft.addRow("Font", frow)
        self.combo_layout = self._combo(ft, "Layout", LAYOUTS, "logo_layout")
        self.chk_dot = QCheckBox("Full stop after the first line")
        self.chk_dot.toggled.connect(lambda v: self._set("logo_dot", v))
        ft.addRow(self.chk_dot)
        self.combo_case = self._combo(ft, "Letters", CASES, "logo_case")
        self.combo_color = self._combo(ft, "Colour", COLORS, "logo_color")
        ft.addRow(hint("Bundled fonts look the same on every computer; any installed font works too. "
                       "In the preview you can drag the name to move it and use the mouse wheel to resize it."))

        g_place = QGroupBox("Size && position")
        fp = QFormLayout(g_place)
        self._slider(fp, "Size", "logo_size", 0.08, 0.9, 0.01)
        self._slider(fp, "Horizontal position", "logo_x", 0.05, 0.95, 0.005, "{:.3f}")
        self._slider(fp, "Vertical position", "logo_y", 0.1, 0.95, 0.005, "{:.3f}")
        self._slider(fp, "2nd line shift", "logo_offset", -0.6, 1.0, 0.01)

        g_light = QGroupBox("Light && timing")
        fl = QFormLayout(g_light)
        self._slider(fl, "Fade in", "logo_attack_ms", 0, 4000, 10, "{:.0f}ms")
        self._slider(fl, "Fade out", "logo_release_ms", 0, 8000, 20, "{:.0f}ms")
        self._slider(fl, "Peak glow", "logo_peak", 0, 3, 0.05)
        self._slider(fl, "Peak decay", "logo_peak_decay_ms", 50, 4000, 10, "{:.0f}ms")
        self._slider(fl, "Glow radius", "logo_glow_radius", 4, 120, 1, "{:.0f}px")
        self._slider(fl, "Always visible", "logo_idle", 0, 1, 0.01)
        fl.addRow(hint("The name shows wherever the Global → Band Name lane has notes (note velocity = "
                       "brightness). Each note start fires a glow peak that fades with its own timing."))

        cols = QHBoxLayout()
        cols.addWidget(g_text, 1)
        cols.addWidget(g_place, 1)
        cols.addWidget(g_light, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(cols)
        lay.addStretch(1)
        self._form_place = fp
        self.reload()

    # ---- helpers --------------------------------------------------------------------------
    def _combo(self, form, label, items, attr):
        cb = QComboBox()
        for key, text in items:
            cb.addItem(text, key)
        cb.currentIndexChanged.connect(lambda i: self._set(attr, cb.itemData(i)))
        form.addRow(label, cb)
        return cb

    def _slider(self, form, label, attr, lo, hi, step, fmt="{:.2f}"):
        s = LabeledSlider(lo, hi, getattr(self.ctl.song.look, attr), step, fmt, default=getattr(_DEFAULTS, attr))
        s.valueChanged.connect(lambda v: self._set(attr, v, key=attr + (":reset" if s.resetting else "")))
        add_slider_row(form, label, s)
        self._sliders[attr] = s

    def _set(self, attr, value, key=None):
        if self._updating:
            return
        setattr(self.ctl.song.look, attr, value)
        self.ctl.commit("look", key or attr)
        if attr == "logo_layout":
            self._update_visibility()

    def _on_quick_font(self, i):
        fam = self.combo_quick.itemData(i)
        if fam:
            self.combo_font.setCurrentFont(QFont(fam))   # emits currentFontChanged -> _set
            self.combo_quick.setCurrentIndex(0)

    def _update_visibility(self):
        stacked = self.ctl.song.look.logo_layout == "stacked"
        self.chk_dot.setVisible(stacked)
        self._form_place.setRowVisible(self._sliders["logo_offset"], stacked)

    def reload(self):
        self._updating = True
        lk = self.ctl.song.look
        if self.edit_text.text() != lk.logo_text:
            self.edit_text.setText(lk.logo_text)
        self.combo_font.setCurrentFont(QFont(lk.logo_font))
        for cb, attr in ((self.combo_layout, "logo_layout"), (self.combo_case, "logo_case"),
                         (self.combo_color, "logo_color")):
            cb.setCurrentIndex(max(0, cb.findData(getattr(lk, attr))))
        self.chk_dot.setChecked(lk.logo_dot)
        for attr, s in self._sliders.items():
            s.set_value(getattr(lk, attr))
        self._updating = False
        self._update_visibility()
