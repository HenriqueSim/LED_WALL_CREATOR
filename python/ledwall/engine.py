"""Motion engine: (song, audio analysis, time) -> FrameState.

Pipeline per frame:
  1. find master-track clips under the playhead (or the auditioned pattern)
  2. evaluate every note of their patterns -> per-lane envelope level / gate / onset peak
  3. per element: resolve its lanes onto members (All, members, groups, Pulse, Chase, Random,
     Strobe) and work out how much it is in the scene; then the global lanes (flash, blackout ...)
  4. modulate with audio energy, add lyrics, optionally blend the end of a loop into its start
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from .audio import AudioAnalysis
from .model import BEATS_PER_BAR, STEPS_PER_BEAT, Song, el_lane, split_lane


@dataclass
class FrameState:
    t: float = 0.0
    beat: float = 0.0
    levels: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))   # per member 0..1
    flash: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))    # per member accent mix
    bands: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))    # per member audio energy
    env: float = 0.0
    beat_pulse: float = 0.0
    bloom: float = 0.0
    sparkle: float = 0.0
    step: int = 0
    blackout: float = 0.0
    logo_level: float = 0.0
    logo_peak: float = 0.0
    lyric: str = ""
    lyric_alpha: float = 0.0
    elements: dict = field(default_factory=dict)   # element id -> (levels, flash, presence)


_SCALARS = ("env", "beat_pulse", "bloom", "sparkle", "blackout", "logo_level", "logo_peak", "lyric_alpha")


def blend_states(a: FrameState, b: FrameState, w: float) -> FrameState:
    out = copy.copy(a)
    for f in _SCALARS:
        setattr(out, f, (1 - w) * getattr(a, f) + w * getattr(b, f))
    for f in ("levels", "flash", "bands"):
        setattr(out, f, (1 - w) * getattr(a, f) + w * getattr(b, f))
    out.elements = {}
    for k in set(a.elements) | set(b.elements):
        la, fa, pa = a.elements.get(k, (0.0, 0.0, 0.0))
        lb, fb, pb = b.elements.get(k, (0.0, 0.0, 0.0))
        out.elements[k] = ((1 - w) * la + w * lb, (1 - w) * fa + w * fb, (1 - w) * pa + w * pb)
    if w > 0.5:
        out.lyric, out.step = b.lyric, b.step
    return out


def _hash01(a: int, b: int, seed: int = 0x5EED) -> float:
    x = (a * 73856093 ^ b * 19349663 ^ seed) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFF) / 65535.0


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class MotionEngine:
    DEMO_DURATION = 96.0

    def __init__(self, song: Song, analysis: AudioAnalysis | None = None):
        self.song = song
        self.analysis = analysis
        self.audition_pattern: str | None = None   # pattern id looped instead of the master track
        self.preview_pulse = False                  # Elements tab: every element in the scene, pulsing
        self.preview_elements: set | None = None    # ... these elements (the one being edited)
        self.preview_logo = True                    # ... and the band name

    # ---- time helpers ---------------------------------------------------------
    @property
    def period(self) -> float:
        return 60.0 / max(self.song.bpm, 1.0)

    def t2b(self, t: float) -> float:
        return (t - self.song.beat_offset) / self.period

    def b2t(self, b: float) -> float:
        return self.song.beat_offset + b * self.period

    @property
    def duration(self) -> float:
        if self.analysis:
            return self.analysis.duration
        return max(self.DEMO_DURATION, self.b2t(self.song.song_end_beats() + 8))

    def export_range(self) -> tuple:
        r = self.song.region
        if r and r[1] - r[0] > 0.05:
            return tuple(r)
        return 0.0, self.duration

    # ---- lane evaluation ---------------------------------------------------------
    def _env_params(self, lane: str):
        """(attack s, release s, peak decay s) per lane type."""
        lk = self.song.look
        if lane == "fx:logo":
            return lk.logo_attack_ms / 1000, lk.logo_release_ms / 1000, 0.0
        if lane == "fx:logo_glow":
            return lk.logo_glow_attack_ms / 1000, lk.logo_glow_release_ms / 1000, lk.logo_peak_decay_ms / 1000
        if lane == "fx:flash":
            return 0.0, 0.25, 0.35
        if lane.endswith(("pulse", "chase", "random", "sparkle", "strobe", "blackout")):
            return 0.0, 0.0, 0.3
        return lk.attack_ms / 1000, lk.release_ms / 1000, 0.3

    def _active_sources(self, b: float, window=None):
        """Yield (start, end, pattern, origin) for clips whose notes may sound at beat b.
        `window` (beats) clips every clip to the loop region."""
        song = self.song
        if self.audition_pattern:
            p = song.pattern(self.audition_pattern)
            if p:
                yield -1e9, 1e9, p, 0.0
            return
        tail = 8.0  # beats of release tail after a clip ends
        for c in song.clips:
            cs, ce = c.start, c.start + c.length
            if window:
                cs, ce = max(cs, window[0]), min(ce, window[1])
                if cs >= ce:
                    continue
            if cs <= b < ce + tail:
                p = song.pattern(c.pattern_id)
                if p and p.notes:
                    yield cs, ce, p, c.start

    def lane_values(self, b: float, loop=None) -> dict:
        """lane -> [level, gate, peak]. With `loop` (start, end beats) the region is treated as
        cyclic: only its own clips sound, and release tails of the previous pass wrap around."""
        out: dict = {}
        if loop:
            d = loop[1] - loop[0]
            self._accumulate(out, b, loop)
            self._accumulate(out, b + d, loop)
        else:
            self._accumulate(out, b, None)
        return out

    def _accumulate(self, out: dict, b: float, window):
        period = self.period
        for cs, ce, pat, origin in self._active_sources(b, window):
            plen = pat.beats
            local = b - origin
            for n in pat.notes:
                if n.lane.startswith("e:") and pat.elements is not None and split_lane(n.lane)[0] not in pat.elements:
                    continue        # the pattern doesn't use that element (its notes are kept, silent)
                atk, rel, pdec = self._env_params(n.lane)
                sb, lb = n.start / STEPS_PER_BEAT, n.length / STEPS_PER_BEAT
                kmax = math.ceil((ce - origin - sb) / plen) - 1          # last occurrence starting inside
                k0 = min(math.floor((local - sb) / plen), kmax)
                for k in (k0, k0 - 1):
                    if k < 0 and origin + k * plen + sb + lb <= cs:
                        continue
                    os_ = origin + k * plen + sb
                    if os_ + lb <= cs or os_ >= ce or b < max(os_, cs):
                        continue
                    oe = min(os_ + lb, ce)
                    age = (b - os_) * period
                    if b < oe:
                        # a note already running when the window starts is fully on (no re-attack)
                        lv = n.velocity * (min(1.0, age / atk) if atk > 0 and os_ >= cs else 1.0)
                        gate = n.velocity
                    else:
                        r = (b - oe) * period
                        if rel <= 0 or r > rel * 6:
                            continue
                        lv = n.velocity * math.exp(-r / rel)
                        gate = 0.0
                    peak = n.velocity * math.exp(-age / pdec) if pdec > 0 and os_ >= cs else 0.0
                    v = out.get(n.lane)
                    if v is None:
                        out[n.lane] = [lv, gate, peak]
                    else:
                        v[0], v[1], v[2] = max(v[0], lv), max(v[1], gate), max(v[2], peak)

    # ---- frame state -------------------------------------------------------------
    def state_at(self, t: float) -> FrameState:
        song = self.song
        if song.loop and not self.audition_pattern:
            s, e = self.export_range()
            d, u = e - s, t - s
            if d > 0.2 and 0.0 <= u < d:
                loop = (self.t2b(s), self.t2b(e))
                st = self._raw_state(t, loop)
                # light programming is cyclic by construction; only the audio-derived values need
                # a crossfade so the last frame matches frame 0
                fade = min(max(self.period * 2, 0.5), d * 0.25)
                w = _smoothstep((u - (d - fade)) / fade)
                if w > 0:
                    a0 = self._raw_state(t - d, loop, lanes_only=False)
                    st.env = (1 - w) * st.env + w * a0.env
                    st.bands = (1 - w) * st.bands + w * a0.bands
                return st
        return self._raw_state(t)

    def _raw_state(self, t: float, loop=None, lanes_only=False) -> FrameState:
        song, lk = self.song, self.song.look
        n_mem = len(song.members)
        b = self.t2b(t)
        phase = b % 1.0
        beat_pulse = math.exp(-phase * 6.0)
        step = math.floor((b - loop[0] if loop else b) * STEPS_PER_BEAT)

        # audio
        if self.analysis is not None:
            a = self.analysis
            env = float(np.interp(t, a.feat_times, a.env))
            raw_bands = np.array([np.interp(t, a.feat_times, x) for x in a.bands], np.float32)
        else:
            env = 0.3 + 0.5 * beat_pulse
            raw_bands = np.clip(env * (0.6 + 0.4 * np.sin(np.arange(6) * 1.3 + t * 2.0)), 0, 1).astype(np.float32)
        thr = (1.0 - lk.sensitivity) * 0.8
        env_s = float(np.clip((env - thr) / (1.0 - thr + 1e-6), 0, 1))
        bands_s = np.clip((raw_bands - thr) / (1.0 - thr + 1e-6), 0, 1)
        if n_mem:
            bands = np.interp(np.linspace(0, len(bands_s) - 1, n_mem), np.arange(len(bands_s)), bands_s).astype(np.float32)
        else:
            bands = np.zeros(0, np.float32)

        lanes = self.lane_values(b, loop)

        def lane(key, idx=0):
            v = lanes.get(key)
            return v[idx] if v else 0.0

        midx = song.member_index()
        fpk = lane("fx:flash", 2)
        bloom = 1.2 * fpk if fpk > 0.002 else 0.0
        elements = {}
        all_lv = np.zeros(n_mem, np.float32)
        all_fl = np.zeros(n_mem, np.float32)
        if self.preview_pulse:
            pe = self.preview_elements
            in_scene = [e for e in song.elements if pe is None or e.id in pe]
        else:   # the elements used by the patterns sounding now (incl. release tails)
            used: set = set()
            probes = (b, b + (loop[1] - loop[0])) if loop else (b,)     # loops wrap release tails around
            for bb in probes:
                for _cs, _ce, pat, _o in self._active_sources(bb, loop):
                    used |= {e.id for e in song.pattern_elements(pat)}
            in_scene = [e for e in song.elements if e.id in used]
        for el in in_scene:
            def L(sub, idx=0, _e=el.id):
                return lane(el_lane(_e, sub), idx)
            levels = np.zeros(n_mem, np.float32)
            flash = np.zeros(n_mem, np.float32)
            if self.preview_pulse:
                pulse, chase, rnd, strobe = 1.0, 0.0, 0.0, 0.0
                presence = 1.0
            else:
                for m in song.members:
                    levels[midx[m.id]] = L(f"m:{m.id}")
                for g in song.groups:
                    gv = L(f"g:{g.id}")
                    if gv > 0:
                        for mid in g.members:
                            if mid in midx:
                                levels[midx[mid]] = max(levels[midx[mid]], gv)
                if n_mem:
                    levels = np.maximum(levels, L("all"))
                pulse, chase, rnd, strobe = L("pulse", 1), L("chase", 1), L("random", 1), L("strobe", 1)
                presence = max(float(levels.max()) if n_mem else 0.0, pulse, chase, rnd, strobe)
            if pulse > 0 and n_mem:
                levels = np.maximum(levels, pulse * (0.2 + 0.8 * beat_pulse))
                bloom += 0.35 * pulse * beat_pulse / max(1, len(song.elements))
            if chase > 0 and n_mem:
                i = step % n_mem
                levels[i] = max(levels[i], chase)
                j = (step - 1) % n_mem
                levels[j] = max(levels[j], chase * 0.35)
            if rnd > 0:
                for i in range(n_mem):
                    if _hash01(step, i) < 0.45:
                        levels[i] = max(levels[i], rnd * (0.55 + 0.45 * _hash01(step, i + 101)))
            # audio reactivity on whatever is lit
            if n_mem and lk.audio_react > 0:
                levels = levels * ((1.0 - lk.audio_react) + lk.audio_react * (0.25 + 0.75 * bands)) * (1 + 0.25 * lk.audio_react)
            if strobe > 0 and n_mem:
                on = ((b * max(1, lk.strobe_rate)) % 1.0) < 0.45
                if on:
                    levels = np.maximum(levels, strobe)
                    flash = np.maximum(flash, strobe)
                    bloom += 0.5 * strobe
                elif max(float(levels.max()), pulse, chase, rnd) < 0.01:
                    presence = 0.0          # strobe alone: dark between flashes
            if fpk > 0.002 and n_mem and presence > 0.001:   # the flash hits what is in the scene
                levels = np.maximum(levels, fpk)
                flash = np.maximum(flash, 0.85 * fpk)
            levels = np.clip(levels, 0, 1.5).astype(np.float32)
            flash = np.clip(flash, 0, 1).astype(np.float32)
            elements[el.id] = (levels, flash, min(1.0, presence))
            if n_mem:
                all_lv = np.maximum(all_lv, levels)
                all_fl = np.maximum(all_fl, flash)
        levels, flash = all_lv, all_fl

        if self.preview_pulse:
            logo_level, logo_peak = (1.0 if self.preview_logo else 0.0), 0.0
        else:
            logo_level = 1.0 if lk.logo_always else max(lk.logo_idle, lane("fx:logo", 0))
            # Band Name Light: a glow held while the note lasts plus a flash at each note start
            logo_peak = lane("fx:logo_glow", 0) * lk.logo_glow + lane("fx:logo_glow", 2) * lk.logo_peak

        lyric, lyric_alpha = "", 0.0
        for ly in song.lyrics:
            if loop and not (loop[0] <= ly.start < loop[1]):
                continue
            if ly.start <= b < ly.start + ly.length:
                fade = 0.2 / self.period
                lyric = ly.text
                lyric_alpha = min(1.0, (b - ly.start) / fade, (ly.start + ly.length - b) / fade)
                break

        return FrameState(
            elements=elements,
            t=t, beat=b, levels=levels, flash=flash,
            bands=bands, env=env_s, beat_pulse=beat_pulse, bloom=min(bloom, 2.0),
            sparkle=lane("fx:sparkle", 1), step=step, blackout=lane("fx:blackout", 1),
            logo_level=logo_level, logo_peak=logo_peak, lyric=lyric, lyric_alpha=max(0.0, lyric_alpha),
        )

    def active_clip_at(self, b: float, track: int | None = None):
        for c in self.song.clips:
            if c.start <= b < c.start + c.length and (track is None or c.track == track):
                return c
        return None


def bar_of(b: float) -> tuple:
    bar = math.floor(b / BEATS_PER_BAR)
    return bar + 1, int(b - bar * BEATS_PER_BAR) + 1
