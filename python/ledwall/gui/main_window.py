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
from .elements_tab import ElementsTab
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
        self.ctl.currentPatternChanged.connect(lambda *_: self._update_audition())
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
        self.elements_tab = ElementsTab(self.ctl)
        self.band_tab = BandTab(self.ctl)
        self.look_tab = LookTab(self.ctl)
        self.export_tab = ExportTab(self.ctl)
        self.tabs.addTab(self.pattern_tab, "Pattern Editor")
        self.tabs.addTab(self.master_tab, "Master Track")
        self.tabs.addTab(self.elements_tab, "Elements")
        self.tabs.addTab(self.band_tab, "Band (members && groups)")
        self.tabs.addTab(self.look_tab, "Look && Song")
        self.tabs.addTab(self.export_tab, "Export")
        self.tabs.currentChanged.connect(lambda *_: self._update_audition())
        self.elements_tab.previewModeChanged.connect(lambda *_: self._update_audition())
        self.canvas.pressed.connect(self._canvas_press)
        self.canvas.dragged.connect(self._canvas_drag)
        self.canvas.released.connect(lambda: setattr(self, "_el_drag", None))
        self.canvas.wheeled.connect(self._canvas_wheel)
        self._el_drag = None

        self.pattern_tab.loopSectionChanged.connect(lambda *_: self._update_audition())
        self.pattern_tab.grid.seekStep.connect(self._seek_pattern_step)
        self._section_clip_id = None   # master-track clip opened in the pattern editor
        self.section = None            # (t0, t1) looped while the pattern editor is open
        self.free_clock = QElapsedTimer()
        self.free_clock.start()
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

    def _pattern_section(self):
        """The master-track clip of the current pattern to loop: the one opened from the master track,
        else the one under the playhead, else its first one. None if the pattern is not placed."""
        pat = self.ctl.current_pattern
        if not pat:
            return None
        clips = sorted((c for c in self.ctl.song.clips if c.pattern_id == pat.id), key=lambda c: c.start)
        if not clips:
            return None
        eng = self.ctl.engine
        b = eng.t2b(self.current_t)
        clip = (next((c for c in clips if c.id == self._section_clip_id), None)
                or next((c for c in clips if c.start <= b < c.start + c.length), None) or clips[0])
        self._section_clip_id = clip.id
        return clip

    def _seek_pattern_step(self, step: float):
        """Ruler click in the pattern editor: jump to that step of the pattern, in the repetition of
        the pattern the playhead is in now (inside the looped section / clip)."""
        pat = self.ctl.current_pattern
        if not pat:
            return
        eng = self.ctl.engine
        b = eng.t2b(self.current_t)
        pb = step / STEPS_PER_BEAT
        if eng.audition_pattern == pat.id:
            target = math.floor(b / pat.beats) * pat.beats + pb
        else:
            clip = self._pattern_section() if self.section is not None else None
            if clip is None:
                clip = next((c for c in self.ctl.song.clips if c.pattern_id == pat.id and c.start <= b < c.start + c.length),
                            None) or next((c for c in sorted(self.ctl.song.clips, key=lambda c: c.start)
                                           if c.pattern_id == pat.id), None)
            if clip is None:
                return
            rep = math.floor((b - clip.start) / pat.beats) if clip.start <= b < clip.start + clip.length else 0
            target = clip.start + rep * pat.beats + pb
            if target >= clip.start + clip.length:       # last repetition is cut short by the clip end
                target = clip.start + pb
        self.seek(eng.b2t(target))

    def _update_audition(self):
        eng = self.ctl.engine
        cur = self.tabs.currentWidget()
        in_elements = cur is self.elements_tab
        eng.preview_pulse = in_elements and self.elements_tab.chk_pulse.isChecked()
        self.canvas.interactive = in_elements
        if not in_elements:
            self.canvas.selection = None
        pat = self.ctl.current_pattern
        self.section, eng.audition_pattern = None, None
        if cur is self.pattern_tab and self.pattern_tab.chk_loop.isChecked() and pat:
            clip = self._pattern_section()
            if clip:
                self.section = (eng.b2t(clip.start), eng.b2t(clip.start + clip.length))
            else:
                eng.audition_pattern = pat.id
        if eng.preview_pulse:
            self.lbl_mode.setText("Elements: pulse preview")
        elif self.section:
            bar0 = int(eng.t2b(self.section[0]) // 4) + 1
            bar1 = int(round(eng.t2b(self.section[1]) / 4))
            self.lbl_mode.setText(f"Looping {pat.name}: bars {bar0}–{bar1}")
        elif eng.audition_pattern:
            self.lbl_mode.setText(f"Looping pattern {pat.name} (not on the master track)")
        else:
            self.lbl_mode.setText("Playing: Master Track")
        self._dirty = True

    def _edit_pattern(self, pid, step=0.0, t=None):
        self._section_clip_id = None
        if t is not None:
            b = self.ctl.engine.t2b(t)
            c = next((c for c in self.ctl.song.clips if c.pattern_id == pid and c.start <= b < c.start + c.length), None)
            self._section_clip_id = c.id if c else None
        self.ctl.set_current_pattern(pid)
        self.tabs.setCurrentWidget(self.pattern_tab)
        if t is not None:
            self.seek(t)
        self.pattern_tab.grid.focus_step(step)
        self.pattern_tab.grid.setFocus()

    # ---- element editing on the preview -------------------------------------------------------
    def _canvas_press(self, x, y):
        if not self.renderer:
            return
        hit = None
        for el in self.ctl.song.elements:        # list top = front, so first hit wins
            bb = self.renderer.bboxes.get(el.id)
            if el.visible and bb and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]:
                hit = el
                break
        if hit is None:
            return
        self.elements_tab.select(hit.id)
        self._el_drag = (hit.id, x, y, hit.x, hit.y)
        self._dirty = True

    def _canvas_drag(self, x, y):
        if not self._el_drag or not self.renderer:
            return
        eid, x0, y0, ex, ey = self._el_drag
        el = self.ctl.song.element(eid)
        if el:
            el.x = min(1.0, max(0.0, ex + (x - x0) / self.renderer.w))
            el.y = min(1.0, max(0.0, ey + (y - y0) / self.renderer.h))
            self.ctl.commit("elements", f"{eid}:move")

    def _canvas_wheel(self, d):
        el = self.elements_tab.element
        if el:
            el.size = min(1.0, max(0.02, el.size * (1.06 if d > 0 else 1 / 1.06)))
            self.ctl.commit("elements", f"{el.id}:size")

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
        if self.section and not (self.section[0] <= self.current_t < self.section[1]):
            self.current_t = self.section[0]
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
            if self.section and t >= self.section[1]:
                self.seek(self.section[0])
                t = self.section[0]
            elif self.ctl.song.loop and r and t >= r[1] and not eng.audition_pattern:
                self.seek(r[0])
                t = r[0]
            elif t >= eng.duration:
                self.pause()
                t = eng.duration
            self.current_t = t
            self._dirty = True
        elif eng.preview_pulse:
            self._dirty = True          # the pulse preview keeps running while paused
        if self._dirty:
            self._render_preview()
            self._dirty = False

    def _render_preview(self):
        tw, th = self.export_tab.resolution()
        pw, ph = self.canvas.preview_size(tw / th)
        if self.renderer is None or (self.renderer.w, self.renderer.h) != (pw, ph):
            self.renderer = FrameRenderer(pw, ph)
        eng, t = self.ctl.engine, self.current_t
        t_frame = self.free_clock.elapsed() / 1000.0 if (eng.preview_pulse and not self.playing) else t
        self.canvas.show_frame(self.renderer.render(eng.state_at(t_frame), self.ctl.song))
        if self.canvas.interactive:
            self.canvas.selection = self.renderer.bboxes.get(self.elements_tab.selected_id or "")
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
