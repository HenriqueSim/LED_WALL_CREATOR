"""Look & Song: audio + tempo, THE song colour, the global light settings and the lyrics."""

from __future__ import annotations

import os

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..model import Look
from .common import ColorButton, LabeledSlider, add_slider_row, hint

_LOOK_DEFAULTS = Look()

SONG_COLORS = [
    ("Amber", (255, 168, 40)), ("Gold", (255, 205, 60)), ("Red", (235, 50, 35)), ("Rose", (255, 80, 130)),
    ("Violet", (160, 90, 255)), ("Blue", (60, 120, 255)), ("Cyan", (40, 210, 255)), ("Green", (90, 230, 110)),
]
PASTEL_COLORS = [
    ("Apricot", (255, 186, 128)), ("Peach", (255, 170, 140)), ("Blush", (248, 160, 175)), ("Butter", (255, 222, 150)),
    ("Lilac", (198, 170, 245)), ("Sky", (160, 198, 250)), ("Mint", (165, 228, 200)), ("Sand", (235, 200, 165)),
]


class LookTab(QWidget):
    loadAudioRequested = pyqtSignal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False
        self._widgets = {}   # Look attr -> (widget, kind)
        lk = ctl.song.look

        # ---- song -----------------------------------------------------------------
        g_song = QGroupBox("Song")
        fs = QFormLayout(g_song)
        self.btn_load = QPushButton("Load Audio File…")
        self.btn_load.clicked.connect(self.loadAudioRequested.emit)
        fs.addRow(self.btn_load)
        self.lbl_file = QLabel("No audio loaded")
        self.lbl_file.setObjectName("hint")
        self.lbl_file.setWordWrap(True)
        fs.addRow(self.lbl_file)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        fs.addRow(self.progress)
        bpm_row = QHBoxLayout()
        self.spin_bpm = QDoubleSpinBox()
        self.spin_bpm.setRange(40, 250)
        self.spin_bpm.setDecimals(2)
        self.spin_bpm.valueChanged.connect(self._on_bpm)
        bpm_row.addWidget(self.spin_bpm, 1)
        for text, fn in (("½", lambda: self.spin_bpm.setValue(self.spin_bpm.value() / 2)),
                         ("×2", lambda: self.spin_bpm.setValue(self.spin_bpm.value() * 2)),
                         ("Auto", self._auto_bpm)):
            b = QPushButton(text)
            b.setFixedWidth(52)
            b.setProperty("compact", True)
            b.clicked.connect(fn)
            bpm_row.addWidget(b)
        fs.addRow("BPM", bpm_row)
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(-60, 600)
        self.spin_offset.setDecimals(3)
        self.spin_offset.setSingleStep(0.01)
        self.spin_offset.setSuffix(" s")
        self.spin_offset.setToolTip("Where bar 1 starts in the audio (also on the Master Track: drag the "
                                    "waveform or use the bar at the bottom)")
        self.spin_offset.valueChanged.connect(self._on_offset)
        fs.addRow("Bar 1 starts at", self.spin_offset)
        self.btn_main = ColorButton(lk.main_rgb)
        self.btn_main.colorChanged.connect(lambda rgb: self._set("main_rgb", tuple(rgb)))
        fs.addRow("Song colour", self.btn_main)
        for title, palette in (("Vivid", SONG_COLORS), ("Pastel", PASTEL_COLORS)):
            sw = QHBoxLayout()
            sw.setSpacing(3)
            for name, rgb in palette:
                b = QPushButton()
                b.setFixedSize(26, 22)
                b.setToolTip(name)
                b.setStyleSheet(f"background: rgb{rgb}; border: 1px solid #444;")
                b.clicked.connect(lambda _=False, c=rgb: (self.btn_main.set_rgb(c), self._set("main_rgb", c)))
                sw.addWidget(b)
            fs.addRow(title, sw)
        self.btn_accent = ColorButton(lk.accent_rgb)
        self.btn_accent.colorChanged.connect(lambda rgb: self._set("accent_rgb", tuple(rgb)))
        fs.addRow("Accent (flash)", self.btn_accent)
        fs.addRow(hint("One colour per song, shared by every member. The block gradient, the glow and the "
                       "haze are all derived from it."))

        # ---- background ----------------------------------------------------------------
        g_bg = QGroupBox("Light && motion (all elements)")
        fb = QFormLayout(g_bg)
        fb.addRow(hint("The shapes themselves are built in the Elements tab."))
        self._slider(fb, "Glow radius", "glow_radius", 2, 90, 1, "{:.0f}px")
        self._slider(fb, "Glow intensity", "glow_intensity", 0, 3, 0.05)
        self._slider(fb, "Attack", "attack_ms", 0, 1000, 5, "{:.0f}ms")
        self._slider(fb, "Release", "release_ms", 0, 3000, 10, "{:.0f}ms")
        self._slider(fb, "Audio reactivity", "audio_react", 0, 1, 0.01)
        self._slider(fb, "Beat sensitivity", "sensitivity", 0, 1, 0.01)
        fb.addRow(hint("The soft haze of light is an element now: add a Haze in the Elements tab and give it notes "
                       "in the patterns where you want it."))
        self.combo_strobe = QComboBox()
        for n in (1, 2, 4, 8):
            self.combo_strobe.addItem(f"{n} per beat", n)
        self.combo_strobe.currentIndexChanged.connect(lambda i: self._set("strobe_rate", self.combo_strobe.itemData(i)))
        fb.addRow("Strobe rate", self.combo_strobe)

        # ---- lyrics ------------------------------------------------------------------------------
        g_lyr = QGroupBox("Lyrics")
        fy = QFormLayout(g_lyr)
        self._slider(fy, "Room at the top", "headroom", 0.1, 0.4, 0.01)
        self._slider(fy, "Lyrics size", "lyrics_size", 0.05, 0.2, 0.005, "{:.3f}")
        self.chk_lyr_font = QCheckBox("Same font as the band name")
        self.chk_lyr_font.toggled.connect(self._on_lyr_same)
        fy.addRow(self.chk_lyr_font)
        self.combo_lyr_font = QFontComboBox()
        self.combo_lyr_font.currentFontChanged.connect(
            lambda f: self._set("lyrics_font", f.family()) if not self.chk_lyr_font.isChecked() else None)
        fy.addRow("Font", self.combo_lyr_font)
        fy.addRow(hint("Lyrics are added on the Master Track (Lyrics lane). The band name, its font and "
                       "layout are set in the Elements tab (★ Band name)."))

        cols = QHBoxLayout()
        for g in (g_song, g_bg, g_lyr):
            cols.addWidget(g, 1)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.addLayout(cols)
        v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        ctl.changed.connect(lambda kind: self.reload() if kind in ("all", "audio", "look") else None)
        self.reload()

    # ---- helpers ----------------------------------------------------------------------------
    def _slider(self, form, label, attr, lo, hi, step, fmt="{:.2f}", integer=False):
        s = LabeledSlider(lo, hi, getattr(self.ctl.song.look, attr), step, fmt, default=getattr(_LOOK_DEFAULTS, attr))
        s.valueChanged.connect(lambda v: self._set(attr, int(round(v)) if integer else v,
                                                   key=attr + (":reset" if s.resetting else "")))
        add_slider_row(form, label, s)
        self._widgets[attr] = s

    def _set(self, attr, value, key=None):
        if self._updating:
            return
        setattr(self.ctl.song.look, attr, value)
        self.ctl.commit("look", key or attr)

    def _on_lyr_same(self, same):
        self.combo_lyr_font.setEnabled(not same)
        if not self._updating:
            self._set("lyrics_font", "" if same else self.combo_lyr_font.currentFont().family())

    def _on_bpm(self, v):
        if not self._updating and abs(v - self.ctl.song.bpm) > 1e-6:
            self.ctl.song.set_timing(bpm=v)
            self.ctl.commit("look", "bpm")
            self.ctl.changed.emit("arrangement")

    def _on_offset(self, v):
        if not self._updating and abs(v - self.ctl.song.beat_offset) > 1e-9:
            self.ctl.song.set_timing(beat_offset=v)
            self.ctl.commit("look", "offset")
            self.ctl.changed.emit("arrangement")

    def _auto_bpm(self):
        a = self.ctl.analysis
        if a:
            self.spin_bpm.setValue(a.bpm)
            self.spin_offset.setValue(a.detected_offset)

    def set_loading(self, value: int | None, msg: str = ""):
        self.progress.setVisible(value is not None)
        self.btn_load.setEnabled(value is None)
        if value is not None:
            self.progress.setValue(value)
            self.lbl_file.setText(msg)

    def reload(self):
        self._updating = True
        song, lk = self.ctl.song, self.ctl.song.look
        a = self.ctl.analysis
        if a:
            self.lbl_file.setText(f"{os.path.basename(a.path)}\n{a.duration:.1f}s · detected {a.bpm:.1f} BPM")
        elif song.audio_path:
            self.lbl_file.setText(f"{os.path.basename(song.audio_path)} (not loaded)")
        else:
            self.lbl_file.setText("No audio loaded — demo timing")
        self.spin_bpm.setValue(song.bpm)
        self.spin_offset.setValue(song.beat_offset)
        self.btn_main.set_rgb(lk.main_rgb)
        self.btn_accent.set_rgb(lk.accent_rgb)
        self.combo_strobe.setCurrentIndex(max(0, self.combo_strobe.findData(lk.strobe_rate)))
        self.chk_lyr_font.setChecked(not lk.lyrics_font)
        self.combo_lyr_font.setEnabled(bool(lk.lyrics_font))
        self.combo_lyr_font.setCurrentFont(QFont(lk.lyrics_font or lk.logo_font))
        for attr, w in self._widgets.items():
            w.set_value(getattr(lk, attr))
        self._updating = False
