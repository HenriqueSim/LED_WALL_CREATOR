"""Single owner of the song being edited: undo/redo snapshots + change notifications."""

from __future__ import annotations

import json
import time

from PyQt6.QtCore import QObject, pyqtSignal

from .engine import MotionEngine
from .model import Song, default_song


class SongController(QObject):
    # kinds: "all" (song replaced / undo), "band", "patterns", "pattern", "arrangement", "look", "audio"
    changed = pyqtSignal(str)
    currentPatternChanged = pyqtSignal(str)

    MAX_UNDO = 300

    def __init__(self):
        super().__init__()
        self.song: Song = default_song()
        self.analysis = None
        self.engine = MotionEngine(self.song)
        self.current_pattern_id = self.song.patterns[0].id if self.song.patterns else None
        self.project_path: str | None = None
        self.dirty = False
        self._undo = [self._snap()]
        self._redo: list = []
        self._last_key = None
        self._last_time = 0.0

    # ---- current pattern ----------------------------------------------------------
    @property
    def current_pattern(self):
        return self.song.pattern(self.current_pattern_id) if self.current_pattern_id else None

    def set_current_pattern(self, pid: str | None):
        if pid != self.current_pattern_id:
            self.current_pattern_id = pid
            self.currentPatternChanged.emit(pid or "")

    # ---- undo / redo ------------------------------------------------------------------
    def _snap(self) -> str:
        return json.dumps(self.song.to_dict())

    def commit(self, kind: str, key: str | None = None):
        """Call after every edit. Edits with the same `key` within 1.5 s merge into one undo step."""
        snap = self._snap()
        now = time.monotonic()
        if key and key == self._last_key and now - self._last_time < 1.5 and len(self._undo) > 1:
            self._undo[-1] = snap
        elif snap != self._undo[-1]:
            self._undo.append(snap)
            del self._undo[:-self.MAX_UNDO]
        self._redo.clear()
        self._last_key, self._last_time = key, now
        self.dirty = True
        self.changed.emit(kind)

    def can_undo(self) -> bool:
        return len(self._undo) > 1

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self):
        if len(self._undo) > 1:
            self._redo.append(self._undo.pop())
            self._restore(self._undo[-1])

    def redo(self):
        if self._redo:
            snap = self._redo.pop()
            self._undo.append(snap)
            self._restore(snap)

    def _restore(self, snap: str):
        self._last_key = None
        self.set_song(Song.from_dict(json.loads(snap)), reset_undo=False)
        self.dirty = True

    # ---- song replacement ------------------------------------------------------------
    def set_song(self, song: Song, reset_undo: bool = True):
        self.song = song
        self.engine.song = song
        if not song.pattern(self.current_pattern_id or ""):
            self.current_pattern_id = song.patterns[0].id if song.patterns else None
        if reset_undo:
            self._undo, self._redo = [self._snap()], []
            self.dirty = False
        self.changed.emit("all")
        self.currentPatternChanged.emit(self.current_pattern_id or "")

    def set_analysis(self, analysis):
        self.analysis = analysis
        self.engine.analysis = analysis
        self.changed.emit("audio")
