"""Pattern editor: drum-machine / piano-roll grid. Every element of the song is a folder of lanes
(All, members, groups, Pulse / Chase / Random / Strobe); notes on them bring the element into the
scene. Global lanes (sparkle, flash, blackout, band name) sit in their own folder at the bottom.
Columns = 1/16 steps. Notes have a start, a length and a velocity (brightness)."""

from __future__ import annotations

import copy
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QScrollBar, QSpinBox, QVBoxLayout, QWidget,
)

from ..model import PATTERN_COLORS, STEPS_PER_BAR, STEPS_PER_BEAT, Note, Pattern, new_id
from .common import LANE_COLORS, ColorButton, hint

_CLIPBOARD: list = []   # [(lane, rel_start, length, velocity)]


class PatternGrid(QWidget):
    selectionChanged = pyqtSignal()
    viewChanged = pyqtSignal()

    HEADER_W = 175
    ROW_H = 24
    RULER_H = 20
    EDGE_PX = 6

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.px = 22.0            # pixels per step
        self.scroll_x = 0.0
        self.snap = 1             # steps
        self.new_velocity = 0.9
        self.selected: list = []
        self.cursor_step = 0
        self.playhead_step: float | None = None
        self.highlight_bar: int | None = None     # bar opened from the master track
        self.collapsed: set = set()               # folded element ids (and "global")
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh_size()

    # ---- model access -----------------------------------------------------------------
    @property
    def pattern(self) -> Pattern | None:
        return self.ctl.current_pattern

    def lanes(self):
        """Visible rows (folders + the lanes of open folders): [(key, label, kind)]."""
        return self.ctl.song.lane_rows(self.collapsed)

    def edit_keys(self) -> list:
        return [k for k, _, kind in self.lanes() if kind != "folder"]

    def _edit_idx_at(self, y: float) -> int:
        """Index into edit_keys() for a y position (folder rows snap to the lane below)."""
        rows = self.lanes()
        row = max(0, min(self.lane_at(y), len(rows) - 1))
        idx = sum(1 for _, _, kind in rows[:row] if kind != "folder")
        return max(0, min(idx, len(self.edit_keys()) - 1))

    def toggle_folder(self, fid: str):
        self.collapsed.symmetric_difference_update({fid})
        self.refresh_size()

    def set_all_collapsed(self, collapsed: bool):
        self.collapsed = ({e.id for e in self.ctl.song.elements} | {"global"}) if collapsed else set()
        self.refresh_size()

    def refresh_size(self):
        self.setMinimumHeight(self.RULER_H + self.ROW_H * len(self.lanes()) + 2)
        self.update()

    def focus_step(self, step: float):
        """Put the edit cursor on `step`, highlight its bar and scroll it into view."""
        pat = self.pattern
        if not pat:
            return
        self.cursor_step = int(min(max(0, self._snapped(step)), pat.steps - 1))
        self.highlight_bar = int(step // STEPS_PER_BAR)
        visible = self.width() - self.HEADER_W
        x = self.cursor_step * self.px
        if not (self.scroll_x <= x <= self.scroll_x + visible - 40):
            self.scroll_x = max(0.0, x - visible * 0.25)
        self.viewChanged.emit()
        self.update()

    def content_width(self) -> float:
        p = self.pattern
        return (p.steps if p else 16) * self.px

    def fit(self):
        p = self.pattern
        if p and self.width() > self.HEADER_W + 50:
            self.px = max(6.0, min(48.0, (self.width() - self.HEADER_W - 12) / p.steps))
        self.scroll_x = 0
        self.viewChanged.emit()
        self.update()

    # ---- coordinates ----------------------------------------------------------------------
    def x_of(self, step: float) -> float:
        return self.HEADER_W + step * self.px - self.scroll_x

    def step_at(self, x: float) -> float:
        return (x - self.HEADER_W + self.scroll_x) / self.px

    def lane_at(self, y: float) -> int:
        return int((y - self.RULER_H) // self.ROW_H)

    def note_rect(self, n: Note, lane_idx: int) -> QRectF:
        return QRectF(self.x_of(n.start) + 1, self.RULER_H + lane_idx * self.ROW_H + 2,
                      max(3.0, n.length * self.px - 2), self.ROW_H - 4)

    def note_at(self, pos: QPointF):
        p = self.pattern
        if not p:
            return None
        keys = [k for k, _, _ in self.lanes()]
        for n in reversed(p.notes):
            if n.lane in keys and self.note_rect(n, keys.index(n.lane)).contains(pos):
                return n
        return None

    # ---- painting --------------------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(12, 12, 12))
        pat = self.pattern
        lanes = self.lanes()
        steps = pat.steps if pat else 16

        # rows
        for i, (key, label, kind) in enumerate(lanes):
            y = self.RULER_H + i * self.ROW_H
            if kind == "folder":
                p.fillRect(QRectF(self.HEADER_W, y, w, self.ROW_H), QColor(34, 34, 38))
                continue
            r, g, b = LANE_COLORS[kind]
            base = 22 if i % 2 else 17
            p.fillRect(QRectF(self.HEADER_W, y, w, self.ROW_H), QColor(base + r // 40, base + g // 40, base + b // 40))
        # beyond pattern end
        x_end = self.x_of(steps)
        if x_end < w:
            p.fillRect(QRectF(x_end, self.RULER_H, w - x_end, h), QColor(0, 0, 0, 170))

        # grid lines
        first = max(0, int(self.step_at(self.HEADER_W)))
        last = min(steps, int(self.step_at(w)) + 1)
        grid_bottom = self.RULER_H + len(lanes) * self.ROW_H
        p.setFont(QFont("Sans", 8))
        for s in range(first, last + 1):
            x = self.x_of(s)
            if s % STEPS_PER_BAR == 0:
                p.setPen(QPen(QColor(120, 120, 120), 1))
            elif s % STEPS_PER_BEAT == 0:
                p.setPen(QPen(QColor(60, 60, 60), 1))
            elif self.px >= 9:
                p.setPen(QPen(QColor(34, 34, 34), 1))
            else:
                continue
            p.drawLine(QPointF(x, self.RULER_H), QPointF(x, grid_bottom))
            if s % STEPS_PER_BEAT == 0 and s < steps:
                bar, beat = divmod(s // STEPS_PER_BEAT, 4)
                p.setPen(QColor(200, 200, 200) if beat == 0 else QColor(110, 110, 110))
                if beat == 0 or self.px * STEPS_PER_BEAT > 26:
                    p.drawText(QPointF(x + 3, 14), f"{bar + 1}" if beat == 0 else f"{bar + 1}.{beat + 1}")
        # lane separators between blocks; a stronger line above every folder
        prev = None
        for i, (_, _, kind) in enumerate(lanes):
            y = self.RULER_H + i * self.ROW_H
            if kind == "folder" and i:
                p.setPen(QPen(QColor(150, 150, 150), 1))
                p.drawLine(QPointF(0, y), QPointF(w, y))
            elif prev and kind != prev and prev != "folder":
                p.setPen(QPen(QColor(70, 70, 70), 1))
                p.drawLine(QPointF(self.HEADER_W, y), QPointF(w, y))
            prev = kind

        # notes
        if pat:
            keys = [k for k, _, _ in lanes]
            kinds = {k: kind for k, _, kind in lanes}
            # collapsed folders show a faint summary of the notes inside them
            for i, (key, _, kind) in enumerate(lanes):
                if kind != "folder" or key[5:] not in self.collapsed:
                    continue
                prefix = f"e:{key[5:]}|" if key != "fold:global" else "fx:"
                for n in pat.notes:
                    if n.lane.startswith(prefix):
                        rect = self.note_rect(n, i)
                        p.fillRect(rect.adjusted(0, 4, 0, -4), QColor(255, 200, 140, int(40 + 60 * n.velocity)))
            for n in pat.notes:
                if n.lane not in keys:
                    continue
                rect = self.note_rect(n, keys.index(n.lane))
                r, g, b = LANE_COLORS[kinds[n.lane]]
                a = int(90 + 165 * max(0.0, min(1.0, n.velocity)))
                p.fillRect(rect, QColor(r, g, b, a))
                vh = (rect.height() - 2) * n.velocity
                p.fillRect(QRectF(rect.left() + 1, rect.bottom() - vh - 1, 3, vh), QColor(255, 255, 255, 160))
                if n in self.selected:
                    p.setPen(QPen(QColor(255, 255, 255), 2))
                    p.drawRect(rect)
                else:
                    p.setPen(QPen(QColor(0, 0, 0, 120), 1))
                    p.drawRect(rect)

        # bar that was double-clicked on the master track
        if self.highlight_bar is not None and self.highlight_bar * STEPS_PER_BAR < steps:
            hx0 = self.x_of(self.highlight_bar * STEPS_PER_BAR)
            hx1 = self.x_of(min(steps, (self.highlight_bar + 1) * STEPS_PER_BAR))
            p.fillRect(QRectF(hx0, self.RULER_H, hx1 - hx0, grid_bottom - self.RULER_H), QColor(255, 160, 40, 28))
            p.fillRect(QRectF(hx0, 0, hx1 - hx0, 3), QColor(255, 160, 40))

        # rubber band
        if self._drag and self._drag["mode"] == "rubber":
            p.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine))
            p.drawRect(self._drag["rect"])

        # cursor + playhead
        xc = self.x_of(self.cursor_step)
        p.setPen(QPen(QColor(255, 160, 40, 120), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(xc, self.RULER_H), QPointF(xc, grid_bottom))
        if self.playhead_step is not None:
            xp = self.x_of(self.playhead_step)
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(xp, 0), QPointF(xp, grid_bottom))

        # header (fixed)
        p.fillRect(QRectF(0, 0, self.HEADER_W, h), QColor(20, 20, 20))
        p.fillRect(QRectF(0, 0, w, self.RULER_H), QColor(0, 0, 0, 0))
        p.setFont(QFont("Sans", 9))
        bold = QFont("Sans", 9, QFont.Weight.Bold)
        normal = QFont("Sans", 9)
        for i, (key, label, kind) in enumerate(lanes):
            y = self.RULER_H + i * self.ROW_H
            if kind == "folder":
                p.fillRect(QRectF(0, y, self.HEADER_W, self.ROW_H), QColor(40, 40, 46))
                p.setFont(bold)
                p.setPen(QColor(255, 210, 160) if key != "fold:global" else QColor(170, 200, 255))
                arrow = "▸ " if key[5:] in self.collapsed else "▾ "
                p.drawText(QRectF(6, y, self.HEADER_W - 8, self.ROW_H), Qt.AlignmentFlag.AlignVCenter, arrow + label)
                continue
            p.setFont(normal)
            p.fillRect(QRectF(14, y + 1, 3, self.ROW_H - 2), QColor(*LANE_COLORS[kind]))
            p.setPen(QColor(190, 170, 230) if kind in ("fx", "global") else QColor(220, 220, 220))
            prefix = {"group": "◆ ", "logo": "★ ", "all": "● "}.get(kind, "")
            p.drawText(QRectF(22, y, self.HEADER_W - 24, self.ROW_H), Qt.AlignmentFlag.AlignVCenter, prefix + label)
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawLine(QPointF(self.HEADER_W, 0), QPointF(self.HEADER_W, h))
        if not pat:
            p.setPen(QColor(120, 120, 120))
            p.drawText(QRectF(self.HEADER_W, 0, w - self.HEADER_W, h), Qt.AlignmentFlag.AlignCenter, "Create a pattern on the left")
        p.end()

    # ---- editing helpers ------------------------------------------------------------------------
    def _snapped(self, stepf: float) -> int:
        return int(math.floor(stepf / self.snap) * self.snap)

    def _commit(self, key=None):
        self.ctl.commit("pattern", key)
        self.selectionChanged.emit()
        self.update()

    def delete_selected(self):
        pat = self.pattern
        if pat and self.selected:
            pat.notes = [n for n in pat.notes if n not in self.selected]
            self.selected = []
            self._commit()

    def copy_selected(self):
        global _CLIPBOARD
        if self.selected:
            s0 = min(n.start for n in self.selected)
            _CLIPBOARD = [(n.lane, n.start - s0, n.length, n.velocity) for n in self.selected]

    def paste(self, at: int | None = None):
        pat = self.pattern
        if not (pat and _CLIPBOARD):
            return
        at = self.cursor_step if at is None else at
        keys = {k for k, _, _ in self.ctl.song.lanes()}
        new = []
        for lane, rel, ln, vel in _CLIPBOARD:
            s = at + rel
            if lane in keys and s < pat.steps:
                n = Note(lane, s, min(ln, pat.steps - s), vel)
                pat.notes.append(n)
                new.append(n)
        self.selected = new
        if new:
            self.cursor_step = min(pat.steps - 1, at + max(r + l for _, r, l, _ in _CLIPBOARD))
            self._commit()

    def duplicate_selected(self):
        if self.selected:
            end = max(n.start + n.length for n in self.selected)
            start = min(n.start for n in self.selected)
            span = end - start
            span = int(math.ceil(span / self.snap) * self.snap)
            self.copy_selected()
            self.paste(start + span)

    def set_selected_velocity(self, v: float):
        if self.selected:
            for n in self.selected:
                n.velocity = v
            self._commit("velocity")

    # ---- mouse ---------------------------------------------------------------------------------
    def mousePressEvent(self, ev):
        self.setFocus()
        pat = self.pattern
        pos = ev.position()
        if pos.y() < self.RULER_H:
            return
        lanes = self.lanes()
        li = self.lane_at(pos.y())
        if not (0 <= li < len(lanes)):
            return
        if lanes[li][2] == "folder":            # click a folder row to fold / unfold it
            if ev.button() == Qt.MouseButton.LeftButton:
                self.toggle_folder(lanes[li][0][5:])
            return
        if not pat or pos.x() < self.HEADER_W:
            return
        stepf = self.step_at(pos.x())
        note = self.note_at(pos)
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if ev.button() == Qt.MouseButton.RightButton:
            if note:
                pat.notes.remove(note)
                if note in self.selected:
                    self.selected.remove(note)
                self._commit()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return

        if note:
            keys = [k for k, _, _ in lanes]
            rect = self.note_rect(note, keys.index(note.lane))
            if shift:
                if note in self.selected:
                    self.selected.remove(note)
                else:
                    self.selected.append(note)
                self.selectionChanged.emit()
                self.update()
                return
            if note not in self.selected:
                self.selected = [note]
                self.selectionChanged.emit()
            if pos.x() > rect.right() - self.EDGE_PX:
                self._drag = dict(mode="resize", anchor=stepf, orig=[(n, n.length) for n in self.selected], changed=False)
            else:
                ek = self.edit_keys()
                self._drag = dict(mode="move", anchor=stepf, anchor_lane=self._edit_idx_at(pos.y()), changed=False,
                                  orig=[(n, n.start, ek.index(n.lane)) for n in self.selected if n.lane in ek])
            self.update()
            return

        if shift:
            self._drag = dict(mode="rubber", origin=pos, rect=QRectF(pos, pos))
            return
        s = self._snapped(stepf)
        if s >= pat.steps:
            return
        self.cursor_step = s
        n = Note(lanes[li][0], s, min(self.snap, pat.steps - s), self.new_velocity)
        pat.notes.append(n)
        self.selected = [n]
        self.selectionChanged.emit()
        self._drag = dict(mode="draw", note=n, start=s, changed=True)
        self.update()

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        pat = self.pattern
        if not self._drag:
            note = self.note_at(pos)
            cursor = Qt.CursorShape.ArrowCursor
            if note:
                keys = [k for k, _, _ in self.lanes()]
                rect = self.note_rect(note, keys.index(note.lane))
                cursor = Qt.CursorShape.SizeHorCursor if pos.x() > rect.right() - self.EDGE_PX else Qt.CursorShape.SizeAllCursor
            self.setCursor(cursor)
            return
        d = self._drag
        stepf = self.step_at(pos.x())
        if d["mode"] == "draw":
            n = d["note"]
            end = max(d["start"] + self.snap, int(math.ceil(stepf / self.snap) * self.snap))
            n.length = max(1, min(end, pat.steps) - n.start)
        elif d["mode"] == "resize":
            delta = int(round((stepf - d["anchor"]) / self.snap) * self.snap)
            for n, ln in d["orig"]:
                n.length = max(1, min(ln + delta, pat.steps - n.start))
            d["changed"] = True
        elif d["mode"] == "move":
            keys = self.edit_keys()
            dstep = int(round((stepf - d["anchor"]) / self.snap) * self.snap)
            dlane = self._edit_idx_at(pos.y()) - d["anchor_lane"]
            if d["orig"]:
                lo = min(s for _, s, _ in d["orig"])
                hi = max(s + n.length for n, s, _ in d["orig"])
                dstep = max(-lo, min(dstep, pat.steps - hi))
                lmin = min(l for _, _, l in d["orig"])
                lmax = max(l for _, _, l in d["orig"])
                dlane = max(-lmin, min(dlane, len(keys) - 1 - lmax))
            for n, s, l in d["orig"]:
                n.start = s + dstep
                n.lane = keys[l + dlane]
            d["changed"] = True
        elif d["mode"] == "rubber":
            d["rect"] = QRectF(d["origin"], pos).normalized()
            keys = [k for k, _, _ in self.lanes()]
            self.selected = [n for n in pat.notes if n.lane in keys and d["rect"].intersects(self.note_rect(n, keys.index(n.lane)))]
            self.selectionChanged.emit()
        self.update()

    def mouseReleaseEvent(self, _ev):
        d, self._drag = self._drag, None
        if d and d.get("changed"):
            self._commit()
        self.update()

    def mouseDoubleClickEvent(self, ev):
        note = self.note_at(ev.position())
        if note and self.pattern:
            self.pattern.notes.remove(note)
            self.selected = [n for n in self.selected if n is not note]
            self._commit()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y() or ev.angleDelta().x()
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor = self.step_at(ev.position().x())
            self.px = max(4.0, min(80.0, self.px * (1.15 if delta > 0 else 1 / 1.15)))
            self.scroll_x = max(0.0, anchor * self.px - (ev.position().x() - self.HEADER_W))
        elif ev.modifiers() & Qt.KeyboardModifier.ShiftModifier or ev.angleDelta().x():
            self.scroll_x = max(0.0, self.scroll_x - delta)
        else:
            ev.ignore()
            return
        self.viewChanged.emit()
        self.update()

    def keyPressEvent(self, ev):
        k, mods = ev.key(), ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        pat = self.pattern
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ctrl and k == Qt.Key.Key_A and pat:
            self.selected = list(pat.notes)
            self.selectionChanged.emit()
            self.update()
        elif ctrl and k == Qt.Key.Key_C:
            self.copy_selected()
        elif ctrl and k == Qt.Key.Key_X:
            self.copy_selected()
            self.delete_selected()
        elif ctrl and k == Qt.Key.Key_V:
            self.paste()
        elif ctrl and k == Qt.Key.Key_D:
            self.duplicate_selected()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.selected and pat:
            d = self.snap if k == Qt.Key.Key_Right else -self.snap
            if all(0 <= n.start + d and n.start + d + n.length <= pat.steps for n in self.selected):
                for n in self.selected:
                    n.start += d
                self._commit("nudge")
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.selected:
            keys = self.edit_keys()
            d = -1 if k == Qt.Key.Key_Up else 1
            idx = [keys.index(n.lane) for n in self.selected if n.lane in keys]
            if idx and 0 <= min(idx) + d and max(idx) + d < len(keys):
                for n in self.selected:
                    if n.lane in keys:
                        n.lane = keys[keys.index(n.lane) + d]
                self._commit("nudge")
        else:
            super().keyPressEvent(ev)


class PatternTab(QWidget):
    loopSectionChanged = pyqtSignal(bool)

    SNAPS = [("1/16", 1), ("1/8", 2), ("1/4 (beat)", 4), ("1/2", 8), ("1 bar", 16)]

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._updating = False

        left = QWidget()
        left.setFixedWidth(250)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.addWidget(QLabel("<b>Patterns</b>"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_rename)
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("New", self.new_pattern), ("Duplicate", self.duplicate_pattern), ("Delete", self.delete_pattern)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        form = QFormLayout()
        self.spin_bars = QSpinBox()
        self.spin_bars.setRange(1, 16)
        self.spin_bars.setSuffix(" bar(s)")
        self.spin_bars.valueChanged.connect(self._on_bars)
        form.addRow("Length", self.spin_bars)
        self.btn_color = ColorButton((255, 160, 40))
        self.btn_color.colorChanged.connect(self._on_color)
        form.addRow("Clip colour", self.btn_color)
        self.combo_snap = QComboBox()
        for label, _ in self.SNAPS:
            self.combo_snap.addItem(label)
        self.combo_snap.currentIndexChanged.connect(lambda i: setattr(self.grid, "snap", self.SNAPS[i][1]))
        form.addRow("Grid", self.combo_snap)
        self.spin_vel = QDoubleSpinBox()
        self.spin_vel.setRange(0.05, 1.0)
        self.spin_vel.setSingleStep(0.05)
        self.spin_vel.setValue(0.9)
        self.spin_vel.setToolTip("Brightness of new notes; also edits the selected notes")
        self.spin_vel.valueChanged.connect(self._on_velocity)
        form.addRow("Velocity", self.spin_vel)
        ll.addLayout(form)
        self.chk_loop = QCheckBox("Loop this section while editing")
        self.chk_loop.setChecked(True)
        self.chk_loop.setToolTip("Playback loops the master-track clip of this pattern (the one you opened, or the "
                                 "one under the playhead). A pattern not on the master track loops on its own.")
        self.chk_loop.toggled.connect(self.loopSectionChanged.emit)
        ll.addWidget(self.chk_loop)
        frow = QHBoxLayout()
        for text, c in (("Fold all", True), ("Unfold all", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, cc=c: self.grid.set_all_collapsed(cc))
            frow.addWidget(b)
        ll.addLayout(frow)
        btn_fit = QPushButton("Fit pattern to width")
        btn_fit.clicked.connect(lambda: self.grid.fit())
        ll.addWidget(btn_fit)
        ll.addWidget(hint("Drag on empty cells to draw · drag a note to move · drag its right edge to resize · "
                          "right-click / double-click deletes · Shift-drag selects · Ctrl+C / X / V / D · "
                          "arrows nudge · Ctrl+wheel zooms · Ctrl+Z undo · click a folder row to fold it"))

        self.grid = PatternGrid(ctl)
        self.grid.selectionChanged.connect(self._on_grid_selection)
        self.grid.viewChanged.connect(self._sync_hscroll)
        scroll = QScrollArea()
        scroll.setWidget(self.grid)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hbar = QScrollBar(Qt.Orientation.Horizontal)
        self.hbar.valueChanged.connect(self._on_hscroll)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(scroll, 1)
        rl.addWidget(self.hbar)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(left)
        lay.addWidget(right, 1)

        ctl.changed.connect(self._on_changed)
        ctl.currentPatternChanged.connect(self._on_current_changed)
        self.reload_list()

    # ---- list --------------------------------------------------------------------------
    def reload_list(self):
        self._updating = True
        self.list.clear()
        for p in self.ctl.song.patterns:
            it = QListWidgetItem(p.name)
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            it.setForeground(QColor(*p.color))
            self.list.addItem(it)
            if p.id == self.ctl.current_pattern_id:
                self.list.setCurrentItem(it)
        self._updating = False
        self._load_props()

    def _load_props(self):
        p = self.ctl.current_pattern
        self._updating = True
        self.spin_bars.setEnabled(p is not None)
        if p:
            self.spin_bars.setValue(p.bars)
            self.btn_color.set_rgb(p.color)
        self._updating = False
        self.grid.selected = []
        self.grid.highlight_bar = None
        self.grid.refresh_size()

    def _on_select(self, cur, _prev):
        if self._updating or cur is None:
            return
        self.ctl.set_current_pattern(cur.data(Qt.ItemDataRole.UserRole))

    def _on_current_changed(self, pid):
        self._updating = True
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == pid:
                self.list.setCurrentItem(it)
        self._updating = False
        self._load_props()
        self.grid.fit()

    def _on_rename(self, item):
        if self._updating:
            return
        p = self.ctl.song.pattern(item.data(Qt.ItemDataRole.UserRole))
        if p and item.text().strip() and item.text() != p.name:
            p.name = item.text().strip()
            self.ctl.commit("patterns")

    def new_pattern(self):
        song = self.ctl.song
        p = Pattern(new_id(), f"Pattern {len(song.patterns) + 1}", 1, PATTERN_COLORS[len(song.patterns) % len(PATTERN_COLORS)])
        song.patterns.append(p)
        self.ctl.current_pattern_id = p.id
        self.ctl.commit("patterns")
        self.ctl.currentPatternChanged.emit(p.id)

    def duplicate_pattern(self):
        src = self.ctl.current_pattern
        if not src:
            return
        p = copy.deepcopy(src)
        p.id, p.name = new_id(), src.name + " copy"
        self.ctl.song.patterns.insert(self.ctl.song.patterns.index(src) + 1, p)
        self.ctl.current_pattern_id = p.id
        self.ctl.commit("patterns")
        self.ctl.currentPatternChanged.emit(p.id)

    def delete_pattern(self):
        p = self.ctl.current_pattern
        if not p:
            return
        self.ctl.song.remove_pattern(p.id)
        self.ctl.current_pattern_id = self.ctl.song.patterns[0].id if self.ctl.song.patterns else None
        self.ctl.commit("patterns")
        self.ctl.currentPatternChanged.emit(self.ctl.current_pattern_id or "")

    def _on_bars(self, v):
        p = self.ctl.current_pattern
        if self._updating or not p or v == p.bars:
            return
        p.bars = v
        p.notes = [n for n in p.notes if n.start < p.steps]
        for n in p.notes:
            n.length = min(n.length, p.steps - n.start)
        self.ctl.commit("pattern", "bars")
        self.grid.fit()

    def _on_color(self, rgb):
        p = self.ctl.current_pattern
        if p:
            p.color = tuple(rgb)
            self.ctl.commit("patterns")

    def _on_velocity(self, v):
        self.grid.new_velocity = v
        if not self._updating:
            self.grid.set_selected_velocity(v)

    def _on_grid_selection(self):
        if self.grid.selected:
            self._updating = True
            self.spin_vel.setValue(self.grid.selected[0].velocity)
            self._updating = False

    # ---- horizontal scroll ------------------------------------------------------------------
    def _sync_hscroll(self):
        visible = max(1, self.grid.width() - self.grid.HEADER_W)
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, max(0, int(self.grid.content_width() - visible + 20)))
        self.hbar.setPageStep(visible)
        self.hbar.setValue(int(self.grid.scroll_x))
        self.hbar.blockSignals(False)

    def _on_hscroll(self, v):
        self.grid.scroll_x = float(v)
        self.grid.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_hscroll()

    def _on_changed(self, kind):
        if kind in ("all", "patterns", "band"):
            self.reload_list()
        elif kind == "elements":
            self.grid.refresh_size()
        elif kind == "pattern":
            self.grid.update()
        if kind == "all":
            self.grid.selected = []
            self.grid.update()
