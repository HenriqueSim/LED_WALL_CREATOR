"""Main window: live preview + transport on top, editing tabs below."""

from __future__ import annotations

import json
import math
import os

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..audio import AudioAnalysisWorker
from ..controller import SongController
from ..engine import bar_of
from ..export import ExportWorker
from ..model import STEPS_PER_BEAT, Song, default_song
from ..renderer import FrameRenderer
from .arrangement import MasterTab
from .band_tab import BandTab
from .common import PreviewCanvas
from .export_tab import ExportTab
from .look_tab import LookTab
from .pattern_editor import PatternTab

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_MULTIMEDIA = True
except Exception:  # pragma: no cover
    HAVE_MULTIMEDIA = False

APP_NAME = "LED Wall Motion Generator"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1600, 980)
        self.ctl = SongController()
        self.renderer: FrameRenderer | None = None
        self.audio_worker = None
        self.export_worker = None
        self.playing = False
        self.clock_anchor = 0.0
        self.elapsed = QElapsedTimer()
        self.elapsed.start()
        self.current_t = 0.0
        self._dirty = True

        self.player = None
        if HAVE_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio_out = QAudioOutput(self)
            self.audio_out.setVolume(0.9)
            self.player.setAudioOutput(self.audio_out)

        self._build_ui()
        self._build_menu()
        self.ctl.changed.connect(self._on_changed)
        self._update_title()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ---- UI ----------------------------------------------------------------------------
    def _build_ui(self):
        self.canvas = PreviewCanvas()
        transport = QHBoxLayout()
        self.btn_play = QPushButton("▶  Play")
        self.btn_play.setFixedWidth(100)
        self.btn_play.clicked.connect(self.toggle_play)
        btn_home = QPushButton("⏮")
        btn_home.setFixedWidth(40)
        btn_home.clicked.connect(self.go_home)
        for b in (self.btn_play, btn_home):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lbl_time = QLabel()
        self.lbl_time.setFont(QFont("Monospace", 11))
        self.lbl_time.setMinimumWidth(240)
        self.lbl_mode = QLabel()
        self.lbl_mode.setStyleSheet("color:#ffb060")
        transport.addWidget(self.btn_play)
        transport.addWidget(btn_home)
        transport.addWidget(self.lbl_time)
        transport.addStretch(1)
        transport.addWidget(self.lbl_mode)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(4, 4, 4, 0)
        tl.addWidget(self.canvas, 1)
        tl.addLayout(transport)

        self.tabs = QTabWidget()
        self.pattern_tab = PatternTab(self.ctl)
        self.master_tab = MasterTab(self.ctl, lambda: self.current_t, self.seek)
        self.band_tab = BandTab(self.ctl)
        self.look_tab = LookTab(self.ctl)
        self.export_tab = ExportTab(self.ctl)
        self.tabs.addTab(self.pattern_tab, "Pattern Editor")
        self.tabs.addTab(self.master_tab, "Master Track")
        self.tabs.addTab(self.band_tab, "Band (members && groups)")
        self.tabs.addTab(self.look_tab, "Look && Song")
        self.tabs.addTab(self.export_tab, "Export")
        self.tabs.currentChanged.connect(lambda *_: self._update_audition())

        self.pattern_tab.auditionChanged.connect(lambda *_: self._update_audition())
        self.master_tab.editPatternRequested.connect(self._edit_pattern)
        self.look_tab.loadAudioRequested.connect(self.load_audio_dialog)
        self.export_tab.renderRequested.connect(self.render_video)
        self.export_tab.resolutionChanged.connect(self._invalidate_renderer)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 600])
        self.setCentralWidget(split)
        self.statusBar().showMessage("Space = play/pause · draw notes in the Pattern Editor · arrange them on the Master Track")

        sc = QShortcut(QKeySequence("Space"), self)
        sc.activated.connect(self.toggle_play)
        QShortcut(QKeySequence("Home"), self).activated.connect(self.go_home)

    def _build_menu(self):
        mb = self.menuBar()
        m_file = mb.addMenu("&File")

        def act(menu, text, fn, key=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a
        act(m_file, "New Song", self.new_song, "Ctrl+N")
        act(m_file, "Open Project…", self.open_project, "Ctrl+O")
        act(m_file, "Save Project", self.save_project, "Ctrl+S")
        act(m_file, "Save Project As…", lambda: self.save_project(ask=True), "Ctrl+Shift+S")
        m_file.addSeparator()
        act(m_file, "Load Audio…", self.load_audio_dialog)
        act(m_file, "Render Video…", self.render_video, "Ctrl+R")
        m_file.addSeparator()
        act(m_file, "Quit", self.close, "Ctrl+Q")
        m_edit = mb.addMenu("&Edit")
        self.act_undo = act(m_edit, "Undo", self.ctl.undo, "Ctrl+Z")
        self.act_redo = act(m_edit, "Redo", self.ctl.redo, "Ctrl+Shift+Z")
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.ctl.redo)

    # ---- change handling ------------------------------------------------------------------
    def _on_changed(self, kind):
        self._dirty = True
        self.act_undo.setEnabled(self.ctl.can_undo())
        self.act_redo.setEnabled(self.ctl.can_redo())
        self._update_title()
        self._update_audition()

    def _update_title(self):
        name = os.path.basename(self.ctl.project_path) if self.ctl.project_path else "untitled"
        self.setWindowTitle(f"{APP_NAME} — {name}{' *' if self.ctl.dirty else ''}")

    def _update_audition(self):
        on = self.pattern_tab.chk_audition.isChecked() and self.tabs.currentWidget() is self.pattern_tab
        pid = self.ctl.current_pattern_id if on else None
        self.ctl.engine.audition_pattern = pid
        pat = self.ctl.current_pattern
        self.lbl_mode.setText(f"Auditioning pattern: {pat.name}" if pid and pat else "Playing: Master Track")
        self._dirty = True

    def _edit_pattern(self, pid, step=0.0, t=None):
        self.ctl.set_current_pattern(pid)
        self.tabs.setCurrentWidget(self.pattern_tab)
        if t is not None:
            self.seek(t)
        self.pattern_tab.grid.focus_step(step)
        self.pattern_tab.grid.setFocus()

    def _invalidate_renderer(self):
        self.renderer = None
        self._dirty = True

    # ---- audio ---------------------------------------------------------------------------------
    def load_audio_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load audio", "",
                                              "Audio/Video (*.wav *.mp3 *.mp4 *.m4a *.flac *.ogg *.aac);;All files (*)")
        if path:
            self.load_audio(path, set_tempo=True)

    def load_audio(self, path: str, set_tempo: bool):
        if self.audio_worker and self.audio_worker.isRunning():
            return
        self.pause()
        self._set_tempo_on_load = set_tempo
        self.look_tab.set_loading(0, os.path.basename(path))
        self.audio_worker = AudioAnalysisWorker(path, self)
        self.audio_worker.progress.connect(lambda v, m: (self.look_tab.set_loading(v, m), self.statusBar().showMessage(m)))
        self.audio_worker.finished_ok.connect(self._on_audio_loaded)
        self.audio_worker.failed.connect(self._on_audio_failed)
        self.audio_worker.start()

    def _on_audio_loaded(self, a):
        self.look_tab.set_loading(None)
        self.ctl.song.audio_path = a.path
        if self._set_tempo_on_load:
            self.ctl.song.bpm = a.bpm
            self.ctl.song.beat_offset = a.detected_offset
        self.ctl.set_analysis(a)
        if self._set_tempo_on_load:
            self.ctl.commit("look", "audio")
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(a.path))
        self.seek(0.0)
        self.statusBar().showMessage(f"Loaded {os.path.basename(a.path)} — {a.bpm:.1f} BPM detected", 6000)

    def _on_audio_failed(self, msg):
        self.look_tab.set_loading(None)
        QMessageBox.critical(self, "Audio load failed", msg)

    # ---- transport -------------------------------------------------------------------------------
    def toggle_play(self):
        self.pause() if self.playing else self.play()

    def play(self):
        if self.current_t >= self.ctl.engine.duration - 0.05:
            self.current_t = 0.0
        self.playing = True
        self.clock_anchor = self.current_t
        self.elapsed.restart()
        if self.player and self.ctl.analysis:
            self.player.setPosition(int(self.current_t * 1000))
            self.player.play()
        self.btn_play.setText("⏸  Pause")

    def pause(self):
        if self.playing:
            self.current_t = self._clock()
        self.playing = False
        if self.player:
            self.player.pause()
        self.btn_play.setText("▶  Play")

    def seek(self, t: float):
        self.current_t = max(0.0, min(t, self.ctl.engine.duration))
        self.clock_anchor = self.current_t
        self.elapsed.restart()
        if self.player and self.ctl.analysis:
            self.player.setPosition(int(self.current_t * 1000))
        self._dirty = True

    def go_home(self):
        r = self.ctl.song.region
        self.seek(r[0] if r else 0.0)

    def _clock(self) -> float:
        return self.clock_anchor + self.elapsed.elapsed() / 1000.0

    def _tick(self):
        eng = self.ctl.engine
        if self.playing:
            t = self._clock()
            if self.player and self.ctl.analysis and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                pt = self.player.position() / 1000.0
                if abs(pt - t) > 0.08:
                    self.clock_anchor, t = pt, pt
                    self.elapsed.restart()
            r = self.ctl.song.region
            if self.ctl.song.loop and r and t >= r[1] and not eng.audition_pattern:
                self.seek(r[0])
                t = r[0]
            elif t >= eng.duration:
                self.pause()
                t = eng.duration
            self.current_t = t
            self._dirty = True
        if self._dirty:
            self._render_preview()
            self._dirty = False

    def _render_preview(self):
        tw, th = self.export_tab.resolution()
        pw, ph = self.canvas.preview_size(tw / th)
        if self.renderer is None or (self.renderer.w, self.renderer.h) != (pw, ph):
            self.renderer = FrameRenderer(pw, ph)
        eng, t = self.ctl.engine, self.current_t
        self.canvas.show_frame(self.renderer.render(eng.state_at(t), self.ctl.song))
        b = eng.t2b(t)
        bar, beat = bar_of(b)
        self.lbl_time.setText(f"{int(t // 60)}:{t % 60:05.2f}   bar {bar}.{beat}")
        self.master_tab.view.set_playhead(t)
        # pattern playhead: auditioned pattern, or the clip of this pattern under the playhead
        pat = self.ctl.current_pattern
        step = None
        if pat:
            if eng.audition_pattern == pat.id:
                step = (b * STEPS_PER_BEAT) % pat.steps
            else:
                for c in self.ctl.song.clips:
                    if c.pattern_id == pat.id and c.start <= b < c.start + c.length:
                        step = ((b - c.start) * STEPS_PER_BEAT) % pat.steps
                        break
        if step != self.pattern_tab.grid.playhead_step:
            self.pattern_tab.grid.playhead_step = step
            self.pattern_tab.grid.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._dirty = True

    # ---- project ------------------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.ctl.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current song first?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save_project()
        return r == QMessageBox.StandardButton.Discard

    def new_song(self):
        if not self._confirm_discard():
            return
        self.pause()
        self.ctl.project_path = None
        self.ctl.set_analysis(None)
        if self.player:
            self.player.setSource(QUrl())
        self.ctl.set_song(default_song())
        self.seek(0.0)
        self._update_title()

    def save_project(self, ask: bool = False) -> bool:
        path = self.ctl.project_path
        if ask or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", path or "song.ledwall.json", "LED wall project (*.json)")
            if not path:
                return False
        with open(path, "w") as fh:
            json.dump(self.ctl.song.to_dict(), fh, indent=1)
        self.ctl.project_path = path
        self.ctl.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def open_project(self, path: str | None = None):
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "LED wall project (*.json)")
            if not path:
                return
        try:
            with open(path) as fh:
                song = Song.from_dict(json.load(fh))
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.pause()
        self.ctl.set_analysis(None)
        self.ctl.set_song(song)
        self.ctl.project_path = path
        self._update_title()
        if song.audio_path and os.path.exists(song.audio_path):
            self.load_audio(song.audio_path, set_tempo=False)
        elif song.audio_path:
            QMessageBox.warning(self, "Audio missing", f"Audio file not found:\n{song.audio_path}")
        self.seek(0.0)

    # ---- export --------------------------------------------------------------------------------------
    def render_video(self):
        if self.export_worker and self.export_worker.isRunning():
            self.export_worker.cancel()
            return
        path, _ = QFileDialog.getSaveFileName(self, "Render video", "ledwall_loop.mp4", "MP4 video (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        self.pause()
        w, h = self.export_tab.resolution()
        et = self.export_tab
        self.export_worker = ExportWorker(self.ctl.song, self.ctl.analysis, w, h, et.fps(), path,
                                          et.chk_ffmpeg.isChecked(), et.chk_audio.isChecked(), et.spin_bitrate.value(), self)
        self.export_worker.progress.connect(lambda v, m: (et.progress.setValue(v), self.statusBar().showMessage(m)))
        self.export_worker.finished_ok.connect(self._on_export_done)
        self.export_worker.failed.connect(self._on_export_failed)
        et.progress.setValue(0)
        et.set_busy(True)
        self.tabs.setCurrentWidget(et)
        self.export_worker.start()

    def _on_export_done(self, path):
        self.export_tab.set_busy(False)
        QMessageBox.information(self, "Render complete", f"Video written to:\n{path}")

    def _on_export_failed(self, msg):
        self.export_tab.set_busy(False)
        self.statusBar().showMessage(msg, 8000)
        if msg != "Export cancelled":
            QMessageBox.critical(self, "Render failed", msg)

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        for wk in (self.export_worker, self.audio_worker):
            if wk and wk.isRunning():
                if isinstance(wk, ExportWorker):
                    wk.cancel()
                wk.wait(3000)
        super().closeEvent(ev)
