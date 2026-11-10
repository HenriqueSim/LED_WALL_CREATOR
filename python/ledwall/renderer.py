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
        self._sun_cache: dict = {}
        self._logo_key = None
        self.logo_bbox = None
        self.shape_bboxes: dict = {}    # (element id, shape index) -> bbox, for picking shapes in the GUI
        self._lyric_cache: dict = {}
        self.bboxes: dict = {}          # element id -> (x0, y0, x1, y1) in pixels, for hit-testing in the GUI

    def th(self, px: float) -> int:
        return max(1, int(round(px * self.s)))

    # ---- helpers ----------------------------------------------------------------------
    @staticmethod
    def element_rgb(el, lk) -> tuple:
        return {"accent": lk.accent_rgb, "custom": el.custom_rgb}.get(el.color, lk.main_rgb)

    @staticmethod
    def parts(arr: np.ndarray, n: int) -> np.ndarray:
        """Share per-member values out over n parts, in order. More parts than members: each member
        gets a run of parts. Fewer: each part takes the brightest of its members (1 part = everyone)."""
        n = max(1, int(n))
        m = len(arr)
        if m == 0:
            return np.zeros(n, np.float32)
        if n >= m:
            return arr[(np.arange(n) * m) // n].astype(np.float32)
        starts = -((-np.arange(n) * m) // n)          # ceil(i * m / n)
        return np.maximum.reduceat(arr, starts).astype(np.float32)

    def part_members(self, n: int, m: int) -> np.ndarray:
        """Which member each of n parts belongs to (for spacing / audio bands)."""
        n = max(1, int(n))
        return (np.arange(n) * max(m, 1)) // n if n >= m else np.arange(n) * max(m, 1) // n

    def _drive(self, el, st: FrameState, song: Song, n: int):
        """(levels, brightness, flash) for the n parts of an element. Brightness = idle glow while the
        element is in the scene + its lit level."""
        lv, fl, presence = st.elements.get(el.id, (None, None, 0.0))
        if lv is None:
            lv = np.zeros(len(song.members), np.float32)
            fl = lv
        levels, flash = self.parts(lv, n), self.parts(fl, n)
        b = el.idle * presence + (1.0 - el.idle) * np.minimum(levels, 1.2)
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
        self.shape_bboxes = {}
        drawers = {"custom": self._draw_custom, "sun": self._draw_sun, "polygon": self._draw_polygon, "ring": self._draw_ring, "bars": self._draw_bars,
                   "shafts": self._draw_shafts, "horizon": self._draw_horizon, "beam": self._draw_beam}
        any_visible = False
        for el in reversed(song.elements):          # list top = front layer
            fn = drawers.get(el.type)
            if fn is None or el.id not in st.elements:      # not used by what is playing now
                continue
            any_visible = True
            layer.fill(0)
            soft = fn(layer, el, st, song)
            cv2.max(core, layer, dst=core)
            g = el.glow * (0.45 if el.fill != "outline" and el.type in ("polygon", "ring") else 1.0)
            if el.type == "sun":
                g = 0.0                                # the sun brings its own halo (soft) so its face keeps its colour  # big fills bloom less
            if g > 0:
                glow_src += cv2.resize(layer, (self.sw, self.sh), interpolation=cv2.INTER_AREA).astype(np.float32) * g
            if soft is not None:
                glow_src += soft

        sigma = max(0.6, lk.glow_radius * self.s / self.DS * (1.0 + 0.6 * st.bloom))
        tight = cv2.GaussianBlur(glow_src, (0, 0), sigma * 0.35)
        wide = cv2.GaussianBlur(glow_src, (0, 0), sigma)
        gain = lk.glow_intensity * (1.0 + 0.7 * st.bloom)
        glow = tight * (1.1 * gain) + wide * (1.9 * gain)
        if any_visible and lk.ambient > 0:
            # ambient wash: a constant base, more while elements are lit, pulsing with the music
            mean_lv = float(st.levels.mean()) if len(st.levels) else 0.0
            k = lk.ambient * (0.05 + 0.1 * mean_lv + 0.06 * lk.ambient_pulse * st.env)
            glow += self.ambient * np.array(_bgr(lk.main_rgb, k), np.float32)
        np.clip(glow, 0, 255, out=glow)
        out = cv2.add(core, cv2.resize(glow.astype(np.uint8), (self.w, self.h), interpolation=cv2.INTER_LINEAR))
        if st.blackout > 0.001:
            out = cv2.convertScaleAbs(out, alpha=max(0.0, 1.0 - st.blackout))
        if not lk.logo_text.strip():
            self.logo_bbox = None
        else:
            if st.logo_level > 0.003 or st.logo_peak > 0.003:
                self._overlay_logo(out, st, song)
            else:
                self._logo_layers(lk)        # keeps logo_bbox current for picking in the GUI
        if st.lyric and st.lyric_alpha > 0.003:
            self._overlay_lyric(out, st, song)
        return out

    # ---- element: custom (user-built from shapes) -------------------------------------------
    def _draw_custom(self, layer, el, st, song):
        lk = song.look
        shapes = el.shapes or []
        n = max(1, len(shapes))
        levels, b, flash = self._drive(el, st, song, n)
        cols = self._part_colors(el, lk, flash)
        S = el.size * self.h * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        cx, cy = el.x * self.w, el.y * self.h
        a0 = math.radians(el.rotation + el.spin * st.beat)
        ca, sa = math.cos(a0), math.sin(a0)
        boxes = []
        for i, sh in enumerate(shapes):
            pts_all = []
            bb, col = float(b[i]), cols[i]
            if bb <= 0.002:
                pts_all = self._shape_outline(sh, cx, cy, S, ca, sa, a0)
            else:
                pts_all = self._draw_shape(layer, sh, cx, cy, S, ca, sa, a0, _bgr(col, bb), bb)
            if len(pts_all):
                P = np.asarray(pts_all, np.float64)
                pad = self.th(sh.get("thickness", 5)) + 2
                box = (P[:, 0].min() - pad, P[:, 1].min() - pad, P[:, 0].max() + pad, P[:, 1].max() + pad)
                self.shape_bboxes[(el.id, i)] = box
                boxes.append(box)
        if boxes:
            B = np.array(boxes)
            self.bboxes[el.id] = (B[:, 0].min(), B[:, 1].min(), B[:, 2].max(), B[:, 3].max())
        else:
            self.bboxes[el.id] = (cx - S, cy - S, cx + S, cy + S)

    @staticmethod
    def _shape_geom(sh, cx, cy, S, ca, sa, a0):
        """Centre (px) and absolute angle (rad) of a shape inside its element."""
        lx, ly = sh.get("x", 0.0) * S, sh.get("y", 0.0) * S
        c = np.array([cx + lx * ca - ly * sa, cy + lx * sa + ly * ca])
        return c, a0 + math.radians(sh.get("rot", 0.0))

    def _shape_polylines(self, sh, cx, cy, S, ca, sa, a0):
        """[(points, closed)] describing a shape (for drawing and bounding boxes)."""
        kind = sh.get("kind", "line")
        c, a = self._shape_geom(sh, cx, cy, S, ca, sa, a0)
        d = np.array([math.cos(a), math.sin(a)])
        nrm = np.array([-d[1], d[0]])
        size = max(0.0, sh.get("size", 0.5)) * S
        if kind == "line":
            return [(np.array([c - d * size / 2, c + d * size / 2]), False)]
        if kind == "dashes":
            n = max(1, int(sh.get("dashes", 8)))
            seg = size / n
            p0 = c - d * size / 2
            return [(np.array([p0 + d * (k * seg + seg * 0.2), p0 + d * ((k + 1) * seg - seg * 0.2)]), False) for k in range(n)]
        if kind == "rect":
            hw, hh = size / 2, max(0.0, sh.get("h", 0.3)) * S / 2
            return [(np.array([c + d * x + nrm * y for x, y in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]), True)]
        if kind == "circle":
            t = np.linspace(0, 2 * math.pi, 73)[:-1]
            return [(np.stack([c[0] + size * np.cos(t), c[1] + size * np.sin(t)], 1), True)]
        if kind == "arc":
            sweep = math.radians(max(1.0, min(360.0, sh.get("sweep", 180.0))))
            t = a + np.linspace(0, sweep, max(8, int(sweep / (2 * math.pi) * 96)))
            return [(np.stack([c[0] + size * np.cos(t), c[1] + size * np.sin(t)], 1), sweep >= 2 * math.pi - 1e-6)]
        if kind == "polygon":
            k = max(3, int(sh.get("sides", 3)))
            t = a - math.pi / 2 + np.arange(k) * 2 * math.pi / k
            return [(np.stack([c[0] + size * np.cos(t), c[1] + size * np.sin(t)], 1), True)]
        if kind == "rays":
            n = max(1, int(sh.get("dashes", 9)))
            sweep = math.radians(max(0.0, min(360.0, sh.get("sweep", 180.0))))
            inner = max(0.0, sh.get("h", 0.5)) * S
            step = sweep / n if sweep >= 2 * math.pi - 1e-6 else sweep / max(1, n - 1)
            out = []
            for k in range(n):
                t = a + (k * step if n > 1 else sweep / 2)
                u = np.array([math.cos(t), math.sin(t)])
                out.append((np.array([c + u * inner, c + u * size]), False))
            return out
        return []

    def _shape_outline(self, sh, cx, cy, S, ca, sa, a0):
        pts = [p for poly, _ in self._shape_polylines(sh, cx, cy, S, ca, sa, a0) for p in poly]
        return pts

    def _draw_shape(self, layer, sh, cx, cy, S, ca, sa, a0, color, bb):
        polys = self._shape_polylines(sh, cx, cy, S, ca, sa, a0)
        t = self.th(sh.get("thickness", 5))
        kind = sh.get("kind")
        fill = sh.get("filled", False) and kind in ("rect", "circle", "arc", "polygon")
        for poly, closed in polys:
            if fill:
                pts = poly
                if kind == "arc" and not closed:   # a filled arc is a pie slice
                    c, _ = self._shape_geom(sh, cx, cy, S, ca, sa, a0)
                    pts = np.vstack([c[None, :], poly])
                dim = tuple(v * 0.8 for v in color)
                cv2.fillPoly(layer, [_fx(pts)], dim, AA, SHIFT)
            cv2.polylines(layer, [_fx(poly)], closed, color, t, AA, SHIFT)
        return [p for poly, _ in polys for p in poly]

    # ---- element: sunset sun -------------------------------------------------------------
    def _sun_maps(self, cx, cy, R, soft, cuts):
        """(x0, y0, alpha, heat) of the half disc at 1/2 resolution, cropped to its box. Cached."""
        key = (round(cx / 2), round(cy / 2), round(R / 2), round(soft, 3), int(cuts), self.w, self.h)
        hit = self._sun_cache.get(key)
        if hit is not None:
            return hit
        if len(self._sun_cache) > 24:
            self._sun_cache.clear()
        hw, hh = max(2, self.w // 2), max(2, self.h // 2)
        c, r = cx / 2, max(1.0, R / 2)
        feather = max(0.75, soft * r)
        x0, x1 = max(0, int(c - r - feather * 2)), min(hw, int(math.ceil(c + r + feather * 2)))
        y0, y1 = max(0, int(cy / 2 - r - feather * 2)), min(hh, int(math.ceil(cy / 2 + 1)))
        if x1 <= x0 or y1 <= y0:
            self._sun_cache[key] = None
            return None
        ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dx, dy = xs + 0.5 - c, ys + 0.5 - cy / 2
        d = np.sqrt(dx * dx + dy * dy)
        alpha = np.clip((r - d) / feather + 0.5, 0.0, 1.0)
        alpha = alpha * alpha * (3 - 2 * alpha)                       # smoothstep edge
        alpha *= np.clip(cy / 2 - (ys + 0.5) + 0.5, 0.0, 1.0)         # cut at the horizon
        u = np.clip(d / r, 0.0, 1.0)
        heat = (1.0 - u) ** 1.6                                       # hottest at the centre
        if cuts > 0:   # retro horizontal cut lines, thicker towards the horizon
            band_top = r * 0.55
            hgt = np.clip(-dy, 0, None)
            for i in range(int(cuts)):
                f = (i + 0.5) / cuts
                yc = band_top * (1 - f)
                th = max(0.6, r * 0.018 * (0.4 + 1.2 * f))
                alpha *= np.clip(np.abs(hgt - yc) / th - 0.5, 0.0, 1.0)
        res = (x0, y0, alpha[..., None].astype(np.float32), heat[..., None].astype(np.float32))
        self._sun_cache[key] = res
        return res

    def _draw_sun(self, layer, el, st, song):
        lk = song.look
        levels, b, flash = self._drive(el, st, song, 1)
        v = min(1.0, float(levels[0]))
        f = 0.6 * float(flash[0])
        bright = float(b[0])
        if bright <= 0.002:
            self.bboxes[el.id] = (el.x * self.w - el.size * self.h, el.y * self.h - el.size * self.h,
                                  el.x * self.w + el.size * self.h, el.y * self.h)
            return None
        cx, cy = el.x * self.w, el.y * self.h
        R = el.size * self.h * (1.0 + el.react_scale * min(1.0, v))
        self.bboxes[el.id] = (cx - R, cy - R, cx + R, cy)
        maps = self._sun_maps(cx, cy, R, el.softness, el.cuts)
        if maps is None:
            return None
        x0, y0, alpha, heat = maps
        base = np.array(self.element_rgb(el, lk), np.float32)
        base = base * (1 - f) + np.array(lk.accent_rgb, np.float32) * f
        edge = np.array(_bgr(base * 0.92), np.float32)
        hot = np.array(_bgr(base * (1 - 0.45) + 255.0 * 0.45), np.float32)
        hot = edge + (hot - edge) * el.core
        img = alpha * (edge + (hot - edge) * heat) * bright
        img = np.clip(img, 0, 255).astype(np.uint8)
        hh, ww = img.shape[:2]
        X0, Y0 = x0 * 2, y0 * 2
        X1, Y1 = min(self.w, X0 + ww * 2), min(self.h, Y0 + hh * 2)
        big = cv2.resize(img, (ww * 2, hh * 2), interpolation=cv2.INTER_LINEAR)[: Y1 - Y0, : X1 - X0]
        cv2.max(layer[Y0:Y1, X0:X1], big, dst=layer[Y0:Y1, X0:X1])
        if el.glow <= 0:
            return None
        # halo only outside the disc: quarter-res alpha, blurred, minus the disc itself
        a = np.zeros((self.sh, self.sw), np.float32)
        q = cv2.resize(alpha[..., 0], (max(1, ww // 2), max(1, hh // 2)), interpolation=cv2.INTER_AREA)
        qx, qy = x0 // 2, y0 // 2
        qh, qw = min(q.shape[0], self.sh - qy), min(q.shape[1], self.sw - qx)
        a[qy:qy + qh, qx:qx + qw] = q[:qh, :qw]
        halo = cv2.GaussianBlur(a, (0, 0), max(1.0, R / self.DS * 0.12)) * np.clip((1.0 - a) * 3.0, 0.0, 1.0)
        return halo[..., None] * (edge * bright * el.glow * 0.9)

    # ---- element: polygon (hexagon) ------------------------------------------------------
    def _draw_polygon(self, layer, el, st, song):
        lk = song.look
        sides = max(3, int(el.sides))
        levels, b, flash = self._drive(el, st, song, sides)
        cols = self._part_colors(el, lk, flash)
        cx, cy = el.x * self.w, el.y * self.h
        R = el.size * self.h * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        ang = np.radians(el.rotation + el.spin * st.beat - 90.0 + np.arange(sides) * 360.0 / sides)
        verts = np.stack([cx + R * np.cos(ang), cy + R * np.sin(ang)], 1)
        mean_b, mean_col = float(np.mean(b)), np.mean(cols, axis=0)
        if el.fill in ("filled", "gradient"):
            self.mask.fill(0)
            cv2.fillPoly(self.mask, [_fx(verts)], int(255 * min(1.0, mean_b * (0.85 if el.fill == "gradient" else 0.5))), AA, SHIFT)
            self._apply_mask(layer, self.mask, mean_col, cy - R, cy + R + 1, el.fill == "gradient")
        t = self.th(el.thickness)
        for i in range(sides):          # one edge per part
            cv2.line(layer, tuple(_fx(verts[i])), tuple(_fx(verts[(i + 1) % sides])), _bgr(cols[i], float(b[i])), t, AA, SHIFT)
        for i in range(sides):          # glowing joints
            k1, k2 = (i - 1) % sides, i
            bb = max(float(b[k1]), float(b[k2]))
            cv2.circle(layer, tuple(_fx(verts[i])), int(t * 0.9 * SCALE), _bgr(self._outline_rgb(cols[k2]), bb), -1, AA, SHIFT)
        self.bboxes[el.id] = (cx - R, cy - R, cx + R, cy + R)

    # ---- element: ring ---------------------------------------------------------------------------
    def _draw_ring(self, layer, el, st, song):
        lk = song.look
        n = max(1, int(el.count))
        levels, b, flash = self._drive(el, st, song, n)
        cols = self._part_colors(el, lk, flash)
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
    def _bars_layout(self, el, n_mem: int):
        n = max(1, int(el.count))
        x0 = (el.x - el.size / 2) * self.w
        width = el.size * self.w
        key = (el.id, n, n_mem, round(el.gap, 3), round(x0), round(width), self.w, self.h)
        lay = self._layout_cache.get(key)
        if lay is None:
            if len(self._layout_cache) > 16:
                self._layout_cache.clear()
            rng = np.random.default_rng(7)
            owner = self.part_members(n, n_mem)
            # extra space where the bars change member, so each member reads as a cluster
            breaks = int(np.count_nonzero(np.diff(owner))) if n > n_mem > 1 else 0
            gap = max(0.0, el.gap)
            units = n + (n - 1) * gap + breaks * 0.9
            bw = width / units
            lay = []
            x = x0
            for i in range(n):
                if i and n > n_mem > 1 and owner[i] != owner[i - 1]:
                    x += bw * 0.9
                ang = 0.0 if (n == 1 or rng.random() < 0.3) else rng.uniform(-13, 13)
                lay.append(dict(member=int(owner[i]), x0=x, x1=x + bw, slope=math.tan(math.radians(ang)) * bw,
                                rest=rng.uniform(0.45, 0.62) if n > 1 else 0.55, hv=rng.uniform(0.8, 1.0) if n > 1 else 1.0,
                                ph=rng.uniform(0, 6.28)))
                x += bw * (1 + gap)
            self._layout_cache[key] = lay
        return lay

    def _bar_limits(self, el, lk):
        top_max = max(lk.headroom + 0.04, 1.0 - el.height) * self.h
        return top_max, self.h

    def _draw_bars(self, layer, el, st, song):
        lk = song.look
        n_mem = len(song.members)
        n = max(1, int(el.count))
        levels, b, flash = self._drive(el, st, song, n)
        rgb = self.element_rgb(el, lk)
        top_max, bottom = self._bar_limits(el, lk)
        span = bottom - top_max
        slats = self._bars_layout(el, n_mem)
        mask = self.mask
        mask.fill(0)
        polys = []
        any_flash = bool(flash.max() > 0.01)
        fmask = np.zeros_like(mask) if any_flash else None
        bands = self.parts(st.bands, n) if len(st.bands) else np.zeros(n, np.float32)
        for k, sl in enumerate(slats):
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
        for poly, bb in (polys if el.thickness > 0 else []):
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
        n = max(1, int(el.count))
        levels, b, flash = self._drive(el, st, song, n)
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
        n = max(1, int(el.count))
        levels, b, flash = self._drive(el, st, song, n)
        cols = self._part_colors(el, lk, flash)
        rgb = self.element_rgb(el, lk)
        top_rgb, bottom_rgb = gradient_colors(rgb)
        yh = el.y * self.h
        x0 = (el.x - el.size / 2) * self.w
        width = el.size * self.w
        slot = width / n
        top_max = max(lk.headroom + 0.04, el.y - el.height) * self.h
        cv2.line(layer, tuple(_fx((x0, yh))), tuple(_fx((x0 + width, yh))), _bgr(rgb, (float(b.min()) + 0.5 * float(levels.mean())) * 0.7),
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
        n = max(1, int(el.count))
        levels, b, flash = self._drive(el, st, song, n)
        cols = self._part_colors(el, lk, flash)
        cx, cy = el.x * self.w, el.y * self.h
        L = el.size * self.w * (1.0 + el.react_scale * float(np.mean(np.minimum(levels, 1.2))))
        a = math.radians(el.rotation + el.spin * st.beat)
        d = np.array([math.cos(a), math.sin(a)])
        nrm = np.array([-d[1], d[0]])
        seg = L / n
        gap = min(seg * 0.08, 0.01 * self.w) if n > 1 else 0.0
        t = self.th(el.thickness)
        lines = max(1, int(el.lines))
        sp = el.spacing * self.h
        for li in range(lines):
            c = np.array([cx, cy]) + nrm * (li - (lines - 1) / 2) * sp
            p0 = c - d * L / 2
            for k in range(n):
                if b[k] <= 0.002:
                    continue
                s0 = p0 + d * (k * seg + gap / 2)
                s1 = p0 + d * ((k + 1) * seg - gap / 2)
                cv2.line(layer, tuple(_fx(s0)), tuple(_fx(s1)), _bgr(cols[k], float(b[k])), t, AA, SHIFT)
        half_w = (lines - 1) / 2 * sp
        ex = abs(d[0]) * L / 2 + abs(nrm[0]) * half_w + t
        ey = abs(d[1]) * L / 2 + abs(nrm[1]) * half_w + t
        self.bboxes[el.id] = (cx - ex, cy - ey, cx + ex, cy + ey)

    # ---- band name layer ----------------------------------------------------------------------
    @staticmethod
    def logo_lines(lk) -> list:
        """The band name split into lines according to the layout."""
        text = " ".join(lk.logo_text.split())
        text = {"upper": text.upper(), "lower": text.lower()}.get(lk.logo_case, text)
        words = text.split()
        if lk.logo_layout == "stacked" and len(words) >= 2:
            return [words[0] + ("." if lk.logo_dot else ""), " ".join(words[1:])]
        if lk.logo_layout == "two_lines" and len(words) >= 2:
            cut = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
            return [" ".join(words[:cut]), " ".join(words[cut:])]
        return [text] if text else []

    def _logo_layers(self, lk):
        key = (lk.logo_text, lk.logo_layout, lk.logo_dot, round(lk.logo_offset, 3), lk.logo_font, lk.logo_case,
               round(lk.logo_size, 3), round(lk.logo_x, 3), round(lk.logo_y, 3), round(lk.logo_glow_radius, 1), self.w, self.h)
        if key == self._logo_key:
            return self._logo
        self._logo_key, self._logo = key, None
        self.logo_bbox = None
        lines = self.logo_lines(lk)
        if not lines:
            return None
        img = QImage(self.w, self.h, QImage.Format.Format_Grayscale8)
        img.fill(0)
        font = QFont(lk.logo_font or LOGO_FONT)
        font.setPixelSize(100)
        xh100 = QFontMetricsF(font).xHeight() or 50.0
        two = len(lines) == 2
        target_h = lk.logo_size * self.h
        xh = target_h / (2.1 if two else 1.35)
        font.setPixelSize(max(6, int(round(100 * xh / xh100))))
        stacked = two and lk.logo_layout == "stacked"

        def layout(font):
            fm = QFontMetricsF(font)
            xh = fm.xHeight() or fm.height() * 0.5
            rects = [fm.tightBoundingRect(t) for t in lines]
            if not two:
                return fm, [(0.0, 0.0)], rects
            if stacked:   # 2nd line shifted right, tight leading
                return fm, [(0.0, 0.0), (fm.horizontalAdvance(lines[0]) * lk.logo_offset, xh * 1.07)], rects
            dy = max(xh * 1.25, rects[0].bottom() - rects[1].top() + xh * 0.25)
            c0 = (rects[0].left() + rects[0].right()) / 2
            c1 = (rects[1].left() + rects[1].right()) / 2
            return fm, [(0.0, 0.0), (c0 - c1, dy)], rects

        fm, offs, rects = layout(font)

        def extent(offs, rects):
            left = min(o[0] + r.left() for o, r in zip(offs, rects))
            right = max(o[0] + r.right() for o, r in zip(offs, rects))
            top = min(o[1] + r.top() for o, r in zip(offs, rects))
            bottom = max(o[1] + r.bottom() for o, r in zip(offs, rects))
            return left, right, top, bottom

        left, right, top, bottom = extent(offs, rects)
        # keep it on the wall and no taller than asked for (script fonts have tall ascenders)
        f = min(1.0, self.w * 0.92 / max(1.0, right - left), min(target_h * 1.2, self.h * 0.95) / max(1.0, bottom - top))
        if f < 0.999:
            font.setPixelSize(max(6, int(font.pixelSize() * f)))
            fm, offs, rects = layout(font)
            left, right, top, bottom = extent(offs, rects)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        ox = lk.logo_x * self.w - (left + right) / 2
        oy = lk.logo_y * self.h - (top + bottom) / 2
        for i, (t, (dx, dy)) in enumerate(zip(lines, offs)):
            if stacked and i == 0:   # descenders of the first line cut at its baseline
                painter.setClipRect(QRectF(0, 0, self.w, oy + 0.5))
            painter.drawText(QRectF(ox + dx, oy + dy - fm.ascent(), self.w * 2, fm.height()), Qt.AlignmentFlag.AlignLeft, t)
            painter.setClipping(False)
        painter.end()
        arr = _qimage_to_array(img, self.w, self.h)
        if not arr.any():
            return None
        R = max(2.0, lk.logo_glow_radius * self.s)
        x, y, ww, hh = cv2.boundingRect(arr)
        self.logo_bbox = (x, y, x + ww, y + hh)
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
        if lk.logo_color == "cutout":   # dark silhouette cut into whatever is behind it (e.g. the sun)
            roi = img[y0:y1, x0:x1].astype(np.float32)
            k = min(1.0, L + 0.3 * P)
            roi *= 1.0 - np.clip(fill * 0.94 + tight * 0.25, 0, 1) * k
            img[y0:y1, x0:x1] = roi.astype(np.uint8)
            return
        col = {"main": lk.main_rgb, "white": (255, 255, 255)}.get(lk.logo_color, lk.accent_rgb)
        roi = img[y0:y1, x0:x1].astype(np.float32)
        roi *= 1.0 - halo * 0.88 * min(1.0, L * 1.6 + P)      # dark halo keeps the name readable
        lum = fill * min(1.0, 0.95 * L + 0.35 * P) + tight * (0.35 * L + 0.6 * P) + wide * (0.25 * L + 0.8 * P)
        roi += lum * np.array(_bgr(col), np.float32)
        np.clip(roi, 0, 255, out=roi)
        img[y0:y1, x0:x1] = roi.astype(np.uint8)

    # ---- lyrics -----------------------------------------------------------------------------------
    def _lyric_layers(self, text: str, lk):
        key = (text, round(lk.lyrics_size, 3), round(lk.headroom, 3), lk.lyrics_font or lk.logo_font)
        if key in self._lyric_cache:
            return self._lyric_cache[key]
        if len(self._lyric_cache) > 48:
            self._lyric_cache.clear()
        img = QImage(self.w, self.h, QImage.Format.Format_Grayscale8)
        img.fill(0)
        font = QFont(lk.lyrics_font or lk.logo_font or LOGO_FONT)
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
