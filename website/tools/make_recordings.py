"""Screen recordings of the app's workflows for the website's feature pages.

The real app is driven by a script (offscreen): a drawn cursor moves, clicks, drags and types,
every frame is grabbed and piped into ffmpeg, and the preview plays in sync with the frames.

    cd python && .venv/bin/python ../website/tools/make_recordings.py [name ...]
Writes website/media/rec-<name>.mp4 and rec-<name>.jpg (poster). Needs tools/demo.ledwall and the
demo song next to the repo root (see make_demo.py).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "python"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])
app.setOrganizationName("LEDWallCreator-recordings")
app.setStyle("Fusion")
from ledwall.resources import load_bundled_fonts, set_app_icon  # noqa: E402
load_bundled_fonts()
set_app_icon(app)
from ledwall.gui.common import DARK_QSS  # noqa: E402
app.setStyleSheet(DARK_QSS)
from ledwall.audio import FFMPEG  # noqa: E402
from ledwall.gui.main_window import MainWindow  # noqa: E402
from ledwall.model import el_lane  # noqa: E402

MEDIA = os.path.join(ROOT, "website", "media")
FPS = 30
W, H = 1600, 960
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def find_song():
    """The demo song: SONG env var, else the first .mp3 at the repo root."""
    import glob
    p = os.environ.get("SONG")
    if p and os.path.isfile(p):
        return p
    found = sorted(glob.glob(os.path.join(ROOT, "*.mp3")))
    return found[0] if found else None


class FakeClock:
    def __init__(self, rec):
        self.rec = rec

    def elapsed(self):
        return int(self.rec.t_free * 1000)

    def restart(self):
        pass

    def start(self):
        pass


class Recorder:
    def __init__(self, name):
        self.name = name
        self.w = MainWindow()
        self.w.resize(W, H)
        self.w.show()
        self.w.timer.stop()                    # frames are rendered by the recorder, in step
        self.w.free_clock = FakeClock(self)
        self.t_free = 0.0
        self.playing = False
        self.cursor = QPointF(W * 0.62, H * 0.55)
        self.ripple = 0.0
        self.pressed = False
        self.frames = 0
        self.poster_at = None
        self.proc = None

    # ---- setup -------------------------------------------------------------------------------
    def open_demo(self):
        w = self.w
        w.open_project(os.path.join(HERE, "demo.ledwall"))
        if find_song():
            w.load_audio(find_song(), set_tempo=False)
        for _ in range(800):
            app.processEvents()
            time.sleep(0.01)
            if w.ctl.analysis is not None and not (w.audio_worker and w.audio_worker.isRunning()):
                break
        w.ctl.dirty = False
        return w

    def start(self):
        out = os.path.join(MEDIA, f"rec-{self.name}.mp4")
        self.out = out
        cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
               "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "25", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", out]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def finish(self):
        self.proc.stdin.close()
        self.proc.wait()
        self.w.ctl.dirty = False
        self.w.close()
        print(f"{self.out}: {os.path.getsize(self.out) / 1e6:.1f} MB, {self.frames / FPS:.1f} s")

    # ---- frames ------------------------------------------------------------------------------
    def play(self, on=True):
        w = self.w
        self.playing = on
        w.playing = False                      # the window's own clock stays off
        w.btn_play.setText("Pause" if on else "Play")

    def frame(self, n=1):
        w = self.w
        for _ in range(n):
            self.t_free += 1.0 / FPS
            if self.playing:
                t = w.current_t + 1.0 / FPS
                sec = w.section or (w.ctl.song.region if w.ctl.song.loop else None)
                if sec and t >= sec[1]:
                    t = sec[0]
                w.current_t = t
            w._dirty = True
            app.processEvents()
            w._render_preview()
            if w.tabs.currentWidget() is w.export_tab:
                w.export_tab.set_position(w.current_t)
            app.processEvents()
            img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
            ptr = img.constBits()
            ptr.setsize(img.sizeInBytes())
            arr = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())[:, :img.width() * 3]
            arr = cv2.cvtColor(arr.reshape(img.height(), img.width(), 3), cv2.COLOR_RGB2BGR)
            if arr.shape[1] != W or arr.shape[0] != H:
                arr = cv2.resize(arr, (W, H), interpolation=cv2.INTER_AREA)
            self._draw_cursor(arr)
            self.proc.stdin.write(arr.tobytes())
            self.frames += 1
            if self.poster_at is not None and self.frames == self.poster_at:
                cv2.imwrite(os.path.join(MEDIA, f"rec-{self.name}.jpg"), arr, [cv2.IMWRITE_JPEG_QUALITY, 82])

    def poster_here(self):
        self.poster_at = self.frames + 1

    def wait(self, seconds):
        self.frame(max(1, int(seconds * FPS)))

    def _draw_cursor(self, img):
        x, y = self.cursor.x(), self.cursor.y()
        if self.ripple > 0:
            r = int(10 + (1 - self.ripple) * 22)
            a = self.ripple
            ov = img.copy()
            cv2.circle(ov, (int(x), int(y)), r, (255, 120, 180), 3, cv2.LINE_AA)
            cv2.addWeighted(ov, a, img, 1 - a, 0, dst=img)
            self.ripple = max(0.0, self.ripple - 0.12)
        if self.pressed:
            cv2.circle(img, (int(x), int(y)), 13, (255, 140, 190), -1, cv2.LINE_AA)
        pts = np.array([[0, 0], [0, 21], [5, 16], [9, 25], [13, 23], [9, 15], [16, 15]], np.float32)
        pts = (pts * 1.1 + [x, y]).astype(np.int32)
        cv2.fillPoly(img, [pts], (20, 20, 24), cv2.LINE_AA)
        cv2.polylines(img, [pts], True, (255, 255, 255), 2, cv2.LINE_AA)

    # ---- cursor + input ------------------------------------------------------------------------
    def win_pos(self, widget, p):
        q = widget.mapTo(self.w, QPoint(int(p[0]), int(p[1])))
        return QPointF(q.x(), q.y())

    def move(self, widget, p, seconds=0.6, during=None):
        target = self.win_pos(widget, p)
        start = QPointF(self.cursor)
        n = max(1, int(seconds * FPS))
        for i in range(1, n + 1):
            u = i / n
            u = u * u * (3 - 2 * u)
            self.cursor = start + (target - start) * u
            if during:
                during(u)
            self.frame()

    def _send(self, widget, etype, p, button=Qt.MouseButton.LeftButton, buttons=None):
        local = QPointF(p[0], p[1])
        glob = QPointF(widget.mapToGlobal(QPoint(int(p[0]), int(p[1]))))
        if buttons is None:
            buttons = Qt.MouseButton.LeftButton if etype != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        ev = QMouseEvent(etype, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, ev)

    def click(self, widget, p, seconds=0.5, double=False):
        self.move(widget, p, seconds)
        self.pressed = True
        self._send(widget, QEvent.Type.MouseButtonPress, p)
        self.frame(2)
        self._send(widget, QEvent.Type.MouseButtonRelease, p)
        if double:
            self._send(widget, QEvent.Type.MouseButtonDblClick, p)
            self._send(widget, QEvent.Type.MouseButtonRelease, p)
        self.pressed = False
        self.ripple = 1.0
        self.frame(4)

    def drag(self, widget, a, b, seconds=0.8, move_first=0.5):
        self.move(widget, a, move_first)
        self.pressed = True
        self._send(widget, QEvent.Type.MouseButtonPress, a)
        self.frame(2)
        n = max(2, int(seconds * FPS))
        for i in range(1, n + 1):
            u = i / n
            u = u * u * (3 - 2 * u)
            p = (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
            self._send(widget, QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
            self.cursor = self.win_pos(widget, p)
            self.frame()
        self._send(widget, QEvent.Type.MouseButtonRelease, b)
        self.pressed = False
        self.frame(3)

    def slide(self, slider_widget, value, seconds=1.0):
        """Move a LabeledSlider to `value`, the cursor riding its handle."""
        s = slider_widget.slider
        lo, hi = s.minimum(), s.maximum()
        start_v = s.value()
        end_v = int(round((value - slider_widget.lo) / slider_widget.step))

        def handle_x(v):
            return 8 + (s.width() - 16) * (v - lo) / max(1, hi - lo)
        self.move(s, (handle_x(start_v), s.height() / 2), 0.5)
        self.pressed = True
        n = max(2, int(seconds * FPS))
        for i in range(1, n + 1):
            u = i / n
            u = u * u * (3 - 2 * u)
            v = int(round(start_v + (end_v - start_v) * u))
            s.setValue(v)
            self.cursor = self.win_pos(s, (handle_x(v), s.height() / 2))
            self.frame()
        self.pressed = False
        self.frame(3)

    def choose(self, combo, index, seconds=0.5):
        self.click(combo, (combo.width() * 0.5, combo.height() / 2), seconds)
        combo.setCurrentIndex(index)
        self.frame(6)

    def type(self, line_edit, text, select_all=True):
        self.click(line_edit, (line_edit.width() * 0.4, line_edit.height() / 2))
        if select_all:
            line_edit.selectAll()
        for ch in text:
            QTest.keyClicks(line_edit, ch)
            self.frame(3)
        self.frame(6)


# ===================================================================================================
def rec_pattern():
    r = Recorder("pattern")
    w = r.open_demo()
    s, eng = w.ctl.song, w.ctl.engine
    chorus = next(p for p in s.patterns if p.name == "Chorus")
    beam = next(e for e in s.elements if e.type == "beam")
    left, right = s.groups[0].id, s.groups[1].id
    chorus.notes = [n for n in chorus.notes if not n.lane.startswith(f"e:{beam.id}|g:")]
    clip = next(c for c in s.clips if c.pattern_id == chorus.id)
    w._edit_pattern(chorus.id, 0, eng.b2t(clip.start))
    g = w.pattern_tab.grid
    g.collapsed = {e.id for e in s.elements if e.id != beam.id} | {"global"}
    g.refresh_size()
    for _ in range(10):
        app.processEvents()
    g.fit()
    app.processEvents()
    keys = [k for k, *_ in g.lanes()]
    row = lambda lane: g.RULER_H + keys.index(lane) * g.ROW_H + g.ROW_H / 2
    r.start()
    r.play(True)
    r.wait(1.2)
    for st, lane in ((0, f"g:{left}"), (4, f"g:{right}"), (8, f"g:{left}"), (12, f"g:{right}")):
        y = row(el_lane(beam.id, lane))
        r.drag(g, (g.x_of(st) + 4, y), (g.x_of(st + 4) - 4, y), 0.45, 0.45)
        if st == 4:
            r.poster_here()
    r.wait(1.0)
    y = row(el_lane(beam.id, "pulse"))
    r.drag(g, (g.x_of(0) + 4, y), (g.x_of(16) - 4, y), 0.9, 0.6)
    r.wait(1.2)
    r.choose(w.pattern_tab.combo_speed, 3)        # double time
    r.wait(2.4)
    r.finish()


def rec_master():
    r = Recorder("master")
    w = r.open_demo()
    s, eng = w.ctl.song, w.ctl.engine
    w.tabs.setCurrentWidget(w.master_tab)
    v = w.master_tab.view
    app.processEvents()
    v.view_start, v.view_end = eng.b2t(60) - v.offset, eng.b2t(124) - v.offset
    v.update()
    w.seek(eng.b2t(76))
    r.start()
    r.play(True)
    r.wait(1.5)
    peak = next(c for c in s.clips if s.pattern(c.pattern_id).name == "Peak")
    y = v.track_y(0) + v.TRACK_H / 2
    x0 = v.t2x(eng.b2t(peak.start + 1))
    r.drag(v, (x0, y), (v.t2x(eng.b2t(peak.start + 1)), v.track_y(1) + v.TRACK_H / 2), 0.7)
    r.poster_here()
    r.wait(1.2)
    x_end = v.t2x(eng.b2t(peak.start + peak.length)) - 2
    r.drag(v, (x_end, v.track_y(1) + v.TRACK_H / 2), (v.t2x(eng.b2t(peak.start + peak.length + 8)), v.track_y(1) + v.TRACK_H / 2), 0.9)
    r.wait(1.5)
    tb = w.master_tab.tempo_bar
    r.click(tb.spin_bpm, (tb.spin_bpm.width() / 2, tb.spin_bpm.height() / 2))
    r.wait(2.5)
    r.finish()


def rec_elements():
    r = Recorder("elements")
    w = r.open_demo()
    s = w.ctl.song
    w.tabs.setCurrentWidget(w.elements_tab)
    et = w.elements_tab
    sun = next(e for e in s.elements if e.type == "sun")
    beam = next(e for e in s.elements if e.type == "beam")
    r.start()
    it = lambda eid: next(et.list.item(i) for i in range(et.list.count())
                          if et.list.item(i).data(Qt.ItemDataRole.UserRole) == eid)
    rect = et.list.visualItemRect(it(sun.id))
    r.click(et.list.viewport(), (rect.center().x() + 30, rect.center().y()), 0.7)
    r.wait(0.8)
    r.slide(et.sliders["size"], 0.75, 1.2)
    r.slide(et.sliders["core"], 0.9, 0.9)
    r.poster_here()
    r.wait(0.8)
    rect = et.list.visualItemRect(it(beam.id))
    r.click(et.list.viewport(), (rect.center().x() + 30, rect.center().y()), 0.7)
    r.wait(0.6)
    r.slide(et.sliders["count"], 10, 1.0)
    r.slide(et.sliders["lines"], 3, 0.8)
    r.slide(et.sliders["y"], 0.75, 1.0)
    r.wait(1.5)
    r.finish()


def rec_custom():
    r = Recorder("custom")
    w = r.open_demo()
    w.tabs.setCurrentWidget(w.elements_tab)
    et = w.elements_tab
    r.start()
    r.wait(0.5)
    et.add("custom")
    el = et.element
    r.frame(8)
    for kind in ("rays", "arc"):
        et.add_shape(kind)
        r.frame(12)
    r.wait(0.4)
    sh_sliders = getattr(et, "shape_sliders", {})
    if "dashes" in sh_sliders:
        r.slide(sh_sliders["dashes"], 16, 1.0)
    if "size" in sh_sliders:
        r.slide(sh_sliders["size"], 1.4, 1.0)
    r.poster_here()
    r.slide(et.sliders["size"], 0.45, 1.0)
    r.slide(et.sliders["spin"], 6, 0.8)
    r.wait(2.0)
    r.finish()


def rec_haze():
    r = Recorder("haze")
    w = r.open_demo()
    s = w.ctl.song
    w.tabs.setCurrentWidget(w.elements_tab)
    et = w.elements_tab
    beam = next(e for e in s.elements if e.type == "beam")
    beam.haze = 0.0
    et.select(beam.id)
    r.start()
    r.wait(1.0)
    r.slide(et.sliders["haze"], 1.4, 2.0)
    r.poster_here()
    r.wait(1.0)
    r.slide(et.sliders["glow"], 2.2, 1.2)
    r.wait(0.6)
    haze = next(e for e in s.elements if e.type == "haze")
    et.select(haze.id)
    r.frame(10)
    r.slide(et.sliders["glow"], 5.0, 1.4)
    r.slide(et.sliders["size"], 0.9, 1.0)
    r.wait(1.5)
    r.finish()


def rec_bandname():
    r = Recorder("bandname")
    w = r.open_demo()
    lk = w.ctl.song.look
    lk.logo_text, lk.logo_color, lk.logo_size = "", "white", 0.3
    w.tabs.setCurrentWidget(w.elements_tab)
    et = w.elements_tab
    et.select("__logo__")
    from ledwall.gui.band_name_panel import BandNamePanel
    pan = w.findChild(BandNamePanel)
    pan.reload()
    r.start()
    r.wait(0.6)
    r.type(pan.edit_text, "your band")
    r.wait(0.6)
    for fam in ("Bebas Neue", "Righteous", "Anton"):
        r.choose(pan.combo_quick, pan.combo_quick.findData(fam), 0.4)
        r.wait(0.5)
    r.poster_here()
    r.slide(pan._sliders["logo_size"], 0.42, 1.0)
    r.slide(pan._sliders["logo_glow"], 1.6, 1.0)
    r.wait(1.5)
    r.finish()


def rec_stage():
    r = Recorder("stage")
    w = r.open_demo()
    eng = w.ctl.engine
    w.tabs.setCurrentWidget(w.export_tab)
    ex = w.export_tab
    app.processEvents()
    w.seek(eng.b2t(84))
    r.start()
    r.play(True)
    r.wait(1.2)
    r.choose(ex.combo_view, ex.combo_view.findData("led"))
    r.wait(2.0)
    r.poster_here()
    r.choose(ex.combo_view, ex.combo_view.findData("projector"))
    r.wait(2.0)
    r.click(ex.chk_house, (8, ex.chk_house.height() / 2))
    r.wait(1.2)
    r.click(ex.chk_house, (8, ex.chk_house.height() / 2))
    r.wait(1.5)
    r.finish()


def rec_export():
    r = Recorder("export")
    w = r.open_demo()
    s, eng = w.ctl.song, w.ctl.engine
    w.tabs.setCurrentWidget(w.export_tab)
    ex = w.export_tab
    ex.combo_view.setCurrentIndex(ex.combo_view.findData("led"))
    for _ in range(80):
        app.processEvents()
        time.sleep(0.03)
        if "render threads" in ex.lbl_encoder.text():
            break
    import tempfile
    from PyQt6.QtWidgets import QFileDialog
    out = os.path.join(tempfile.mkdtemp(), "show.mp4")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
    w.seek(s.region[0])
    r.start()
    r.play(True)
    r.wait(1.0)
    r.choose(ex.combo_fps, ex.combo_fps.findText("60"))
    r.click(ex.btn_render, (ex.btn_render.width() / 2, ex.btn_render.height() / 2), 0.7)
    r.play(False)
    r.poster_here()
    k = 0
    while w.export_worker and w.export_worker.isRunning():     # time-lapse while it renders
        app.processEvents()
        time.sleep(0.02)
        k += 1
        if k % 8 == 0:
            r.frame()
    r.frame(int(1.5 * FPS))
    r.finish()


RECORDINGS = {"pattern": rec_pattern, "master": rec_master, "elements": rec_elements, "custom": rec_custom,
              "haze": rec_haze, "bandname": rec_bandname, "stage": rec_stage, "export": rec_export}

if __name__ == "__main__":
    names = sys.argv[1:] or list(RECORDINGS)
    for n in names:
        RECORDINGS[n]()
