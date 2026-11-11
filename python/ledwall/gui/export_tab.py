"""Export tab: watch the whole video (optionally on a simulated stage, in a big window) and render it."""

from __future__ import annotations

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..audio import FFMPEG
from ..model import BEATS_PER_BAR
from .common import hint

VIEWS = [("wall", "LED wall only"), ("led", "On stage: LED wall (light from behind the band)"),
         ("projector", "On stage: projector (light from the front)")]

RESOLUTIONS = [("3840 × 1080  (Ultra-wide LED banner)", (3840, 1080)),
               ("1920 × 1080  (Full HD)", (1920, 1080)),
               ("Custom…", None)]


class ExportTab(QWidget):
    renderRequested = pyqtSignal()
    resolutionChanged = pyqtSignal()
    playFromStartRequested = pyqtSignal()
    playToggleRequested = pyqtSignal()
    scrubbed = pyqtSignal(float)          # seconds
    viewChanged = pyqtSignal()
    bigWindowRequested = pyqtSignal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False

        # ---- playback & stage preview ----------------------------------------------------------
        gp = QGroupBox("Watch it")
        fp = QFormLayout(gp)
        prow = QHBoxLayout()
        self.btn_from_start = QPushButton("▶  Play from the start")
        self.btn_from_start.clicked.connect(self.playFromStartRequested.emit)
        self.btn_play = QPushButton("⏯  Play / pause")
        self.btn_play.clicked.connect(self.playToggleRequested.emit)
        prow.addWidget(self.btn_from_start)
        prow.addWidget(self.btn_play)
        fp.addRow(prow)
        srow = QHBoxLayout()
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 10000)
        self.scrub.valueChanged.connect(self._on_scrub)
        self.lbl_pos = QLabel("0:00.0")
        self.lbl_pos.setMinimumWidth(110)
        srow.addWidget(self.scrub, 1)
        srow.addWidget(self.lbl_pos)
        fp.addRow("Position", srow)
        self.combo_view = QComboBox()
        for key, label in VIEWS:
            self.combo_view.addItem(label, key)
        self.combo_view.currentIndexChanged.connect(lambda *_: self._on_view())
        fp.addRow("View", self.combo_view)
        vrow = QHBoxLayout()
        self.chk_band = QCheckBox("Band on stage")
        self.chk_band.setChecked(True)
        self.chk_audience = QCheckBox("Audience")
        self.chk_audience.setChecked(True)
        for c in (self.chk_band, self.chk_audience):
            c.toggled.connect(lambda *_: self.viewChanged.emit())
            vrow.addWidget(c)
        vrow.addStretch(1)
        fp.addRow(vrow)
        self.btn_window = QPushButton("Open big preview window (F11 = fullscreen)")
        self.btn_window.clicked.connect(self.bigWindowRequested.emit)
        fp.addRow(self.btn_window)
        fp.addRow(hint("The stage view puts the wall behind your band (one silhouette per member, in Band "
                       "order) so you can judge the look before the show. LED wall: the light comes from "
                       "behind and the band is backlit. Projector: the picture comes from the front, lands "
                       "on the band and they cast shadows on the screen."))

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
        self.chk_gpu = QCheckBox("Encode on the graphics card when possible (much faster)")
        self.chk_gpu.setChecked(True)
        self.chk_gpu.setEnabled(FFMPEG is not None)
        self.chk_gpu.toggled.connect(lambda *_: self._probe_encoder())
        self.chk_ffmpeg.toggled.connect(self.chk_gpu.setEnabled)
        f.addRow(self.chk_gpu)
        self.lbl_encoder = QLabel("")
        self.lbl_encoder.setObjectName("hint")
        f.addRow("Encoder", self.lbl_encoder)
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
        for grp in (gp, g):
            col = QVBoxLayout()
            col.addWidget(grp)
            col.addStretch(1)
            lay.addLayout(col, 1)
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

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self.lbl_encoder.text():
            self._probe_encoder()

    def _probe_encoder(self):
        """Find out (in the background, once) which encoder a render will use, and show it."""
        if FFMPEG is None:
            self.lbl_encoder.setText("OpenCV (install ffmpeg for H.264 and audio)")
            return
        from ..export import pick_encoder, render_threads
        gpu = self.chk_gpu.isChecked()
        self.lbl_encoder.setText("checking…")

        class Probe(QThread):
            done = pyqtSignal(str)

            def run(self_):
                self_.done.emit(pick_encoder(gpu)[1])
        self._probe = Probe(self)
        self._probe.done.connect(lambda name: self.lbl_encoder.setText(f"{name}, {render_threads()} render threads"))
        self._probe.start()

    def resolution(self) -> tuple:
        res = RESOLUTIONS[self.combo_res.currentIndex()][1]
        return res if res else (self.spin_w.value() // 2 * 2, self.spin_h.value() // 2 * 2)

    def fps(self) -> int:
        return int(self.combo_fps.currentText())

    # ---- playback ----------------------------------------------------------------------------
    def view(self) -> str:
        return self.combo_view.currentData()

    def _on_view(self):
        on_stage = self.view() != "wall"
        self.chk_band.setEnabled(on_stage)
        self.chk_audience.setEnabled(on_stage)
        self.viewChanged.emit()

    def _on_scrub(self, v):
        if self._updating:
            return
        s, e = self.ctl.engine.export_range()
        self.scrubbed.emit(s + (e - s) * v / 10000.0)

    def set_position(self, t: float):
        s, e = self.ctl.engine.export_range()
        self._updating = True
        self.scrub.setValue(int(round(10000 * min(1.0, max(0.0, (t - s) / max(1e-6, e - s))))))
        self._updating = False
        self.lbl_pos.setText(f"{int(t // 60)}:{t % 60:04.1f} / {int(e // 60)}:{e % 60:04.1f}")

    def update_range(self):
        eng = self.ctl.engine
        s, e = eng.export_range()
        loop = "seamless loop" if self.ctl.song.loop else "no loop blend"
        self.lbl_range.setText(f"{s:.2f}s → {e:.2f}s  ({(e - s) / eng.period / BEATS_PER_BAR:.1f} bars, {loop})")

    def set_busy(self, busy: bool):
        self.progress.setVisible(busy)
        self.btn_render.setText("Cancel Render" if busy else "Render Video")
