"""FrameState -> BGR uint8 frame.

Layers (back to front):
  1. background design (Vertical Blocks / Light Shafts / Horizon / None), drawn crisp with
     OpenCV + a soft layer, then a two-radius Gaussian bloom computed at 1/4 resolution
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
        self.mask = np.zeros((self.h, self.w), np.uint8)
        self.fmask = np.zeros((self.h, self.w), np.uint8)
        ys, xs = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.ambient = np.exp(-(((xs - self.sw / 2) / (self.sw * 0.55)) ** 2 + ((self.sh - ys) / (self.sh * 0.38)) ** 2))[..., None]
        self.vfade = (np.clip(1.0 - ys[:, :1] / self.sh, 0, 1) ** 0)[..., None]  # replaced in _gradient
        self._grad_key = self._layout_key = self._logo_key = None
        self._lyric_cache: dict = {}

    def th(self, px: float) -> int:
        return max(1, int(round(px * self.s)))

    # ---- caches --------------------------------------------------------------------
    def _gradient(self, main_rgb, headroom: float):
        key = (tuple(main_rgb), round(headroom, 3))
        if key != self._grad_key:
            top, bottom = gradient_colors(main_rgb)
            for size, attr in (((self.h, self.w), "grad"), ((self.sh, self.sw), "grad_small")):
                hh, ww = size
                t = np.clip((np.arange(hh, dtype=np.float32) / hh - headroom) / (1.0 - headroom), 0, 1)[:, None]
                col = np.array(top[::-1], np.float32) * (1 - t) + np.array(bottom[::-1], np.float32) * t
                setattr(self, attr, np.ascontiguousarray(np.repeat(col[:, None, :], ww, axis=1)).astype(np.uint8))
            self.outline_rgb = tuple(min(255.0, c * 0.55 + 255 * 0.45) for c in top)
            yy = np.arange(self.sh, dtype=np.float32)[:, None, None] / self.sh
            self.vfade = np.clip((yy - headroom) / (1.0 - headroom), 0, 1) ** 1.3  # light rises from the floor
            self._grad_key = key
        return self.grad

    def _layout(self, n: int, slats: int, seed: int = 7):
        """Slat geometry: members are clusters of thin slats so the 6 read as one skyline."""
        key = (n, slats, self.w, self.h)
        if key != self._layout_key:
            rng = np.random.default_rng(seed)
            margin = 0.03 * self.w
            slot = (self.w - 2 * margin) / max(n, 1)
            member_gap = slot * 0.09
            area = slot - member_gap
            sw_ = area / (slats + (slats - 1) * 0.28)
            out = []
            for i in range(n):
                x0 = margin + i * slot + member_gap / 2
                for j in range(slats):
                    xl = x0 + j * sw_ * 1.28
                    ang = 0.0 if rng.random() < 0.3 else rng.uniform(-13, 13)
                    out.append(dict(member=i, x0=xl, x1=xl + sw_, slope=math.tan(math.radians(ang)) * sw_,
                                    rest=rng.uniform(0.64, 0.76), hv=rng.uniform(0.8, 1.0), ph=rng.uniform(0, 6.28)))
            self.slats = out
            self.slot = slot
            self._layout_key = key
        return self.slats

    # ---- main entry ------------------------------------------------------------------
    def render(self, st: FrameState, song: Song) -> np.ndarray:
        lk = song.look
        core = self.core
        core.fill(0)
        self._gradient(lk.main_rgb, lk.headroom)
        soft = None
        design = lk.design
        if len(st.levels) and design == "blocks":
            self._draw_blocks(core, st, song)
        elif len(st.levels) and design == "shafts":
            soft = self._draw_shafts(core, st, song)
        elif len(st.levels) and design == "horizon":
            soft = self._draw_horizon(core, st, song)

        small = cv2.resize(core, (self.sw, self.sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        if soft is not None:
            small += soft
        sigma = max(0.6, lk.glow_radius * self.s / self.DS * (1.0 + 0.6 * st.bloom))
        tight = cv2.GaussianBlur(small, (0, 0), sigma * 0.35)
        wide = cv2.GaussianBlur(small, (0, 0), sigma)
        gain = lk.glow_intensity * (1.0 + 0.7 * st.bloom) * {"blocks": 0.45, "shafts": 0.7}.get(design, 1.0)
        glow = tight * (1.1 * gain) + wide * (1.9 * gain)
        mean_lv = float(st.levels.mean()) if len(st.levels) else 0.0
        if design != "none":
            amb = 0.05 + 0.1 * mean_lv + 0.06 * st.env
            glow += self.ambient * np.array(_bgr(lk.main_rgb, amb), np.float32)
        np.clip(glow, 0, 255, out=glow)
        out = cv2.add(core, cv2.resize(glow.astype(np.uint8), (self.w, self.h), interpolation=cv2.INTER_LINEAR))
        if st.blackout > 0.001:
            out = cv2.convertScaleAbs(out, alpha=max(0.0, 1.0 - st.blackout))

        if lk.logo_text.strip() and (st.logo_level > 0.003 or st.logo_peak > 0.003):
            self._overlay_logo(out, st, song)
        if st.lyric and st.lyric_alpha > 0.003:
            self._overlay_lyric(out, st, song)
        return out

    # ---- design: vertical blocks --------------------------------------------------------
    def _slat_top(self, sl, level, st, lk):
        top_max = (lk.headroom + 0.04) * self.h
        rest = sl["rest"] * self.h
        y = rest - (rest - top_max) * min(level, 1.0) * sl["hv"]
        y -= 0.025 * self.h * lk.audio_react * float(st.bands[sl["member"]]) * (0.5 + 0.5 * math.sin(sl["ph"] + st.beat * 1.7))
        return max(top_max, y)

    def _draw_blocks(self, core, st: FrameState, song: Song):
        lk = song.look
        slats = self._layout(len(song.members), max(1, lk.slats_per_member))
        idle = lk.idle_level
        mask, fmask = self.mask, self.fmask
        mask.fill(0)
        any_flash = bool(st.flash.max() > 0.01) if len(st.flash) else False
        if any_flash:
            fmask.fill(0)
        polys = []
        for sl in slats:
            lv = float(st.levels[sl["member"]])
            b = idle + (1.0 - idle) * min(lv, 1.2)
            y = self._slat_top(sl, lv, st, lk)
            poly = np.array([(sl["x0"], y - sl["slope"] / 2), (sl["x1"], y + sl["slope"] / 2),
                             (sl["x1"], self.h + 2), (sl["x0"], self.h + 2)])
            p = _fx(poly)
            cv2.fillPoly(mask, [p], int(255 * min(1.0, b * 0.82)), AA, SHIFT)
            if any_flash:
                f = float(st.flash[sl["member"]])
                if f > 0.01:
                    cv2.fillPoly(fmask, [p], int(255 * min(1.0, f * b)), AA, SHIFT)
            polys.append((p, b))
        cv2.multiply(self.grad, cv2.merge([mask, mask, mask]), dst=core, scale=1.0 / 255.0)
        if any_flash:
            acc = cv2.multiply(cv2.merge([fmask, fmask, fmask]), _bgr(lk.accent_rgb) + (0.0,), scale=1.0 / 255.0)
            cv2.max(core, acc, dst=core)
        for p, b in polys:   # thin bright edge like a neon tube
            cv2.polylines(core, [p], True, _bgr(self.outline_rgb, min(1.0, b * 1.05)), self.th(2), AA, SHIFT)
        if st.sparkle > 0.01:
            self._sparkles(core, st, lk, [(sl, self._slat_top(sl, float(st.levels[sl["member"]]), st, lk)) for sl in slats])

    def _sparkles(self, core, st, lk, tops):
        rng = np.random.default_rng(st.step * 7919 + 13)
        count = int(3 + 14 * st.sparkle)
        for _ in range(count):
            sl, y = tops[int(rng.integers(0, len(tops)))]
            x = rng.uniform(sl["x0"], sl["x1"])
            yy = y + rng.uniform(0.0, 0.35) * (self.h - y)
            r = 0.006 * self.h * rng.uniform(0.6, 1.4)
            col = _bgr(lk.accent_rgb, st.sparkle * 1.2)
            cv2.circle(core, tuple(_fx((x, yy))), int(r * SCALE), col, -1, AA, SHIFT)
            cv2.line(core, tuple(_fx((x - 3 * r, yy))), tuple(_fx((x + 3 * r, yy))), col, self.th(1), AA, SHIFT)
            cv2.line(core, tuple(_fx((x, yy - 3 * r))), tuple(_fx((x, yy + 3 * r))), col, self.th(1), AA, SHIFT)

    # ---- design: light shafts (soft) ------------------------------------------------------
    def _draw_shafts(self, core, st: FrameState, song: Song):
        lk = song.look
        n = len(song.members)
        slot = self.w * 0.94 / n
        smask = np.zeros((self.sh, self.sw), np.uint8)
        top_max = (lk.headroom + 0.04) * self.h
        k = 1.0 / self.DS
        flash_mix = float(st.flash.max()) if len(st.flash) else 0.0
        for i in range(n):
            lv = float(st.levels[i])
            b = lk.idle_level + (1 - lk.idle_level) * min(lv, 1.2)
            cx = self.w * 0.03 + (i + 0.5) * slot
            y = self.h * 0.72 - (self.h * 0.72 - top_max) * min(lv, 1.0)
            bw, tw = slot * 0.05, slot * 0.2
            poly = np.array([(cx - bw, self.h), (cx + bw, self.h), (cx + tw, y), (cx - tw, y)]) * k
            cv2.fillPoly(smask, [_fx(poly)], int(255 * min(1.0, b)), AA, SHIFT)
            cv2.line(core, tuple(_fx((cx, self.h))), tuple(_fx((cx, y + 0.1 * (self.h - y))))
                     , _bgr(self.outline_rgb, b * 0.55), self.th(2), AA, SHIFT)
        soft = cv2.multiply(self.grad_small, cv2.merge([smask, smask, smask]), scale=1.0 / 255.0).astype(np.float32)
        soft = cv2.GaussianBlur(soft, (0, 0), 6 * self.s / (self.DS / 4) + 1)
        if flash_mix > 0.01:
            soft += cv2.merge([smask] * 3).astype(np.float32) / 255.0 * np.array(_bgr(lk.accent_rgb, flash_mix * 0.6), np.float32)
        if st.sparkle > 0.01:
            tops = [({"x0": self.w * 0.03 + (i + 0.3) * slot, "x1": self.w * 0.03 + (i + 0.7) * slot, "member": i},
                     self.h * 0.6) for i in range(n)]
            self._sparkles(core, st, lk, tops)
        return soft * self.vfade * 0.9

    # ---- design: horizon ------------------------------------------------------------------
    def _draw_horizon(self, core, st: FrameState, song: Song):
        lk = song.look
        n = len(song.members)
        yh = self.h * 0.84
        slot = self.w * 0.9 / n
        mean_lv = float(st.levels.mean())
        top_rgb, bottom_rgb = gradient_colors(lk.main_rgb)
        base_b = lk.idle_level + 0.5 * mean_lv
        cv2.line(core, tuple(_fx((0, yh))), tuple(_fx((self.w, yh))), _bgr(lk.main_rgb, base_b * 0.7), self.th(2), AA, SHIFT)
        soft = np.zeros((self.sh, self.sw, 3), np.float32)
        k = 1.0 / self.DS
        top_max = (lk.headroom + 0.04) * self.h
        for i in range(n):
            lv = float(st.levels[i])
            b = lk.idle_level + (1 - lk.idle_level) * min(lv, 1.2)
            col = np.array(lk.main_rgb, np.float32) * (1 - st.flash[i]) + np.array(lk.accent_rgb, np.float32) * st.flash[i]
            cx = self.w * 0.05 + (i + 0.5) * slot
            half = slot * (0.12 + 0.22 * min(lv, 1.0))
            cv2.line(core, tuple(_fx((cx - half, yh))), tuple(_fx((cx + half, yh))), _bgr(col, b * 1.2), self.th(4), AA, SHIFT)
            # upward haze cone + reflection, both soft
            yt = yh - (yh - top_max) * min(lv, 1.0) * 0.9
            cone = np.array([(cx - half * 0.6, yh), (cx + half * 0.6, yh), (cx + half * 1.6, yt), (cx - half * 1.6, yt)]) * k
            cv2.fillPoly(soft, [_fx(cone)], _bgr(top_rgb, b * 0.35), AA, SHIFT)
            refl = np.array([(cx - half * 0.5, yh), (cx + half * 0.5, yh), (cx + half * 0.2, self.h), (cx - half * 0.2, self.h)]) * k
            cv2.fillPoly(soft, [_fx(refl)], _bgr(bottom_rgb, b * 0.5), AA, SHIFT)
        if st.sparkle > 0.01:
            tops = [({"x0": self.w * 0.05 + i * slot, "x1": self.w * 0.05 + (i + 1) * slot, "member": i}, yh * 0.8)
                    for i in range(n)]
            self._sparkles(core, st, lk, tops)
        return cv2.GaussianBlur(soft, (0, 0), 5 * self.s + 1) * (0.35 + 0.65 * self.vfade)

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
