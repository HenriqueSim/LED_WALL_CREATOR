"""FrameState -> BGR uint8 frame.

Layers (back to front):
  1. the song's elements (polygon / ring / vertical blocks / light shafts / horizon / beam), each
     drawn crisp on its own layer with its own glow amount, then a two-radius Gaussian bloom at 1/4 res
  2. band name layer with its own envelope, peak and glow radius (composited after the bloom)
  3. lyrics in the reserved headroom band at the top
"""

from __future__ import annotations

import colorsys
import math

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

from .engine import FrameState
from .model import Song

SHIFT = 4
SCALE = 1 << SHIFT
AA = cv2.LINE_AA
LOGO_FONT = "Anton"


def _fx(pts) -> np.ndarray:
    return np.round(np.asarray(pts, np.float64) * SCALE).astype(np.int32)


def _bgr(rgb, k: float = 1.0) -> tuple:
    """RGB * k -> BGR; over-bright colours are scaled, not clipped, so hue survives."""
    r, g, b = rgb[0] * k, rgb[1] * k, rgb[2] * k
    m = max(r, g, b)
    if m > 255.0:
        f = 255.0 / m
        r, g, b = r * f, g * f, b * f
    return (float(b), float(g), float(r))


def gradient_colors(main_rgb) -> tuple:
    """(top, bottom) of the block gradient derived from the song colour:
    top = song colour lifted towards white, bottom = deeper and shifted towards red."""
    r, g, b = (c / 255.0 for c in main_rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    top = colorsys.hsv_to_rgb((h + 0.02) % 1.0, max(0.0, s * 0.78), min(1.0, v * 1.0 + 0.04))
    bottom = colorsys.hsv_to_rgb((h - 0.075) % 1.0, min(1.0, s * 0.85 + 0.1), v * 0.62)
    return tuple(c * 255 for c in top), tuple(c * 255 for c in bottom)


def _qimage_to_array(img: QImage, w: int, h: int) -> np.ndarray:
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()


class FrameRenderer:
    DS = 4

    def __init__(self, width: int, height: int):
        self.resize(width, height)

    def resize(self, width: int, height: int):
        self.w, self.h = int(width), int(height)
        self.s = self.h / 1080.0
        self.sw, self.sh = max(2, self.w // self.DS), max(2, self.h // self.DS)
        self.core = np.zeros((self.h, self.w, 3), np.uint8)
        self.layer = np.zeros((self.h, self.w, 3), np.uint8)
        self.mask = np.zeros((self.h, self.w), np.uint8)
        ys, xs = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.ambient = np.exp(-(((xs - self.sw / 2) / (self.sw * 0.55)) ** 2 + ((self.sh - ys) / (self.sh * 0.38)) ** 2))[..., None]
        self._grad_cache: dict = {}
        self._layout_cache: dict = {}
        self._logo_key = None
        self._lyric_cache: dict = {}
        self.bboxes: dict = {}          # element id -> (x0, y0, x1, y1) in pixels, for hit-testing in the GUI

    def th(self, px: float) -> int:
        return max(1, int(round(px * self.s)))

    # ---- helpers ----------------------------------------------------------------------
    @staticmethod
    def element_rgb(el, lk) -> tuple:
        return {"accent": lk.accent_rgb, "custom": el.custom_rgb}.get(el.color, lk.main_rgb)

    def _drive(self, el, st: FrameState, song: Song):
        """Per-part (one per member) brightness and flash for an element."""
        n = max(1, len(st.levels))
        lv = st.levels if len(st.levels) else np.zeros(1, np.float32)
        fl = st.flash if len(st.flash) else np.zeros(1, np.float32)
        d = el.driver
        if d == "members":
            levels, flash = lv, fl
        else:
            if d.startswith("m:"):
                idx = song.member_index().get(d[2:])
                v, f = (float(lv[idx]), float(fl[idx])) if idx is not None and idx < len(lv) else (0.0, 0.0)
            elif d.startswith("g:"):
                g = next((g for g in song.groups if g.id == d[2:]), None)
                mi = song.member_index()
                idx = [mi[m] for m in (g.members if g else []) if m in mi and mi[m] < len(lv)]
                v = float(max((lv[i] for i in idx), default=0.0))
                f = float(max((fl[i] for i in idx), default=0.0))
            elif d == "always":
                v, f = 1.0, float(fl.max())
            elif d == "audio":
                v, f = st.env, float(fl.max())
            else:
                v, f = 0.0, 0.0
            levels, flash = np.full(n, v, np.float32), np.full(n, f, np.float32)
        b = el.idle + (1.0 - el.idle) * np.minimum(levels, 1.2)
        return levels, b, flash

    def _part_colors(self, el, lk, flash) -> list:
        base = np.array(self.element_rgb(el, lk), np.float32)
        acc = np.array(lk.accent_rgb, np.float32)
        return [base * (1 - f) + acc * f for f in flash]

    def _grad_rows(self, rgb, y0: float, y1: float, width: int, height: int):
        """Cached vertical gradient image (rows y0..y1) derived from an element colour."""
        y0 = max(0, int(y0) // 4 * 4)
        y1 = min(height, int(math.ceil(y1 / 4.0)) * 4)
        key = (tuple(int(c) for c in rgb), y0, y1, width)
        img = self._grad_cache.get(key)
        if img is None:
            if len(self._grad_cache) > 32:
                self._grad_cache.clear()
            top, bottom = gradient_colors(rgb)
            t = np.linspace(0.0, 1.0, max(1, y1 - y0), dtype=np.float32)[:, None]
            col = np.array(top[::-1], np.float32) * (1 - t) + np.array(bottom[::-1], np.float32) * t
            img = np.ascontiguousarray(np.repeat(col[:, None, :], width, axis=1)).astype(np.uint8)
            self._grad_cache[key] = img
        return y0, y1, img

    def _apply_mask(self, layer, mask, rgb, y0, y1, gradient: bool):
        y0, y1, img = self._grad_rows(rgb, y0, y1, self.w, self.h)
        if y1 <= y0:
            return
        m = mask[y0:y1]
        m3 = cv2.merge([m, m, m])
        if gradient:
            res = cv2.multiply(img, m3, scale=1.0 / 255.0)
        else:
            res = cv2.multiply(m3, _bgr(rgb) + (0.0,), scale=1.0 / 255.0)
        layer[y0:y1] = cv2.max(layer[y0:y1], res)

    @staticmethod
    def _outline_rgb(rgb) -> tuple:
        top, _ = gradient_colors(rgb)
        return tuple(min(255.0, c * 0.55 + 255 * 0.45) for c in top)

    # ---- main entry ------------------------------------------------------------------
    def render(self, st: FrameState, song: Song) -> np.ndarray:
        lk = song.look
        core, layer = self.core, self.layer
        core.fill(0)
        glow_src = np.zeros((self.sh, self.sw, 3), np.float32)
        self.bboxes = {}
        drawers = {"polygon": self._draw_polygon, "ring": self._draw_ring, "bars": self._draw_bars,
                   "shafts": self._draw_shafts, "horizon": self._draw_horizon, "beam": self._draw_beam}
        any_visible = False
        for el in reversed(song.elements):          # list top = front layer
            fn = drawers.get(el.type)
            if not el.visible or fn is None:
                continue
            any_visible = True
            layer.fill(0)
            soft = fn(layer, el, st, song)
            cv2.max(core, layer, dst=core)
            g = el.glow * (0.45 if el.fill != "outline" and el.type in ("polygon", "ring") else 1.0)  # big fills bloom less
            if g > 0:
                glow_src += cv2.resize(layer, (self.sw, self.sh), interpolation=cv2.INTER_AREA).astype(np.float32) * g
            if soft is not None:
                glow_src += soft

        sigma = max(0.6, lk.glow_radius * self.s / self.DS * (1.0 + 0.6 * st.bloom))
        tight = cv2.GaussianBlur(glow_src, (0, 0), sigma * 0.35)
        wide = cv2.GaussianBlur(glow_src, (0, 0), sigma)
        gain = lk.glow_intensity * (1.0 + 0.7 * st.bloom)
        glow = tight * (1.1 * gain) + wide * (1.9 * gain)
        if any_visible:
            mean_lv = float(st.levels.mean()) if len(st.levels) else 0.0
            glow += self.ambient * np.array(_bgr(lk.main_rgb, 0.05 + 0.1 * mean_lv + 0.06 * st.env), np.float32)
        np.clip(glow, 0, 255, out=glow)
        out = cv2.add(core, cv2.resize(glow.astype(np.uint8), (self.w, self.h), interpolation=cv2.INTER_LINEAR))
        if st.blackout > 0.001:
            out = cv2.convertScaleAbs(out, alpha=max(0.0, 1.0 - st.blackout))
        if lk.logo_text.strip() and (st.logo_level > 0.003 or st.logo_peak > 0.003):
            self._overlay_logo(out, st, song)
        if st.lyric and st.lyric_alpha > 0.003:
            self._overlay_lyric(out, st, song)
        return out

    # ---- element: polygon (hexagon) ------------------------------------------------------
    def _draw_polygon(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        cols = self._part_colors(el, lk, flash)
        n = len(b)
        cx, cy = el.x * self.w, el.y * self.h
        R = el.size * self.h * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        sides = max(3, int(el.sides))
        ang = np.radians(el.rotation + el.spin * st.beat - 90.0 + np.arange(sides) * 360.0 / sides)
        verts = np.stack([cx + R * np.cos(ang), cy + R * np.sin(ang)], 1)
        mean_b, mean_col = float(np.mean(b)), np.mean(cols, axis=0)
        if el.fill in ("filled", "gradient"):
            self.mask.fill(0)
            cv2.fillPoly(self.mask, [_fx(verts)], int(255 * min(1.0, mean_b * (0.85 if el.fill == "gradient" else 0.5))), AA, SHIFT)
            self._apply_mask(layer, self.mask, mean_col, cy - R, cy + R + 1, el.fill == "gradient")
        t = self.th(el.thickness)
        for i in range(sides):          # one edge per member (wraps if sides != members)
            k = i % n
            cv2.line(layer, tuple(_fx(verts[i])), tuple(_fx(verts[(i + 1) % sides])), _bgr(cols[k], float(b[k])), t, AA, SHIFT)
        for i in range(sides):          # glowing joints
            k1, k2 = (i - 1) % sides % n, i % n
            bb = max(float(b[k1]), float(b[k2]))
            cv2.circle(layer, tuple(_fx(verts[i])), int(t * 0.9 * SCALE), _bgr(self._outline_rgb(cols[k2]), bb), -1, AA, SHIFT)
        self.bboxes[el.id] = (cx - R, cy - R, cx + R, cy + R)

    # ---- element: ring ---------------------------------------------------------------------------
    def _draw_ring(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        cols = self._part_colors(el, lk, flash)
        n = len(b)
        cx, cy = el.x * self.w, el.y * self.h
        R = el.size * self.h * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        if el.fill in ("filled", "gradient"):
            self.mask.fill(0)
            cv2.circle(self.mask, tuple(_fx((cx, cy))), int(R * SCALE), int(255 * min(1.0, float(np.mean(b)) * 0.7)), -1, AA, SHIFT)
            self._apply_mask(layer, self.mask, np.mean(cols, axis=0), cy - R, cy + R + 1, el.fill == "gradient")
        t = self.th(el.thickness)
        start = el.rotation + el.spin * st.beat - 90.0
        seg = 360.0 / n
        gap = 3.0 if n > 1 else 0.0
        for k in range(n):
            a0 = start + k * seg + gap / 2
            cv2.ellipse(layer, tuple(_fx((cx, cy))), (int(R * SCALE), int(R * SCALE)), 0, a0, a0 + seg - gap,
                        _bgr(cols[k], float(b[k])), t, AA, SHIFT)
        self.bboxes[el.id] = (cx - R, cy - R, cx + R, cy + R)

    # ---- element: vertical blocks ------------------------------------------------------------------
    def _bars_layout(self, el, n: int):
        x0 = (el.x - el.size / 2) * self.w
        width = el.size * self.w
        key = (el.id, n, el.slats, round(x0), round(width), self.w, self.h)
        lay = self._layout_cache.get(key)
        if lay is None:
            if len(self._layout_cache) > 16:
                self._layout_cache.clear()
            rng = np.random.default_rng(7)
            slats = max(1, int(el.slats))
            slot = width / max(n, 1)
            member_gap = slot * 0.09
            sw_ = (slot - member_gap) / (slats + (slats - 1) * 0.28)
            lay = []
            for i in range(n):
                xm = x0 + i * slot + member_gap / 2
                for j in range(slats):
                    xl = xm + j * sw_ * 1.28
                    ang = 0.0 if rng.random() < 0.3 else rng.uniform(-13, 13)
                    lay.append(dict(member=i, x0=xl, x1=xl + sw_, slope=math.tan(math.radians(ang)) * sw_,
                                    rest=rng.uniform(0.45, 0.62), hv=rng.uniform(0.8, 1.0), ph=rng.uniform(0, 6.28)))
            self._layout_cache[key] = lay
        return lay

    def _bar_limits(self, el, lk):
        top_max = max(lk.headroom + 0.04, 1.0 - el.height) * self.h
        return top_max, self.h

    def _draw_bars(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        n = len(b)
        rgb = self.element_rgb(el, lk)
        top_max, bottom = self._bar_limits(el, lk)
        span = bottom - top_max
        slats = self._bars_layout(el, n)
        mask = self.mask
        mask.fill(0)
        polys = []
        any_flash = bool(flash.max() > 0.01)
        fmask = np.zeros_like(mask) if any_flash else None
        bands = st.bands if len(st.bands) == n else np.zeros(n, np.float32)
        for sl in slats:
            k = sl["member"]
            lv = float(levels[k])
            rest = bottom - span * (1.0 - sl["rest"])
            y = rest - (rest - top_max) * min(lv, 1.0) * sl["hv"]
            y -= 0.025 * self.h * lk.audio_react * float(bands[k]) * (0.5 + 0.5 * math.sin(sl["ph"] + st.beat * 1.7))
            y = max(top_max, y)
            poly = _fx(np.array([(sl["x0"], y - sl["slope"] / 2), (sl["x1"], y + sl["slope"] / 2),
                                 (sl["x1"], bottom + 2), (sl["x0"], bottom + 2)]))
            if el.fill != "outline":
                cv2.fillPoly(mask, [poly], int(255 * min(1.0, float(b[k]) * 0.82)), AA, SHIFT)
            if any_flash and flash[k] > 0.01:
                cv2.fillPoly(fmask, [poly], int(255 * min(1.0, float(flash[k] * b[k]))), AA, SHIFT)
            polys.append((poly, float(b[k])))
        if el.fill != "outline":
            self._apply_mask(layer, mask, rgb, top_max - 0.05 * self.h, bottom, el.fill == "gradient")
        if any_flash:
            acc = cv2.multiply(cv2.merge([fmask] * 3), _bgr(lk.accent_rgb) + (0.0,), scale=1.0 / 255.0)
            cv2.max(layer, acc, dst=layer)
        orgb = self._outline_rgb(rgb)
        t = self.th(el.thickness)
        for poly, bb in polys:
            cv2.polylines(layer, [poly], True, _bgr(orgb, min(1.0, bb * 1.05)), t, AA, SHIFT)
        if st.sparkle > 0.01:
            self._sparkles(layer, st, lk, [(sl, top_max + span * 0.3) for sl in slats])
        self.bboxes[el.id] = ((el.x - el.size / 2) * self.w, top_max, (el.x + el.size / 2) * self.w, bottom)

    def _sparkles(self, layer, st, lk, tops):
        rng = np.random.default_rng(st.step * 7919 + 13)
        for _ in range(int(3 + 14 * st.sparkle)):
            sl, y = tops[int(rng.integers(0, len(tops)))]
            x = rng.uniform(sl["x0"], sl["x1"])
            yy = y + rng.uniform(0.0, 0.35) * (self.h - y)
            r = 0.006 * self.h * rng.uniform(0.6, 1.4)
            col = _bgr(lk.accent_rgb, st.sparkle * 1.2)
            cv2.circle(layer, tuple(_fx((x, yy))), int(r * SCALE), col, -1, AA, SHIFT)
            cv2.line(layer, tuple(_fx((x - 3 * r, yy))), tuple(_fx((x + 3 * r, yy))), col, self.th(1), AA, SHIFT)
            cv2.line(layer, tuple(_fx((x, yy - 3 * r))), tuple(_fx((x, yy + 3 * r))), col, self.th(1), AA, SHIFT)

    # ---- soft helpers ------------------------------------------------------------------------------
    def _soft_gradient(self, rgb, top_max, intensity_mask):
        """intensity_mask (sh, sw) uint8 -> soft BGR float layer, gradient + fade towards the top."""
        top, bottom = gradient_colors(rgb)
        yy = np.arange(self.sh, dtype=np.float32)[:, None] / self.sh
        t = np.clip((yy - top_max / self.h) / max(1e-3, 1.0 - top_max / self.h), 0, 1)
        col = (np.array(top[::-1], np.float32) * (1 - t)[..., None] + np.array(bottom[::-1], np.float32) * t[..., None])
        fade = (t ** 1.3)[..., None]
        return col * (intensity_mask[..., None].astype(np.float32) / 255.0) * fade

    # ---- element: light shafts ------------------------------------------------------------------
    def _draw_shafts(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        n = len(b)
        rgb = self.element_rgb(el, lk)
        x0 = (el.x - el.size / 2) * self.w
        slot = el.size * self.w / n
        top_max, bottom = self._bar_limits(el, lk)
        smask = np.zeros((self.sh, self.sw), np.uint8)
        k = 1.0 / self.DS
        orgb = self._outline_rgb(rgb)
        for i in range(n):
            lv = float(levels[i])
            cx = x0 + (i + 0.5) * slot
            y = bottom - (bottom - top_max) * (0.35 + 0.65 * min(lv, 1.0))
            bw, tw = slot * 0.05, slot * 0.2
            poly = np.array([(cx - bw, bottom), (cx + bw, bottom), (cx + tw, y), (cx - tw, y)]) * k
            cv2.fillPoly(smask, [_fx(poly)], int(255 * min(1.0, float(b[i]))), AA, SHIFT)
            cv2.line(layer, tuple(_fx((cx, bottom))), tuple(_fx((cx, y + 0.1 * (bottom - y)))), _bgr(orgb, float(b[i]) * 0.55),
                     self.th(max(1.0, el.thickness * 0.4)), AA, SHIFT)
        soft = cv2.GaussianBlur(self._soft_gradient(rgb, top_max, smask), (0, 0), 6 * self.s + 1)
        if flash.max() > 0.01:
            soft += cv2.merge([smask] * 3).astype(np.float32) / 255.0 * np.array(_bgr(lk.accent_rgb, float(flash.max()) * 0.6), np.float32)
        self.bboxes[el.id] = (x0, top_max, x0 + el.size * self.w, bottom)
        return soft * 0.9

    # ---- element: horizon ---------------------------------------------------------------------------
    def _draw_horizon(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        cols = self._part_colors(el, lk, flash)
        n = len(b)
        rgb = self.element_rgb(el, lk)
        top_rgb, bottom_rgb = gradient_colors(rgb)
        yh = el.y * self.h
        x0 = (el.x - el.size / 2) * self.w
        width = el.size * self.w
        slot = width / n
        top_max = max(lk.headroom + 0.04, el.y - el.height) * self.h
        cv2.line(layer, tuple(_fx((x0, yh))), tuple(_fx((x0 + width, yh))), _bgr(rgb, (el.idle + 0.5 * float(levels.mean())) * 0.7),
                 self.th(max(1.0, el.thickness * 0.4)), AA, SHIFT)
        soft = np.zeros((self.sh, self.sw, 3), np.float32)
        k = 1.0 / self.DS
        for i in range(n):
            lv, bb = float(levels[i]), float(b[i])
            cx = x0 + (i + 0.5) * slot
            half = slot * (0.12 + 0.22 * min(lv, 1.0))
            cv2.line(layer, tuple(_fx((cx - half, yh))), tuple(_fx((cx + half, yh))), _bgr(cols[i], bb * 1.2), self.th(el.thickness), AA, SHIFT)
            yt = yh - (yh - top_max) * min(lv, 1.0) * 0.9
            cone = np.array([(cx - half * 0.6, yh), (cx + half * 0.6, yh), (cx + half * 1.6, yt), (cx - half * 1.6, yt)]) * k
            cv2.fillPoly(soft, [_fx(cone)], _bgr(top_rgb, bb * 0.35), AA, SHIFT)
            refl = np.array([(cx - half * 0.5, yh), (cx + half * 0.5, yh), (cx + half * 0.2, self.h), (cx - half * 0.2, self.h)]) * k
            cv2.fillPoly(soft, [_fx(refl)], _bgr(bottom_rgb, bb * 0.5), AA, SHIFT)
        self.bboxes[el.id] = (x0, top_max, x0 + width, min(self.h, yh + 0.1 * self.h))
        return cv2.GaussianBlur(soft, (0, 0), 5 * self.s + 1)

    # ---- element: beam ------------------------------------------------------------------------------
    def _draw_beam(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song)
        cols = self._part_colors(el, lk, flash)
        n = len(b)
        cx, cy = el.x * self.w, el.y * self.h
        L = el.size * self.w * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        a = math.radians(el.rotation + el.spin * st.beat)
        d = np.array([math.cos(a), math.sin(a)])
        p0 = np.array([cx, cy]) - d * L / 2
        seg = L / n
        gap = min(seg * 0.08, 0.01 * self.w) if n > 1 else 0.0
        t = self.th(el.thickness)
        for k in range(n):
            s0 = p0 + d * (k * seg + gap / 2)
            s1 = p0 + d * ((k + 1) * seg - gap / 2)
            cv2.line(layer, tuple(_fx(s0)), tuple(_fx(s1)), _bgr(cols[k], float(b[k])), t, AA, SHIFT)
        ex = abs(d[0]) * L / 2 + t
        ey = abs(d[1]) * L / 2 + t
        self.bboxes[el.id] = (cx - ex, cy - ey, cx + ex, cy + ey)

    # ---- band name layer ----------------------------------------------------------------------
    def _logo_layers(self, lk):
        key = (lk.logo_text, lk.logo_stacked, round(lk.logo_size, 3), round(lk.logo_y, 3),
               round(lk.logo_glow_radius, 1), self.w, self.h)
        if key == self._logo_key:
            return self._logo
        self._logo_key, self._logo = key, None
        text = lk.logo_text.strip()
        words = text.split()
        img = QImage(self.w, self.h, QImage.Format.Format_Grayscale8)
        img.fill(0)
        font = QFont(LOGO_FONT)
        font.setPixelSize(100)
        xh100 = QFontMetricsF(font).xHeight() or 50.0
        stacked = lk.logo_stacked and len(words) >= 2
        target_h = lk.logo_size * self.h
        xh = target_h / (2.1 if stacked else 1.35)
        font.setPixelSize(max(6, int(round(100 * xh / xh100))))
        fm = QFontMetricsF(font)
        xh = fm.xHeight()
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        if stacked:
            # layout of the reference logo: "vespa." / "maia" offset right, tight leading,
            # descenders of the first line cut at the baseline
            l1, l2 = words[0] + ".", " ".join(words[1:])
            r1, r2 = fm.tightBoundingRect(l1), fm.tightBoundingRect(l2)
            adv1 = fm.horizontalAdvance(l1)
            dx2 = adv1 * 0.456
            dy2 = xh * 1.07
            left = min(r1.left(), dx2 + r2.left())
            right = max(r1.right(), dx2 + r2.right())
            top = r1.top()
            bottom = dy2 + r2.bottom()
            if right - left > self.w * 0.9:          # keep it on the wall
                f = self.w * 0.9 / (right - left)
                font.setPixelSize(max(6, int(font.pixelSize() * f)))
                painter.setFont(font)
                fm = QFontMetricsF(font)
                xh = fm.xHeight()
                r1, r2 = fm.tightBoundingRect(l1), fm.tightBoundingRect(l2)
                adv1 = fm.horizontalAdvance(l1)
                dx2, dy2 = adv1 * 0.456, xh * 1.07
                left, right = min(r1.left(), dx2 + r2.left()), max(r1.right(), dx2 + r2.right())
                top, bottom = r1.top(), dy2 + r2.bottom()
            ox = self.w / 2 - (left + right) / 2
            oy = lk.logo_y * self.h - (top + bottom) / 2
            painter.setClipRect(QRectF(0, 0, self.w, oy + 0.5))
            painter.drawText(QRectF(ox, oy - fm.ascent(), self.w * 2, fm.height()), Qt.AlignmentFlag.AlignLeft, l1)
            painter.setClipping(False)
            painter.drawText(QRectF(ox + dx2, oy + dy2 - fm.ascent(), self.w * 2, fm.height()), Qt.AlignmentFlag.AlignLeft, l2)
        else:
            r = fm.tightBoundingRect(text)
            if r.width() > self.w * 0.9:
                font.setPixelSize(max(6, int(font.pixelSize() * self.w * 0.9 / r.width())))
                painter.setFont(font)
                fm = QFontMetricsF(font)
                r = fm.tightBoundingRect(text)
            ox = self.w / 2 - (r.left() + r.right()) / 2
            oy = lk.logo_y * self.h - (r.top() + r.bottom()) / 2
            painter.drawText(QRectF(ox, oy - fm.ascent(), self.w * 2, fm.height()), Qt.AlignmentFlag.AlignLeft, text)
        painter.end()
        arr = _qimage_to_array(img, self.w, self.h)
        if not arr.any():
            return None
        R = max(2.0, lk.logo_glow_radius * self.s)
        x, y, ww, hh = cv2.boundingRect(arr)
        pad = int(R * 3)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(self.w, x + ww + pad), min(self.h, y + hh + pad)
        crop = arr[y0:y1, x0:x1]
        hk = max(5, int(round(R * 0.6)) | 1)
        halo = cv2.GaussianBlur(cv2.dilate(crop, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (hk, hk))), (0, 0), hk / 2.5)
        f32 = lambda a: a[..., None].astype(np.float32) / 255.0
        self._logo = (x0, y0, x1, y1, f32(crop), f32(cv2.GaussianBlur(crop, (0, 0), R * 0.25)),
                      f32(cv2.GaussianBlur(crop, (0, 0), R)), f32(halo))
        return self._logo

    def _overlay_logo(self, img, st: FrameState, song: Song):
        lk = song.look
        layers = self._logo_layers(lk)
        if layers is None:
            return
        x0, y0, x1, y1, fill, tight, wide, halo = layers
        L, P = min(st.logo_level, 1.0), st.logo_peak
        col = {"main": lk.main_rgb, "white": (255, 255, 255)}.get(lk.logo_color, lk.accent_rgb)
        roi = img[y0:y1, x0:x1].astype(np.float32)
        roi *= 1.0 - halo * 0.88 * min(1.0, L * 1.6 + P)      # dark halo keeps the name readable
        lum = fill * min(1.0, 0.95 * L + 0.35 * P) + tight * (0.35 * L + 0.6 * P) + wide * (0.25 * L + 0.8 * P)
        roi += lum * np.array(_bgr(col), np.float32)
        np.clip(roi, 0, 255, out=roi)
        img[y0:y1, x0:x1] = roi.astype(np.uint8)

    # ---- lyrics -----------------------------------------------------------------------------------
    def _lyric_layers(self, text: str, lk):
        key = (text, round(lk.lyrics_size, 3), round(lk.headroom, 3))
        if key in self._lyric_cache:
            return self._lyric_cache[key]
        if len(self._lyric_cache) > 48:
            self._lyric_cache.clear()
        img = QImage(self.w, self.h, QImage.Format.Format_Grayscale8)
        img.fill(0)
        font = QFont(LOGO_FONT)
        font.setPixelSize(100)
        cap100 = QFontMetricsF(font).capHeight() or 70.0
        cap = min(lk.lyrics_size * self.h, lk.headroom * self.h * 0.62)
        font.setPixelSize(max(6, int(100 * cap / cap100)))
        fm = QFontMetricsF(font)
        r = fm.tightBoundingRect(text)
        if r.width() > self.w * 0.86:
            font.setPixelSize(max(6, int(font.pixelSize() * self.w * 0.86 / r.width())))
            fm = QFontMetricsF(font)
            r = fm.tightBoundingRect(text)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        ox = self.w / 2 - (r.left() + r.right()) / 2
        oy = lk.headroom * self.h * 0.5 - (r.top() + r.bottom()) / 2
        painter.drawText(QRectF(ox, oy - fm.ascent(), self.w * 2, fm.height()), Qt.AlignmentFlag.AlignLeft, text)
        painter.end()
        arr = _qimage_to_array(img, self.w, self.h)
        if not arr.any():
            self._lyric_cache[key] = None
            return None
        x, y, ww, hh = cv2.boundingRect(arr)
        pad = int(12 * self.s) + 2
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(self.w, x + ww + pad), min(self.h, y + hh + pad)
        crop = arr[y0:y1, x0:x1]
        res = (x0, y0, x1, y1, crop[..., None].astype(np.float32) / 255.0,
               cv2.GaussianBlur(crop, (0, 0), 6 * self.s + 1)[..., None].astype(np.float32) / 255.0)
        self._lyric_cache[key] = res
        return res

    def _overlay_lyric(self, img, st: FrameState, song: Song):
        layers = self._lyric_layers(st.lyric, song.look)
        if layers is None:
            return
        x0, y0, x1, y1, fill, glow = layers
        a = st.lyric_alpha
        roi = img[y0:y1, x0:x1].astype(np.float32)
        roi *= 1.0 - glow * 0.8 * a
        roi += (fill * a + glow * 0.45 * a) * np.array(_bgr(song.look.accent_rgb), np.float32)
        np.clip(roi, 0, 255, out=roi)
        img[y0:y1, x0:x1] = roi.astype(np.uint8)
