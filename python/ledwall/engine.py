"""Motion engine: (song, audio analysis, time) -> FrameState.

Pipeline per frame:
  1. find master-track clips under the playhead (or the auditioned pattern)
  2. evaluate every note of their patterns -> per-lane envelope level / gate / onset peak
  3. per element: resolve its lanes onto its members = parts (All, members, groups, Pulse, Chase,
     Random, Strobe) and work out how much it is in the scene; then the global lanes (flash, blackout ...)
  4. modulate with audio energy, add lyrics, optionally blend the end of a loop into its start
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from .audio import AudioAnalysis
from .model import BEATS_PER_BAR, LYRIC_DEFAULT_RATE, STEPS_PER_BEAT, Song, chase_parts, el_lane, part_owners, split_lane


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
    lyric_words: int = -1          # words of the lyric shown so far (-1 = all of them)
    lyric_flash: float = 0.0       # white hit on the lyric (its Flash lane)
    elements: dict = field(default_factory=dict)   # element id -> (levels, flash per part, presence)


_SCALARS = ("env", "beat_pulse", "bloom", "sparkle", "blackout", "logo_level", "logo_peak", "lyric_alpha", "lyric_flash")


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
        out.lyric, out.step, out.lyric_words = b.lyric, b.step, b.lyric_words
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
        self.preview_highlight: tuple | None = None  # Band tab: (element id, member indices) lit alone

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
                yield -1e9, 1e9, p, 0.0, ()
            return
        tail = 8.0  # beats of release tail after a clip ends
        muted = song.muted_tracks
        for c in song.clips:
            if c.track in muted:
                continue
            cs, ce = c.start, c.start + c.length
            if window:
                cs, ce = max(cs, window[0]), min(ce, window[1])
                if cs >= ce:
                    continue
            if cs <= b < ce + tail:
                p = song.pattern(c.pattern_id)
                if p and p.notes:
                    yield cs, ce, p, c.start, c.muted

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
        for cs, ce, pat, origin, muted in self._active_sources(b, window):
            plen = pat.beats
            local = b - origin
            for n in pat.notes:
                if n.lane.startswith("e:"):
                    eid = split_lane(n.lane)[0]
                    if (pat.elements is not None and eid not in pat.elements) or eid in muted:
                        continue    # the pattern doesn't use that element, or this clip switched it off
                elif muted and "global" in muted:
                    continue        # this clip switched the global effects off
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
                    eid = n.lane[2:].split("|", 1)[0] if n.lane.startswith("e:") else ""
                    if v is None:
                        out[n.lane] = [lv, gate, peak, pat.speed(eid), n, b - os_]
                    else:
                        if gate > v[1]:     # the louder note sets speed / direction / seed / start time
                            v[3], v[4], v[5] = pat.speed(eid), n, b - os_
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

        b_rel = b - loop[0] if loop else b

        def fx_clock(sub_key):
            """(beat pulse, 1/16 step) of an effect lane at its pattern's speed. The step counts from
            the note's own start, so a Chase begins on its starting member wherever the note sits."""
            v = lanes.get(sub_key)
            sp = v[3] if v else 1.0
            st = math.floor(v[5] * STEPS_PER_BEAT * sp + 1e-6) if v else step
            if sp == 1.0:
                return beat_pulse, st
            return math.exp(-((b * sp) % 1.0) * 6.0), st

        fpk = lane("fx:flash", 2)
        bloom = 1.2 * fpk if fpk > 0.002 else 0.0
        elements = {}
        all_lv = np.zeros(n_mem, np.float32)
        all_fl = np.zeros(n_mem, np.float32)
        hl = self.preview_highlight
        if hl is not None:            # Band tab: just the element, its chosen members lit
            in_scene = [e for e in song.elements if e.id == hl[0]]
        elif self.preview_pulse:
            pe = self.preview_elements
            in_scene = [e for e in song.elements if pe is None or e.id in pe]
        else:   # the elements used by the patterns sounding now (incl. release tails)
            used: set = set()
            probes = (b, b + (loop[1] - loop[0])) if loop else (b,)     # loops wrap release tails around
            for bb in probes:
                for _cs, _ce, pat, _o, muted in self._active_sources(bb, loop):
                    used |= {e.id for e in song.pattern_elements(pat) if e.id not in muted}
            in_scene = [e for e in song.elements if e.id in used]
        for el in in_scene:
            def L(sub, idx=0, _e=el.id):
                return lane(el_lane(_e, sub), idx)
            n = el.part_count()          # one level per member (= part / segment) of the element
            levels = np.zeros(n, np.float32)
            flash = np.zeros(n, np.float32)
            pulse = chase = rnd = strobe = 0.0
            if hl is not None:
                for i in hl[1]:
                    if 0 <= i < n:
                        levels[i] = 1.0
                presence = 1.0
            elif self.preview_pulse:
                pulse, presence = 1.0, 1.0
            else:
                for i in range(n):
                    levels[i] = L(f"p:{i}")
                for g in el.groups:
                    gv = L(f"g:{g['id']}")
                    idx = [i for i in g.get("parts", []) if 0 <= i < n]
                    if gv > 0 and idx:
                        levels[idx] = np.maximum(levels[idx], gv)
                if n:
                    levels = np.maximum(levels, L("all"))
                pulse, chase, rnd, strobe = L("pulse", 1), L("chase", 1), L("random", 1), L("strobe", 1)
                presence = max(float(levels.max()) if n else 0.0, pulse, chase, rnd, strobe)
            if pulse > 0 and n:
                bp, _ = fx_clock(el_lane(el.id, "pulse"))
                levels = np.maximum(levels, pulse * (0.2 + 0.8 * bp))
                bloom += 0.35 * pulse * bp / max(1, len(song.elements))
            if chase > 0 and n:
                key = el_lane(el.id, "chase")
                _, cs = fx_clock(key)
                nt = lanes[key][4] if key in lanes else None
                d, st0 = (nt.direction, nt.start_part) if nt else ("right", -1)
                if cs > 0:
                    for j in chase_parts(cs - 1, n, d, st0):     # a fading trail one step behind
                        levels[j] = max(levels[j], chase * 0.35)
                for i in chase_parts(cs, n, d, st0):
                    levels[i] = max(levels[i], chase)
            if rnd > 0:
                key = el_lane(el.id, "random")
                _, rs = fx_clock(key)
                sd = 0x5EED + 7919 * (int(lanes[key][4].seed if key in lanes else 1) - 1)   # seed 1 = the original
                for i in range(n):
                    if _hash01(rs, i, sd) < 0.45:
                        levels[i] = max(levels[i], rnd * (0.55 + 0.45 * _hash01(rs, i + 101, sd)))
            # audio reactivity on whatever is lit: low parts on the left, highs on the right
            if n and lk.audio_react > 0 and hl is None:
                pb = np.interp(np.linspace(0, len(bands_s) - 1, n), np.arange(len(bands_s)), bands_s)
                levels = levels * ((1.0 - lk.audio_react) + lk.audio_react * (0.25 + 0.75 * pb)) * (1 + 0.25 * lk.audio_react)
            if strobe > 0 and n:
                sv = lanes.get(el_lane(el.id, "strobe"))
                on = ((b * max(1, lk.strobe_rate) * (sv[3] if sv else 1.0)) % 1.0) < 0.45
                if on:
                    levels = np.maximum(levels, strobe)
                    flash = np.maximum(flash, strobe)
                    bloom += 0.5 * strobe
                elif max(float(levels.max()), pulse, chase, rnd) < 0.01:
                    presence = 0.0          # strobe alone: dark between flashes
            if fpk > 0.002 and n and presence > 0.001 and hl is None:   # the flash hits what is in the scene
                levels = np.maximum(levels, fpk)
                flash = np.maximum(flash, 0.85 * fpk)
            levels = np.clip(levels, 0, 1.5).astype(np.float32)
            flash = np.clip(flash, 0, 1).astype(np.float32)
            if el.type == "haze":       # no parts: its amount is the lane level
                amount = 1.0 if (self.preview_pulse or hl is not None) else L("all")
                elements[el.id] = (levels, flash, min(1.0, amount))
                continue
            elements[el.id] = (levels, flash, min(1.0, presence))
            if n_mem and n:   # what each person on stage "has" lit (haze, stage view)
                for i, owners in enumerate(part_owners(n, n_mem)):
                    for o in owners:
                        all_lv[o] = max(all_lv[o], levels[i])
                        all_fl[o] = max(all_fl[o], flash[i])
        levels, flash = all_lv, all_fl

        if hl is not None:
            logo_level, logo_peak = 0.0, 0.0
        elif self.preview_pulse:
            logo_level, logo_peak = (1.0 if self.preview_logo else 0.0), 0.0
        else:
            logo_level = 1.0 if lk.logo_always else max(lk.logo_idle, lane("fx:logo", 0))
            # Band Name Light: a glow held while the note lasts plus a flash at each note start
            logo_peak = lane("fx:logo_glow", 0) * lk.logo_glow + lane("fx:logo_glow", 2) * lk.logo_peak

        lyric, lyric_alpha, lyric_words, lyric_flash = "", 0.0, -1, 0.0
        for ly in song.lyrics:
            if loop and not (loop[0] <= ly.start < loop[1]):
                continue
            if ly.start <= b < ly.start + ly.length:
                fade = 0.2 / self.period
                lyric = ly.text
                lyric_alpha = min(1.0, (b - ly.start) / fade, (ly.start + ly.length - b) / fade)
                if ly.notes:
                    lv, lyric_words, lyric_flash = self.lyric_light(ly, b - ly.start)
                    lyric_alpha *= lv
                break

        return FrameState(
            elements=elements,
            t=t, beat=b, levels=levels, flash=flash,
            bands=bands, env=env_s, beat_pulse=beat_pulse, bloom=min(bloom, 2.0),
            sparkle=lane("fx:sparkle", 1), step=step, blackout=lane("fx:blackout", 1),
            logo_level=logo_level, logo_peak=logo_peak, lyric=lyric, lyric_alpha=max(0.0, lyric_alpha),
            lyric_words=lyric_words, lyric_flash=lyric_flash,
        )

    def lyric_light(self, ly, lb: float) -> tuple:
        """(level 0..1, words shown (-1 = all), white flash 0..1) of a lyric clip with notes, `lb`
        beats after its start. Light / Pulse / Strobe / Flash notes light it (between them it glows
        at `idle`); with only Word-by-word notes the text is simply on."""
        period = self.period
        end = ly.length
        level, flash, words, n_word, lit = 0.0, 0.0, 0, 0, False
        for n in ly.notes:
            sub = n.lane[3:] if n.lane.startswith("ly:") else n.lane
            os_ = n.start / STEPS_PER_BEAT
            if os_ >= end:
                continue
            oe = min(os_ + n.length / STEPS_PER_BEAT, end)
            if sub == "words":
                n_word += 1
                words += lb >= os_
                continue
            lit = True
            if lb < os_:
                continue
            age = (lb - os_) * period            # seconds since the note started
            on = lb < oe
            rate = n.rate or LYRIC_DEFAULT_RATE.get(sub, 1.0)
            ph = ((lb - os_) * rate) % 1.0
            if sub == "light":
                v = n.velocity * min(1.0, age / 0.03) if on else n.velocity * math.exp(-(lb - oe) * period / 0.15)
            elif sub == "pulse":
                v = n.velocity * (0.2 + 0.8 * math.exp(-ph * 5.0)) if on else 0.0
            elif sub == "strobe":
                v = n.velocity if on and ph < 0.45 else 0.0
            elif sub == "flash":
                f = n.velocity * math.exp(-age / 0.12)
                flash = max(flash, f)
                v = f
            else:
                v = 0.0
            level = max(level, v)
        level = max(level, ly.idle) if lit else 1.0
        return min(1.0, level), (words if n_word else -1), min(1.0, flash)

    def active_clip_at(self, b: float, track: int | None = None):
        for c in self.song.clips:
            if c.start <= b < c.start + c.length and (track is None or c.track == track):
                return c
        return None


def bar_of(b: float) -> tuple:
    bar = math.floor(b / BEATS_PER_BAR)
    return bar + 1, int(b - bar * BEATS_PER_BAR) + 1
