"""Export settings + Render button."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..audio import FFMPEG
from ..model import BEATS_PER_BAR
from .common import hint

RESOLUTIONS = [("3840 × 1080  (Ultra-wide LED banner)", (3840, 1080)),
               ("1920 × 1080  (Full HD)", (1920, 1080)),
               ("Custom…", None)]


class ExportTab(QWidget):
    renderRequested = pyqtSignal()
    resolutionChanged = pyqtSignal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        g = QGroupBox("Render")
        f = QFormLayout(g)
        self.combo_res = QComboBox()
        for label, _ in RESOLUTIONS:
            self.combo_res.addItem(label)
        self.combo_res.currentIndexChanged.connect(self._on_res)
        f.addRow("Resolution", self.combo_res)
        row = QHBoxLayout()
        self.spin_w, self.spin_h = QSpinBox(), QSpinBox()
        for sp, v in ((self.spin_w, 3840), (self.spin_h, 1080)):
            sp.setRange(64, 16384)
            sp.setSingleStep(2)
            sp.setValue(v)
            sp.setEnabled(False)
            sp.valueChanged.connect(lambda *_: self.resolutionChanged.emit())
            row.addWidget(sp)
        f.addRow("Custom", row)
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["30", "60"])
        self.combo_fps.setCurrentIndex(1)
        f.addRow("FPS", self.combo_fps)
        self.lbl_range = QLabel()
        f.addRow("Range", self.lbl_range)
        f.addRow(hint("Range = the loop region from the Master Track (or the whole song). "
                      "Turn on 'Seamless loop' there for a clip that loops without a jump."))
        self.chk_ffmpeg = QCheckBox("H.264 high-bitrate encode (ffmpeg)")
        self.chk_ffmpeg.setChecked(FFMPEG is not None)
        self.chk_ffmpeg.setEnabled(FFMPEG is not None)
        f.addRow(self.chk_ffmpeg)
        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(4, 200)
        self.spin_bitrate.setValue(40)
        self.spin_bitrate.setSuffix(" Mbps")
        f.addRow("Bitrate", self.spin_bitrate)
        self.chk_audio = QCheckBox("Mux audio into the video")
        self.chk_audio.setEnabled(FFMPEG is not None)
        f.addRow(self.chk_audio)
        self.btn_render = QPushButton("Render Video")
        self.btn_render.setObjectName("primary")
        self.btn_render.setMinimumHeight(36)
        self.btn_render.clicked.connect(self.renderRequested.emit)
        f.addRow(self.btn_render)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        f.addRow(self.progress)
        lay = QHBoxLayout(self)
        col = QVBoxLayout()
        col.addWidget(g)
        col.addStretch(1)
        lay.addLayout(col)
        lay.addStretch(1)
        ctl.changed.connect(lambda *_: self.update_range())
        self.update_range()

    def _on_res(self, i):
        res = RESOLUTIONS[i][1]
        self.spin_w.setEnabled(res is None)
        self.spin_h.setEnabled(res is None)
        if res:
            self.spin_w.setValue(res[0])
            self.spin_h.setValue(res[1])
        self.resolutionChanged.emit()

    def resolution(self) -> tuple:
        res = RESOLUTIONS[self.combo_res.currentIndex()][1]
        return res if res else (self.spin_w.value() // 2 * 2, self.spin_h.value() // 2 * 2)

    def fps(self) -> int:
        return int(self.combo_fps.currentText())

    def update_range(self):
        eng = self.ctl.engine
        s, e = eng.export_range()
        loop = "seamless loop" if self.ctl.song.loop else "no loop blend"
        self.lbl_range.setText(f"{s:.2f}s → {e:.2f}s  ({(e - s) / eng.period / BEATS_PER_BAR:.1f} bars, {loop})")

    def set_busy(self, busy: bool):
        self.progress.setVisible(busy)
        self.btn_render.setText("Cancel Render" if busy else "Render Video")
