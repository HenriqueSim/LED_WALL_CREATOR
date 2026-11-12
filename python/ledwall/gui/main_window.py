"""Main window: live preview + transport on top, editing tabs below."""

from __future__ import annotations

import json
import math
import os

from PyQt6.QtCore import QElapsedTimer, QSettings, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .. import project as proj
from .. import updater
from ..audio import AudioAnalysisWorker
from ..controller import SongController
from ..engine import bar_of
from ..export import ExportWorker
from ..model import STEPS_PER_BEAT, Song, default_song
from ..renderer import FrameRenderer
from .arrangement import MasterTab
from .band_tab import BandTab
from .common import PreviewCanvas, icon
from .elements_tab import ElementsTab
from .export_tab import ExportTab
from .stage_window import StageWindow
from ..stage import StageSimulator
from .look_tab import LookTab
from .pattern_editor import PatternTab
from .project_dialog import ProjectDialog
from .update_dialog import UpdateChecker, UpdateDialog, should_auto_check, skipped
from ..version import __version__

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_MULTIMEDIA = True
except Exception:  # pragma: no cover
    HAVE_MULTIMEDIA = False

APP_NAME = "LED Wall Creator"


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
        self.ctl.currentPatternChanged.connect(lambda *_: self.elements_tab.reload())
        self._update_title()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.update_checker = None
        if updater.current_install() and should_auto_check():
            QTimer.singleShot(2500, lambda: self.check_updates(manual=False))

    # ---- UI ----------------------------------------------------------------------------
    def _build_ui(self):
        self.canvas = PreviewCanvas()
        transport = QHBoxLayout()
        transport.setContentsMargins(2, 8, 2, 4)
        transport.setSpacing(8)
        logo = QLabel()
        from ..resources import ICONS_DIR
        from PyQt6.QtGui import QPixmap
        pm = QPixmap(os.path.join(ICONS_DIR, "app.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            transport.addWidget(logo)
        self.btn_play = QPushButton("Play")
        self.btn_play.setIcon(icon("play"))
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setObjectName("play")
        self.btn_play.setFixedWidth(116)
        self.btn_play.clicked.connect(self.toggle_play)
        btn_home = QPushButton()
        btn_home.setIcon(icon("skip-back"))
        btn_home.setIconSize(QSize(16, 16))
        btn_home.setObjectName("ghost")
        btn_home.setToolTip("Back to the start (Home)")
        btn_home.clicked.connect(self.go_home)
        for b in (self.btn_play, btn_home):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lbl_time = QLabel()
        self.lbl_time.setObjectName("timecode")
        self.lbl_time.setMinimumWidth(260)
        self.lbl_mode = QLabel()
        self.lbl_mode.setObjectName("modeTag")
        transport.addWidget(self.btn_play)
        transport.addWidget(btn_home)
        transport.addWidget(self.lbl_time)
        transport.addStretch(1)
        transport.addWidget(self.lbl_mode)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(10, 8, 10, 0)
        tl.addWidget(self.canvas, 1)
        tl.addLayout(transport)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
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
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.band_tab.highlightChanged.connect(self._update_audition)
        self.band_tab.elementChosen.connect(self.elements_tab.select)
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
        self.export_tab.playFromStartRequested.connect(self.play_from_start)
        self.export_tab.playToggleRequested.connect(self.toggle_play)
        self.export_tab.scrubbed.connect(self.seek)
        self.export_tab.viewChanged.connect(lambda: setattr(self, "_dirty", True))
        self.export_tab.bigWindowRequested.connect(self.open_big_window)
        self.big_window = None
        self._stage_cache = {}      # target -> (StageSimulator, FrameRenderer)
        self.export_tab.resolutionChanged.connect(self._invalidate_renderer)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([360, 600])
        self.setCentralWidget(split)
        self.split = split
        self._split_before_export = None
        self.tabs.currentChanged.connect(self._on_tab_for_split)
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
        act(m_file, "New Project…", self.new_song, "Ctrl+N")
        act(m_file, "Open Project…", lambda: self.open_project(), "Ctrl+O")
        self.m_recent = m_file.addMenu("Open Recent")
        self.m_recent.aboutToShow.connect(self._fill_recent)
        act(m_file, "Save Project", lambda: self.save_project(), "Ctrl+S")
        act(m_file, "Save Project As…", lambda: self.save_project(ask=True), "Ctrl+Shift+S")
        act(m_file, "Show Project Folder", self.show_project_folder)
        m_file.addSeparator()
        act(m_file, "Load Audio…", self.load_audio_dialog)
        act(m_file, "Render Video…", self.render_video, "Ctrl+R")
        m_file.addSeparator()
        act(m_file, "Quit", self.close, "Ctrl+Q")
        m_edit = mb.addMenu("&Edit")
        self.act_undo = act(m_edit, "Undo", self.ctl.undo, "Ctrl+Z")
        self.act_redo = act(m_edit, "Redo", self.ctl.redo, "Ctrl+Shift+Z")
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.ctl.redo)
        m_help = mb.addMenu("&Help")
        act(m_help, "Check for Updates…", lambda: self.check_updates(manual=True))
        act(m_help, "About", self.show_about)

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

    def _on_tab_changed(self, *_):
        if self.tabs.currentWidget() is self.band_tab:      # show the element picked in the Elements tab
            self.band_tab.select_element(self.elements_tab.selected_id)
        self._update_audition()

    def _update_audition(self):
        eng = self.ctl.engine
        cur = self.tabs.currentWidget()
        eng.preview_highlight = self.band_tab.highlight() if cur is self.band_tab else None
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
        if eng.preview_highlight is not None:
            self.lbl_mode.setText("Band: the selected member / group lit")
        elif eng.preview_pulse:
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
    def _logo_hit(self, x, y) -> bool:
        """True when (x, y) is on the band name's letters (or their halo)."""
        r = self.renderer
        bb = getattr(r, "logo_bbox", None)
        if not bb or not r.logo_drawn or not (bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]) or not r._logo:
            return False
        x0, y0, x1, y1, _fill, _tight, _wide, halo = r._logo
        ix, iy = int(x) - x0, int(y) - y0
        return 0 <= iy < halo.shape[0] and 0 <= ix < halo.shape[1] and float(halo[iy, ix, 0]) > 0.05

    def _canvas_press(self, x, y):
        if not self.renderer:
            return
        song, et = self.ctl.song, self.elements_tab
        sel = et.element
        if sel is not None and sel.type == "custom":   # pick a shape of the selected custom element
            for i in reversed(range(len(sel.shapes))):
                bb = self.renderer.shape_bboxes.get((sel.id, i))
                if bb and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]:
                    et.select_shape(i)
                    sh = sel.shapes[i]
                    self._el_drag = ("shape", x, y, sh.get("x", 0.0), sh.get("y", 0.0), sel.id, i)
                    self._dirty = True
                    return
        if self._logo_hit(x, y):                  # the band name is drawn on top of the elements
            et.select_logo()
            lk = song.look
            self._el_drag = ("logo", x, y, lk.logo_x, lk.logo_y)
            self._dirty = True
            return
        hit = None
        for el in song.elements:        # list top = front, so first hit wins
            bb = self.renderer.bboxes.get(el.id)
            if bb and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]:     # only drawn elements have a box
                hit = el
                break
        if hit is None:
            return
        et.select(hit.id)
        self._el_drag = ("element", x, y, hit.x, hit.y, hit.id)
        self._dirty = True

    def _canvas_drag(self, x, y):
        if not self._el_drag or not self.renderer:
            return
        kind, x0, y0, ex, ey = self._el_drag[:5]
        r, song = self.renderer, self.ctl.song
        dx, dy = (x - x0) / r.w, (y - y0) / r.h
        if kind == "logo":
            song.look.logo_x = min(0.95, max(0.05, ex + dx))
            song.look.logo_y = min(0.95, max(0.1, ey + dy))
            self.ctl.commit("look", "logo:move")
        elif kind == "shape":
            eid, i = self._el_drag[5:]
            el = song.element(eid)
            if el and i < len(el.shapes):
                # pixel delta -> the element's own (rotated, scaled) units
                a = math.radians(el.rotation)
                S = max(1e-6, el.size * r.h)
                px, py = x - x0, y - y0
                el.shapes[i]["x"] = round(ex + (px * math.cos(a) + py * math.sin(a)) / S, 3)
                el.shapes[i]["y"] = round(ey + (-px * math.sin(a) + py * math.cos(a)) / S, 3)
                self.ctl.commit("elements", f"{eid}:shape{i}:move")
        else:
            el = song.element(self._el_drag[5])
            if el:
                el.x = min(1.0, max(0.0, ex + dx))
                el.y = min(1.2, max(0.0, ey + dy))
                self.ctl.commit("elements", f"{el.id}:move")

    def _canvas_wheel(self, d):
        f = 1.06 if d > 0 else 1 / 1.06
        et = self.elements_tab
        if et.logo_selected:
            lk = self.ctl.song.look
            lk.logo_size = min(0.9, max(0.08, lk.logo_size * f))
            self.ctl.commit("look", "logo_size")
            return
        el, sh = et.element, et.shape
        if sh is not None:
            sh["size"] = min(6.0, max(0.01, sh.get("size", 0.5) * f))
            self.ctl.commit("elements", f"{el.id}:shape{et.selected_shape}:size")
        elif el:
            el.size = min(1.5, max(0.02, el.size * f))
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
        path = a.path
        if (self._set_tempo_on_load and proj.media_dir(self.ctl.project_path)
                and QSettings().value("project/copy_media", True, type=bool)):
            try:                    # a project with a Media folder keeps its own copy of the audio
                path = proj.copy_into_media(path, self.ctl.project_path)
                a.path = path
            except OSError as exc:
                self.statusBar().showMessage(f"Couldn't copy the audio into Media: {exc}", 8000)
        self.ctl.song.audio_path = path
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
        self.btn_play.setText("Pause")
        self.btn_play.setIcon(icon("pause"))

    def pause(self):
        if self.playing:
            self.current_t = self._clock()
        self.playing = False
        if self.player:
            self.player.pause()
        self.btn_play.setText("Play")
        self.btn_play.setIcon(icon("play"))

    def seek(self, t: float):
        self.current_t = max(0.0, min(t, self.ctl.engine.duration))
        self.clock_anchor = self.current_t
        self.elapsed.restart()
        if self.player and self.ctl.analysis:
            self.player.setPosition(int(self.current_t * 1000))
        self._dirty = True

    def _on_tab_for_split(self, _i):
        """The Export tab is about watching: give the preview most of the window while it is open."""
        in_export = self.tabs.currentWidget() is self.export_tab
        if in_export and self._split_before_export is None:
            self._split_before_export = self.split.sizes()
            total = sum(self._split_before_export)
            self.split.setSizes([int(total * 0.62), total - int(total * 0.62)])
        elif not in_export and self._split_before_export is not None:
            self.split.setSizes(self._split_before_export)
            self._split_before_export = None
        self._dirty = True

    def play_from_start(self):
        self.pause()
        self.seek(self.ctl.engine.export_range()[0])
        self.play()

    def open_big_window(self):
        if self.big_window is None:
            self.big_window = StageWindow()
            self.big_window.togglePlay.connect(self.toggle_play)
            self.big_window.closed.connect(lambda: setattr(self, "_dirty", True))
        self.big_window.show()
        self.big_window.raise_()
        self.big_window.activateWindow()
        self._dirty = True

    def _render_view(self, target: str, canvas, st, t: float, max_w: int):
        """Render the Export view (wall only / on stage) for one canvas."""
        song = self.ctl.song
        tw, th = self.export_tab.resolution()
        view = self.export_tab.view()
        if view == "wall":
            pw, ph = canvas.preview_size(tw / th, max_w)
            sim = None
        else:
            pw, ph = canvas.preview_size(16 / 9, max_w)
        cached = self._stage_cache.get(target)
        if not cached or cached[2] != (pw, ph, tw, th, view):
            sim = StageSimulator(pw, ph) if view != "wall" else None
            ww, wh = sim.wall_size(th / tw) if sim else (pw, ph)
            cached = (sim, FrameRenderer(ww, wh), (pw, ph, tw, th, view))
            self._stage_cache[target] = cached
        sim, rend, _ = cached
        frame = rend.render(st, song)
        if sim is not None:
            frame = sim.compose(frame, len(song.members), view, t,
                                self.export_tab.chk_band.isChecked(), self.export_tab.chk_audience.isChecked(),
                                house=self.export_tab.house(), beat=st.beat, env=st.env)
        canvas.show_frame(frame)

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
        if eng.preview_pulse:   # Elements tab: only what is being edited
            et = self.elements_tab
            eng.preview_logo = et.logo_selected
            if et.logo_selected:      # a dark cut-out name needs the pattern's elements behind it to be seen
                pat = self.ctl.current_pattern
                cut = self.ctl.song.look.logo_color == "cutout" and pat
                eng.preview_elements = {e.id for e in self.ctl.song.pattern_elements(pat)} if cut else set()
            else:
                eng.preview_elements = {et.element.id} if et.element is not None else set()
        st = eng.state_at(t_frame)
        in_export = self.tabs.currentWidget() is self.export_tab
        if in_export:
            self._render_view("main", self.canvas, st, t_frame, 1400)
        else:
            self.canvas.show_frame(self.renderer.render(st, self.ctl.song))
        if self.big_window is not None and self.big_window.isVisible():
            self._render_view("big", self.big_window.canvas, st, t_frame, 1920)
        self.export_tab.set_position(t)
        if self.canvas.interactive:
            et = self.elements_tab
            if et.logo_selected:
                self.canvas.selection = self.renderer.logo_bbox
            elif et.shape is not None:
                self.canvas.selection = self.renderer.shape_bboxes.get((et.selected_id, et.selected_shape))
            else:
                self.canvas.selection = self.renderer.bboxes.get(et.selected_id or "")
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
        grid = self.pattern_tab.grid
        # waveform behind the pattern: the audio of the repetition of its clip being played (or the
        # first one); none for a pattern that is not on the master track
        wave = None
        if pat and self.ctl.analysis is not None and grid.isVisible():
            clip = None if eng.audition_pattern else (
                next((c for c in self.ctl.song.clips if c.pattern_id == pat.id and c.start <= b < c.start + c.length),
                     None) or self._pattern_section())
            if clip is not None:
                rep = math.floor((b - clip.start) / pat.beats) if clip.start <= b < clip.start + clip.length else 0
                wave = clip.start + rep * pat.beats
        if step != grid.playhead_step or wave != grid.wave_start_beat:
            grid.playhead_step, grid.wave_start_beat = step, wave
            grid.update()

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
        dlg = ProjectDialog(self, "New project", "My song", ok_text="Create")
        if not dlg.exec():
            return
        self.pause()
        self.ctl.project_path = None
        self.ctl.set_analysis(None)
        if self.player:
            self.player.setSource(QUrl())
        self.ctl.set_song(default_song())
        self.seek(0.0)
        self._write_project(dlg.path)

    def _project_dialog_defaults(self):
        """(name, location) for the Save dialog, from the current project."""
        p = self.ctl.project_path
        if not p:
            return "My song", None
        name = os.path.splitext(os.path.basename(p))[0]
        if name.endswith(".ledwall"):
            name = name[:-8]
        folder = os.path.dirname(os.path.abspath(p))
        in_own_folder = os.path.basename(folder) == name
        return name, os.path.dirname(folder) if in_own_folder else folder

    def save_project(self, ask: bool = False) -> bool:
        path = self.ctl.project_path
        # old .json projects are saved as a new .ledwall project (the .json is left as it was)
        if ask or not path or not path.lower().endswith(proj.EXT):
            name, loc = self._project_dialog_defaults()
            audio = self.ctl.song.audio_path
            dlg = ProjectDialog(self, "Save project", name, loc, has_audio=bool(audio and os.path.isfile(audio)))
            if not dlg.exec():
                return False
            path = dlg.path
            if dlg.copy_media and audio and os.path.isfile(audio):
                try:
                    self.ctl.song.audio_path = proj.copy_into_media(audio, path)
                except OSError as exc:
                    QMessageBox.warning(self, "Audio", f"Couldn't copy the audio into the Media folder:\n{exc}")
        return self._write_project(path)

    def _write_project(self, path: str) -> bool:
        try:
            proj.save(self.ctl.song, path)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.ctl.project_path = path
        self.ctl.dirty = False
        self._add_recent(path)
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def open_project(self, path: str | None = None):
        if not self._confirm_discard():
            return
        if not path:
            start = os.path.dirname(self.ctl.project_path) if self.ctl.project_path else \
                QSettings().value("project/location", proj.default_location())
            path, _ = QFileDialog.getOpenFileName(self, "Open project", start, proj.OPEN_FILTER)
            if not path:
                return
        try:
            song = proj.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"{path}\n\n{exc}")
            return
        self.pause()
        self.ctl.set_analysis(None)
        if self.player:
            self.player.setSource(QUrl())
        self.ctl.set_song(song)
        self.ctl.project_path = path
        self._add_recent(path)
        self._update_title()
        if song.audio_path and os.path.exists(song.audio_path):
            self.load_audio(song.audio_path, set_tempo=False)
        elif song.audio_path:
            QMessageBox.warning(self, "Audio missing", f"Audio file not found:\n{song.audio_path}\n\n"
                                "Put it in the project's Media folder, or load it again with File → Load Audio.")
        self.seek(0.0)

    # ---- recent projects ------------------------------------------------------------------------
    def _recent(self) -> list:
        v = QSettings().value("recent_projects", [])
        return [p for p in (v if isinstance(v, list) else [v] if v else []) if p]

    def _add_recent(self, path: str):
        path = os.path.abspath(path)
        lst = [path] + [p for p in self._recent() if p != path]
        QSettings().setValue("recent_projects", lst[:10])

    def _fill_recent(self):
        self.m_recent.clear()
        items = [p for p in self._recent() if os.path.exists(p)]
        for p in items:
            a = self.m_recent.addAction(f"{os.path.splitext(os.path.basename(p))[0]}   —   {os.path.dirname(p)}")
            a.triggered.connect(lambda _=False, p=p: self.open_project(p))
        if not items:
            self.m_recent.addAction("(none yet)").setEnabled(False)

    def show_project_folder(self):
        p = self.ctl.project_path
        folder = os.path.dirname(os.path.abspath(p)) if p else QSettings().value("project/location", proj.default_location())
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ---- updates ---------------------------------------------------------------------------------
    def check_updates(self, manual: bool):
        if self.update_checker and self.update_checker.isRunning():
            return
        self.update_checker = UpdateChecker(self)
        self.update_checker.found.connect(lambda info: self._update_found(info, manual))
        if manual:
            self.update_checker.none.connect(lambda: QMessageBox.information(
                self, "Updates", f"You have the latest version ({__version__})."))
            self.update_checker.failed.connect(lambda m: QMessageBox.warning(
                self, "Updates", f"Couldn't check for updates (are you online?).\n\n{m}"))
        self.update_checker.start()

    def _update_found(self, info: dict, manual: bool):
        if not manual and skipped(info["version"]):
            return
        UpdateDialog(info, before_install=self._confirm_discard, quit_app=self._quit_for_update,
                     open_project=lambda: self.ctl.project_path, parent=self).exec()

    def _quit_for_update(self):
        self.ctl.dirty = False          # already saved or discarded in before_install
        self.close()

    def show_about(self):
        QMessageBox.about(self, "About", f"<b>LED Wall Creator</b> {__version__}<br><br>"
                          "Audio-synced LED wall loops for live shows.<br>"
                          f'<a href="https://github.com/{updater.GITHUB_REPO}">github.com/{updater.GITHUB_REPO}</a>')

    # ---- export --------------------------------------------------------------------------------------
    def render_video(self):
        if self.export_worker and self.export_worker.isRunning():
            self.export_worker.cancel()
            return
        p = self.ctl.project_path
        default = (os.path.join(os.path.dirname(os.path.abspath(p)), os.path.splitext(os.path.basename(p))[0] + ".mp4")
                   if p else "ledwall_loop.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Render video", default, "MP4 video (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        self.pause()
        w, h = self.export_tab.resolution()
        et = self.export_tab
        self.export_worker = ExportWorker(self.ctl.song, self.ctl.analysis, w, h, et.fps(), path,
                                          et.chk_ffmpeg.isChecked(), et.chk_audio.isChecked(), et.spin_bitrate.value(), self,
                                          prefer_gpu=et.chk_gpu.isChecked())
        self.export_worker.progress.connect(lambda v, m: (et.progress.setValue(v), self.statusBar().showMessage(m)))
        self.export_worker.finished_ok.connect(self._on_export_done)
        self.export_worker.failed.connect(self._on_export_failed)
        et.progress.setValue(0)
        et.set_busy(True)
        self.export_started = QElapsedTimer()
        self.export_started.start()
        self.tabs.setCurrentWidget(et)
        self.export_worker.start()

    def _on_export_done(self, path):
        self.export_tab.set_busy(False)
        took = self.export_started.elapsed() / 1000 if hasattr(self, "export_started") else 0
        QMessageBox.information(self, "Render complete", f"Video written to:\n{path}\n\n"
                                f"{took:.0f} s with {self.export_worker.encoder_label}.")

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
        if self.big_window is not None:
            self.big_window.close()
        super().closeEvent(ev)
