"""Master track: song timeline with the waveform, 3 pattern tracks and a lyrics lane.
Clips reference patterns (edit the pattern once, every copy follows) and loop when stretched."""

from __future__ import annotations

import copy
import math

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from ..model import BEATS_PER_BAR, N_TRACKS, STEPS_PER_BEAT, Clip, LyricClip, new_id
from .common import hint

_CLIP_CLIPBOARD: list = []   # [("clip", pattern_id, track, rel_start, length) | ("lyric", text, rel_start, length)]


class ArrangementView(QWidget):
    seekRequested = pyqtSignal(float)
    editPattern = pyqtSignal(str)
    regionChanged = pyqtSignal()

    HEADER_W = 96
    RULER_H = 22
    WAVE_H = 54
    TRACK_H = 46
    LYRIC_H = 32
    EDGE_PX = 7

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.view_start, self.view_end = 0.0, max(30.0, ctl.engine.duration)
        self.playhead = 0.0
        self.snap = "bar"
        self.selected: list = []
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(self.RULER_H + self.WAVE_H + N_TRACKS * self.TRACK_H + self.LYRIC_H + 4)

    # ---- geometry ---------------------------------------------------------------------
    @property
    def eng(self):
        return self.ctl.engine

    def track_y(self, i: int) -> float:
        return self.RULER_H + self.WAVE_H + i * self.TRACK_H

    @property
    def lyric_y(self) -> float:
        return self.track_y(N_TRACKS)

    def row_at(self, y: float):
        if y < self.RULER_H:
            return "ruler"
        if y < self.RULER_H + self.WAVE_H:
            return "wave"
        if y < self.lyric_y:
            return ("track", int((y - self.track_y(0)) // self.TRACK_H))
        if y < self.lyric_y + self.LYRIC_H:
            return "lyrics"
        return None

    def t2x(self, t: float) -> float:
        return self.HEADER_W + (t - self.view_start) / (self.view_end - self.view_start) * (self.width() - self.HEADER_W)

    def x2t(self, x: float) -> float:
        return self.view_start + (x - self.HEADER_W) / max(1, self.width() - self.HEADER_W) * (self.view_end - self.view_start)

    def b2x(self, b: float) -> float:
        return self.t2x(self.eng.b2t(b))

    def x2b(self, x: float) -> float:
        return self.eng.t2b(self.x2t(x))

    def snap_unit(self) -> float:
        return {"bar": float(BEATS_PER_BAR), "beat": 1.0}.get(self.snap, 1.0 / STEPS_PER_BEAT)

    def snap_b(self, b: float, mode="round") -> float:
        u = self.snap_unit()
        f = math.floor if mode == "floor" else round
        return f(b / u) * u

    def reset_view(self):
        self.view_start, self.view_end = 0.0, max(10.0, self.eng.duration)
        self.update()

    def set_playhead(self, t: float):
        self.playhead = t
        span = self.view_end - self.view_start
        if t > self.view_end or t < self.view_start:
            self.view_start = max(0.0, min(t - span * 0.1, self.eng.duration - span))
            self.view_end = self.view_start + span
        self.update()

    def clip_rect(self, c: Clip) -> QRectF:
        x0, x1 = self.b2x(c.start), self.b2x(c.start + c.length)
        return QRectF(x0, self.track_y(c.track) + 2, max(4.0, x1 - x0), self.TRACK_H - 4)

    def lyric_rect(self, l: LyricClip) -> QRectF:
        x0, x1 = self.b2x(l.start), self.b2x(l.start + l.length)
        return QRectF(x0, self.lyric_y + 2, max(4.0, x1 - x0), self.LYRIC_H - 4)

    def item_at(self, pos: QPointF):
        for c in reversed(self.ctl.song.clips):
            if self.clip_rect(c).contains(pos):
                return c
        for l in reversed(self.ctl.song.lyrics):
            if self.lyric_rect(l).contains(pos):
                return l
        return None

    def rect_of(self, item) -> QRectF:
        return self.clip_rect(item) if isinstance(item, Clip) else self.lyric_rect(item)

    # ---- painting -------------------------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        song, eng = self.ctl.song, self.eng
        p.fillRect(0, 0, w, h, QColor(11, 11, 11))
        for i in range(N_TRACKS):
            p.fillRect(QRectF(self.HEADER_W, self.track_y(i), w, self.TRACK_H), QColor(18 if i % 2 else 15, 18 if i % 2 else 15, 18 if i % 2 else 15))
        p.fillRect(QRectF(self.HEADER_W, self.lyric_y, w, self.LYRIC_H), QColor(22, 18, 12))

        # region
        if song.region:
            x0, x1 = self.t2x(song.region[0]), self.t2x(song.region[1])
            p.fillRect(QRectF(x0, 0, x1 - x0, h), QColor(255, 140, 20, 55 if song.loop else 30))
            p.setPen(QPen(QColor(255, 150, 40), 1))
            p.drawLine(QPointF(x0, 0), QPointF(x0, h))
            p.drawLine(QPointF(x1, 0), QPointF(x1, h))

        # bar grid
        b0, b1 = eng.t2b(self.view_start), eng.t2b(self.view_end)
        px_per_bar = (self.b2x(4) - self.b2x(0))
        step_bars = 1 if px_per_bar > 22 else (4 if px_per_bar > 6 else 16)
        p.setFont(QFont("Sans", 8))
        for bar in range(max(0, int(b0 // 4) - 1), int(b1 // 4) + 2):
            if bar % step_bars:
                continue
            x = self.b2x(bar * 4)
            if x < self.HEADER_W:
                continue
            p.setPen(QPen(QColor(55, 55, 55) if bar % 4 == 0 else QColor(32, 32, 32), 1))
            p.drawLine(QPointF(x, self.RULER_H), QPointF(x, h))
            p.setPen(QColor(150, 150, 150))
            p.drawText(QPointF(x + 2, 14), str(bar + 1))
        if px_per_bar > 60:
            p.setPen(QPen(QColor(26, 26, 26), 1))
            for bt in range(max(0, int(b0)), int(b1) + 1):
                if bt % 4:
                    x = self.b2x(bt)
                    p.drawLine(QPointF(x, self.track_y(0)), QPointF(x, h))

        # waveform
        a = self.ctl.analysis
        ww = w - self.HEADER_W
        mid = self.RULER_H + self.WAVE_H / 2
        if a is not None and ww > 2:
            n = len(a.peaks_max)
            xs = np.arange(ww + 1)
            ts = self.view_start + xs / ww * (self.view_end - self.view_start)
            idx = np.clip((ts / a.duration * n).astype(np.int64), 0, n - 1)
            mx = np.maximum.reduceat(a.peaks_max, idx[:-1])
            mn = np.minimum.reduceat(a.peaks_min, idx[:-1])
            sc = self.WAVE_H / 2 * 0.92 / max(1e-6, float(np.max(np.abs(a.peaks_max))))
            p.setPen(QPen(QColor(255, 140, 20, 160), 1))
            p.drawLines([QLineF(self.HEADER_W + i, mid - mx[i] * sc, self.HEADER_W + i, mid - mn[i] * sc) for i in range(ww)])
        else:
            p.setPen(QColor(90, 90, 90))
            p.drawText(QRectF(self.HEADER_W, self.RULER_H, ww, self.WAVE_H), Qt.AlignmentFlag.AlignCenter,
                       "No audio loaded (Look & Song tab → Load Audio)")

        # clips
        lanes = [k for k, _, _ in song.lanes()]
        p.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        for c in song.clips:
            pat = song.pattern(c.pattern_id)
            if not pat:
                continue
            r = self.clip_rect(c)
            if r.right() < self.HEADER_W or r.left() > w:
                continue
            cr, cg, cb = pat.color
            p.fillRect(r, QColor(cr // 3, cg // 3, cb // 3))
            p.fillRect(QRectF(r.left(), r.top(), r.width(), 13), QColor(cr, cg, cb, 200))
            # mini notes, repeated for every loop of the pattern inside the clip
            if lanes and r.width() > 8:
                inner_h = r.height() - 16
                lh = inner_h / len(lanes)
                reps = int(math.ceil(c.length / pat.beats))
                p.setPen(Qt.PenStyle.NoPen)
                for k in range(reps):
                    base_b = c.start + k * pat.beats
                    for nt in pat.notes:
                        if nt.lane not in lanes:
                            continue
                        sb = base_b + nt.start / STEPS_PER_BEAT
                        if sb >= c.start + c.length:
                            continue
                        eb = min(sb + nt.length / STEPS_PER_BEAT, c.start + c.length)
                        x0, x1 = self.b2x(sb), self.b2x(eb)
                        y = r.top() + 14 + lanes.index(nt.lane) * lh
                        p.fillRect(QRectF(x0, y, max(1.0, x1 - x0 - 0.5), max(1.0, lh - 0.5)), QColor(cr, cg, cb, int(80 + 150 * nt.velocity)))
                    if k > 0:
                        xk = self.b2x(base_b)
                        p.setPen(QPen(QColor(0, 0, 0, 200), 1, Qt.PenStyle.DashLine))
                        p.drawLine(QPointF(xk, r.top()), QPointF(xk, r.bottom()))
                        p.setPen(Qt.PenStyle.NoPen)
            p.setPen(QColor(0, 0, 0))
            p.drawText(QRectF(r.left() + 3, r.top(), r.width() - 4, 13), Qt.AlignmentFlag.AlignVCenter, pat.name)
            p.setPen(QPen(QColor(255, 255, 255), 2) if c in self.selected else QPen(QColor(cr, cg, cb), 1))
            p.drawRect(r)

        # lyrics
        p.setFont(QFont("Sans", 9))
        for l in song.lyrics:
            r = self.lyric_rect(l)
            p.fillRect(r, QColor(120, 90, 40))
            p.setPen(QColor(255, 235, 200))
            p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, l.text)
            p.setPen(QPen(QColor(255, 255, 255), 2) if l in self.selected else QPen(QColor(200, 160, 90), 1))
            p.drawRect(r)

        # rubber band
        if self._drag and self._drag["mode"] == "rubber":
            p.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine))
            p.drawRect(self._drag["rect"])

        # playhead
        x = self.t2x(self.playhead)
        if x >= self.HEADER_W:
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))

        # header
        p.fillRect(QRectF(0, 0, self.HEADER_W, h), QColor(20, 20, 20))
        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont("Sans", 9))
        p.drawText(QRectF(6, self.RULER_H, self.HEADER_W, self.WAVE_H), Qt.AlignmentFlag.AlignVCenter, "Audio")
        for i in range(N_TRACKS):
            p.drawText(QRectF(6, self.track_y(i), self.HEADER_W, self.TRACK_H), Qt.AlignmentFlag.AlignVCenter, f"Track {i + 1}")
        p.drawText(QRectF(6, self.lyric_y, self.HEADER_W, self.LYRIC_H), Qt.AlignmentFlag.AlignVCenter, "Lyrics")
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawLine(QPointF(self.HEADER_W, 0), QPointF(self.HEADER_W, h))
        p.end()

    # ---- editing ---------------------------------------------------------------------------------
    def _commit(self, key=None):
        self.ctl.commit("arrangement", key)
        self.update()

    def place_pattern(self, beat: float, track: int, pid: str | None = None):
        pid = pid or self.ctl.current_pattern_id
        pat = self.ctl.song.pattern(pid) if pid else None
        if not pat:
            return
        c = Clip(new_id(), pat.id, max(0, min(N_TRACKS - 1, track)), max(0.0, beat), pat.beats)
        self.ctl.song.clips.append(c)
        self.selected = [c]
        self._commit()

    def add_lyric(self, beat: float, text: str | None = None):
        if text is None:
            text, ok = QInputDialog.getText(self, "Lyric", "Text shown in the top band:")
            if not ok or not text.strip():
                return
        l = LyricClip(new_id(), text.strip(), max(0.0, beat), 2.0 * BEATS_PER_BAR)
        self.ctl.song.lyrics.append(l)
        self.selected = [l]
        self._commit()

    def delete_selected(self):
        if not self.selected:
            return
        song = self.ctl.song
        song.clips = [c for c in song.clips if c not in self.selected]
        song.lyrics = [l for l in song.lyrics if l not in self.selected]
        self.selected = []
        self._commit()

    def copy_selected(self):
        global _CLIP_CLIPBOARD
        if not self.selected:
            return
        s0 = min(i.start for i in self.selected)
        _CLIP_CLIPBOARD = [("clip", i.pattern_id, i.track, i.start - s0, i.length) if isinstance(i, Clip)
                           else ("lyric", i.text, i.start - s0, i.length) for i in self.selected]

    def paste(self, at_beat: float | None = None):
        if not _CLIP_CLIPBOARD:
            return
        if at_beat is None:
            at_beat = self.snap_b(self.eng.t2b(self.playhead), "floor")
        new = []
        song = self.ctl.song
        for item in _CLIP_CLIPBOARD:
            if item[0] == "clip" and song.pattern(item[1]):
                c = Clip(new_id(), item[1], item[2], at_beat + item[3], item[4])
                song.clips.append(c)
                new.append(c)
            elif item[0] == "lyric":
                l = LyricClip(new_id(), item[1], at_beat + item[2], item[3])
                song.lyrics.append(l)
                new.append(l)
        self.selected = new
        if new:
            self._commit()

    def duplicate_selected(self):
        if not self.selected:
            return
        s0 = min(i.start for i in self.selected)
        e0 = max(i.start + i.length for i in self.selected)
        self.copy_selected()
        self.paste(s0 + (e0 - s0))

    def make_unique(self, clip: Clip):
        song = self.ctl.song
        src = song.pattern(clip.pattern_id)
        if not src:
            return
        p = copy.deepcopy(src)
        p.id, p.name = new_id(), src.name + " (unique)"
        song.patterns.append(p)
        clip.pattern_id = p.id
        self.ctl.commit("patterns")
        self.update()

    # ---- mouse --------------------------------------------------------------------------------------
    def mousePressEvent(self, ev):
        self.setFocus()
        pos = ev.position()
        if pos.x() < self.HEADER_W:
            return
        row = self.row_at(pos.y())
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if row in ("ruler", "wave"):
            if ev.button() != Qt.MouseButton.LeftButton:
                return
            if shift:
                t0 = self.eng.b2t(self.snap_b(self.x2b(pos.x())))
                self._drag = dict(mode="region", t0=t0)
                self.ctl.song.region = (t0, t0)
            else:
                self._drag = dict(mode="seek")
                self.seekRequested.emit(max(0.0, self.x2t(pos.x())))
            return
        item = self.item_at(pos)
        if ev.button() == Qt.MouseButton.RightButton:
            if item:
                self._context_menu(item, ev.globalPosition().toPoint())
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if item:
            if shift:
                (self.selected.remove if item in self.selected else self.selected.append)(item)
                self.update()
                return
            if item not in self.selected:
                self.selected = [item]
            r = self.rect_of(item)
            if pos.x() > r.right() - self.EDGE_PX:
                self._drag = dict(mode="resize", item=item, changed=False)
            else:
                if ctrl:   # Ctrl-drag copies
                    self.copy_selected()
                    keep = list(self.selected)
                    self.paste(min(i.start for i in keep))
                anchor_track = row[1] if isinstance(row, tuple) else 0
                self._drag = dict(mode="move", anchor_b=self.x2b(pos.x()), anchor_track=anchor_track, changed=ctrl,
                                  orig=[(i, i.start, getattr(i, "track", None)) for i in self.selected])
            self.update()
            return
        if shift:
            self._drag = dict(mode="rubber", origin=pos, rect=QRectF(pos, pos))
            return
        self.selected = []
        self.seekRequested.emit(max(0.0, self.x2t(pos.x())))
        self.update()

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        d = self._drag
        if not d:
            item = self.item_at(pos)
            cur = Qt.CursorShape.ArrowCursor
            if item:
                cur = Qt.CursorShape.SizeHorCursor if pos.x() > self.rect_of(item).right() - self.EDGE_PX else Qt.CursorShape.SizeAllCursor
            self.setCursor(cur)
            return
        if d["mode"] == "seek":
            self.seekRequested.emit(max(0.0, self.x2t(pos.x())))
        elif d["mode"] == "region":
            t1 = self.eng.b2t(self.snap_b(self.x2b(pos.x())))
            self.ctl.song.region = (min(d["t0"], t1), max(d["t0"], t1))
            self.regionChanged.emit()
        elif d["mode"] == "resize":
            it = d["item"]
            new_len = self.snap_b(self.x2b(pos.x()) - it.start)
            it.length = max(self.snap_unit(), new_len)
            d["changed"] = True
        elif d["mode"] == "move" and d["orig"]:
            ref_item, ref_start, _ = d["orig"][0]
            raw = self.x2b(pos.x()) - d["anchor_b"]
            db = self.snap_b(ref_start + raw) - ref_start
            db = max(db, -min(s for _, s, _ in d["orig"]))
            row = self.row_at(pos.y())
            dt = (row[1] - d["anchor_track"]) if isinstance(row, tuple) else 0
            tracks = [t for _, _, t in d["orig"] if t is not None]
            if tracks:
                dt = max(-min(tracks), min(dt, N_TRACKS - 1 - max(tracks)))
            for it, s, t in d["orig"]:
                it.start = s + db
                if t is not None:
                    it.track = t + dt
            d["changed"] = True
        elif d["mode"] == "rubber":
            d["rect"] = QRectF(d["origin"], pos).normalized()
            song = self.ctl.song
            self.selected = [c for c in song.clips if d["rect"].intersects(self.clip_rect(c))] + \
                            [l for l in song.lyrics if d["rect"].intersects(self.lyric_rect(l))]
        self.update()

    def mouseReleaseEvent(self, _ev):
        d, self._drag = self._drag, None
        if not d:
            return
        if d["mode"] == "region":
            r = self.ctl.song.region
            if r and r[1] - r[0] < 0.05:
                self.ctl.song.region = None
            self.ctl.commit("arrangement")
            self.regionChanged.emit()
        elif d.get("changed"):
            self._commit()
        self.update()

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position()
        if pos.x() < self.HEADER_W:
            return
        item = self.item_at(pos)
        row = self.row_at(pos.y())
        if isinstance(item, Clip):
            self.editPattern.emit(item.pattern_id)
        elif isinstance(item, LyricClip):
            text, ok = QInputDialog.getText(self, "Lyric", "Text:", text=item.text)
            if ok and text.strip():
                item.text = text.strip()
                self._commit()
        elif isinstance(row, tuple):
            self.place_pattern(self.snap_b(self.x2b(pos.x()), "floor"), row[1])
        elif row == "lyrics":
            self.add_lyric(self.snap_b(self.x2b(pos.x()), "floor"))

    def _context_menu(self, item, gpos):
        m = QMenu(self)
        if item not in self.selected:
            self.selected = [item]
        a_dup = m.addAction("Duplicate  (Ctrl+D)")
        a_del = m.addAction("Delete  (Del)")
        a_edit = a_uniq = a_fit = None
        if isinstance(item, Clip):
            m.addSeparator()
            a_edit = m.addAction("Edit pattern")
            a_uniq = m.addAction("Make unique (own copy of the pattern)")
            a_fit = m.addAction("Reset length to one pattern loop")
        chosen = m.exec(gpos)
        if chosen is None:
            return
        if chosen is a_dup:
            self.duplicate_selected()
        elif chosen is a_del:
            self.delete_selected()
        elif chosen is a_edit:
            self.editPattern.emit(item.pattern_id)
        elif chosen is a_uniq:
            self.make_unique(item)
        elif chosen is a_fit:
            pat = self.ctl.song.pattern(item.pattern_id)
            if pat:
                item.length = pat.beats
                self._commit()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y() or ev.angleDelta().x()
        span = self.view_end - self.view_start
        dur = max(self.eng.duration, 1.0)
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            pivot = self.x2t(ev.position().x())
            new_span = float(np.clip(span * (0.8 if delta > 0 else 1.25), 2.0, dur))
            frac = (pivot - self.view_start) / span
            self.view_start = max(0.0, pivot - frac * new_span)
            self.view_end = min(dur, self.view_start + new_span)
            self.view_start = max(0.0, self.view_end - new_span)
        else:
            shift = -np.sign(delta) * span * 0.1
            self.view_start = float(np.clip(self.view_start + shift, 0, max(0.0, dur - span)))
            self.view_end = self.view_start + span
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ctrl and k == Qt.Key.Key_C:
            self.copy_selected()
        elif ctrl and k == Qt.Key.Key_X:
            self.copy_selected()
            self.delete_selected()
        elif ctrl and k == Qt.Key.Key_V:
            self.paste()
        elif ctrl and k == Qt.Key.Key_D:
            self.duplicate_selected()
        elif ctrl and k == Qt.Key.Key_A:
            self.selected = list(self.ctl.song.clips) + list(self.ctl.song.lyrics)
            self.update()
        else:
            super().keyPressEvent(ev)


class MasterTab(QWidget):
    editPatternRequested = pyqtSignal(str)

    def __init__(self, ctl, get_playhead, seek, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.get_playhead = get_playhead
        left = QWidget()
        left.setFixedWidth(250)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        form = QFormLayout()
        self.combo_pat = QComboBox()
        self.combo_pat.currentIndexChanged.connect(self._on_combo)
        form.addRow("Pattern", self.combo_pat)
        self.combo_track = QComboBox()
        self.combo_track.addItems([f"Track {i + 1}" for i in range(N_TRACKS)])
        form.addRow("On", self.combo_track)
        self.combo_snap = QComboBox()
        self.combo_snap.addItems(["Bar", "Beat", "1/16"])
        self.combo_snap.currentIndexChanged.connect(lambda i: setattr(self.view, "snap", ["bar", "beat", "step"][i]))
        form.addRow("Snap", self.combo_snap)
        ll.addLayout(form)
        b_place = QPushButton("Place pattern at playhead")
        b_place.clicked.connect(self._place)
        ll.addWidget(b_place)
        row = QHBoxLayout()
        for text, fn in (("Duplicate", lambda: self.view.duplicate_selected()), ("Delete", lambda: self.view.delete_selected())):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        b_lyric = QPushButton("Add lyric at playhead…")
        b_lyric.clicked.connect(lambda: self.view.add_lyric(self.view.snap_b(self.ctl.engine.t2b(self.get_playhead()), "floor")))
        ll.addWidget(b_lyric)
        ll.addSpacing(8)
        ll.addWidget(QLabel("<b>Loop / export region</b>"))
        row2 = QHBoxLayout()
        for text, fn in (("Set In", self._set_in), ("Set Out", self._set_out), ("Clear", self._clear_region)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row2.addWidget(b)
        ll.addLayout(row2)
        self.chk_loop = QCheckBox("Seamless loop")
        self.chk_loop.toggled.connect(self._on_loop)
        ll.addWidget(self.chk_loop)
        self.lbl_region = QLabel()
        self.lbl_region.setObjectName("hint")
        ll.addWidget(self.lbl_region)
        ll.addStretch(1)
        ll.addWidget(hint("Double-click a track: place pattern · drag clip: move (Ctrl copies) · drag right edge: "
                          "stretch (pattern loops) · double-click clip: edit its pattern · right-click: menu · "
                          "Ctrl+C / V (at playhead) / D · Shift-drag ruler: loop region · Shift-drag tracks: select · "
                          "double-click Lyrics lane: add text"))

        self.view = ArrangementView(ctl)
        self.view.seekRequested.connect(seek)
        self.view.editPattern.connect(self.editPatternRequested.emit)
        self.view.regionChanged.connect(self._update_region_label)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(left)
        lay.addWidget(self.view, 1)
        ctl.changed.connect(self._on_changed)
        ctl.currentPatternChanged.connect(self._select_combo)
        self._reload_patterns()
        self._update_region_label()

    def _reload_patterns(self):
        self.combo_pat.blockSignals(True)
        self.combo_pat.clear()
        for p in self.ctl.song.patterns:
            self.combo_pat.addItem(p.name, p.id)
        self.combo_pat.blockSignals(False)
        self._select_combo(self.ctl.current_pattern_id)

    def _select_combo(self, pid):
        i = self.combo_pat.findData(pid)
        if i >= 0:
            self.combo_pat.blockSignals(True)
            self.combo_pat.setCurrentIndex(i)
            self.combo_pat.blockSignals(False)

    def _on_combo(self, i):
        pid = self.combo_pat.itemData(i)
        if pid:
            self.ctl.set_current_pattern(pid)

    def _place(self):
        b = self.view.snap_b(self.ctl.engine.t2b(self.get_playhead()), "floor")
        self.view.place_pattern(b, self.combo_track.currentIndex())

    def _set_in(self):
        t = self.ctl.engine.b2t(self.view.snap_b(self.ctl.engine.t2b(self.get_playhead())))
        end = self.ctl.song.region[1] if self.ctl.song.region else self.ctl.engine.duration
        self.ctl.song.region = (t, max(end, t + self.ctl.engine.period))
        self.ctl.commit("arrangement")
        self._update_region_label()

    def _set_out(self):
        t = self.ctl.engine.b2t(self.view.snap_b(self.ctl.engine.t2b(self.get_playhead())))
        start = self.ctl.song.region[0] if self.ctl.song.region else 0.0
        self.ctl.song.region = (max(0.0, min(start, t - self.ctl.engine.period)), t) if t > 0 else None
        self.ctl.commit("arrangement")
        self._update_region_label()

    def _clear_region(self):
        self.ctl.song.region = None
        self.ctl.commit("arrangement")
        self._update_region_label()

    def _on_loop(self, v):
        if v != self.ctl.song.loop:
            self.ctl.song.loop = v
            self.ctl.commit("arrangement")

    def _update_region_label(self):
        s, e = self.ctl.engine.export_range()
        bars = (e - s) / self.ctl.engine.period / BEATS_PER_BAR
        self.lbl_region.setText(f"{s:.2f}s → {e:.2f}s  ({e - s:.1f}s ≈ {bars:.1f} bars)")
        self.view.update()

    def _on_changed(self, kind):
        if kind in ("all", "patterns"):
            self._reload_patterns()
        if kind in ("all", "audio"):
            self.view.reset_view()
        self.chk_loop.blockSignals(True)
        self.chk_loop.setChecked(self.ctl.song.loop)
        self.chk_loop.blockSignals(False)
        if kind == "all":
            self.view.selected = []
        self._update_region_label()
