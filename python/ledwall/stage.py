"""Stage simulation: puts a rendered wall frame into a venue view with the band in front.

Two lighting modes:
  "led"        the wall emits light: the band is backlit (dark silhouettes with rim light), the wall
               spills onto the haze and is reflected on the stage floor
  "projector"  the image comes from a projector in the audience: it lands on the band too (their
               bodies show the image), the band casts shadows on the screen, the screen's black is
               grey and the beam is visible in the haze

Optional house lights: the venue's own moving heads (not controlled by the band), coloured beams
through the haze that jump to new random positions and colours with the beat.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

AA = cv2.LINE_AA

# house light colours (RGB): saturated club colours plus a cold white
HOUSE_COLORS = [(255, 40, 160), (40, 200, 255), (60, 80, 255), (255, 150, 30), (255, 40, 40),
                (60, 255, 140), (180, 60, 255), (230, 235, 255)]


def _h01(*k) -> float:
    """Deterministic pseudo-random 0..1 from integers (same beat -> same look on every replay)."""
    x = 0
    for v in k:
        x = (x * 1000003 + int(v) * 2654435761 + 0x9E3779B9) & 0xFFFFFFFF
        x ^= x >> 15
        x = (x * 2246822519) & 0xFFFFFFFF
        x ^= x >> 13
    return (x & 0xFFFFFF) / float(0x1000000)

# instrument / pose for each member slot, cycled: on a typical stage the drummer sits in the middle
ROLES_BY_COUNT = {
    1: ["singer"], 2: ["guitar", "singer"], 3: ["guitar", "singer", "bass"],
    4: ["guitar", "singer", "drums", "bass"], 5: ["guitar", "singer", "drums", "bass", "keys"],
    6: ["keys", "guitar", "drums", "singer", "bass", "guitar"],
}


def roles_for(n: int) -> list:
    if n in ROLES_BY_COUNT:
        return ROLES_BY_COUNT[n]
    base = ROLES_BY_COUNT[6]
    return [base[i % 6] for i in range(n)]


class StageSimulator:
    def __init__(self, width: int, height: int):
        self.w, self.h = int(width), int(height)
        self._band_key = None
        self._aud_key = None

    # ---- layout ------------------------------------------------------------------------------
    def wall_rect(self, wall_aspect: float) -> tuple:
        """(x0, y0, x1, y1) of the wall inside the stage view; wall_aspect = wall height / width."""
        ww = self.w * 0.86
        wh = ww * wall_aspect
        max_h = self.h * 0.50
        if wh > max_h:
            wh, ww = max_h, max_h / wall_aspect
        x0 = (self.w - ww) / 2
        y1 = self.h * 0.62
        return int(round(x0)), int(round(y1 - wh)), int(round(x0 + ww)), int(round(y1))

    def wall_size(self, wall_aspect: float) -> tuple:
        x0, y0, x1, y1 = self.wall_rect(wall_aspect)
        return max(2, (x1 - x0) // 2 * 2), max(2, (y1 - y0) // 2 * 2)

    # ---- silhouettes ----------------------------------------------------------------------------
    def _figure(self, m, cx, feet, H, role, t):
        """Draw one band member (and instrument) into mask m. H = standing height in px."""
        sway = math.sin(t * 2.1 + cx * 0.01) * H * 0.006
        if role == "drums":
            # sitting behind the kit on a riser
            seat = feet - H * 0.28
            hx = cx + sway
            cv2.circle(m, (int(hx), int(seat - H * 0.47)), int(H * 0.06), 255, -1, AA)
            torso = np.array([(hx - H * 0.11, seat - H * 0.38), (hx + H * 0.11, seat - H * 0.38),
                              (hx + H * 0.09, seat), (hx - H * 0.09, seat)], np.int32)
            cv2.fillPoly(m, [torso], 255, AA)
            for s in (-1, 1):   # arms holding sticks, moving with the beat
                a = math.radians(-35 + 25 * math.sin(t * 6.3 + s))
                ex, ey = hx + s * H * 0.16, seat - H * 0.22
                cv2.line(m, (int(hx + s * H * 0.1), int(seat - H * 0.34)), (int(ex), int(ey)), 255, max(2, int(H * 0.035)), AA)
                cv2.line(m, (int(ex), int(ey)), (int(ex + s * H * 0.14 * math.cos(a)), int(ey + H * 0.14 * math.sin(a))),
                         255, max(1, int(H * 0.012)), AA)
            # kit: bass drum, toms, cymbals on stands
            cv2.circle(m, (int(cx), int(feet - H * 0.15)), int(H * 0.15), 255, -1, AA)
            for s in (-1, 1):
                cv2.ellipse(m, (int(cx + s * H * 0.2), int(feet - H * 0.32)), (int(H * 0.08), int(H * 0.05)), 0, 0, 360, 255, -1, AA)
                sx = cx + s * H * 0.38
                cv2.line(m, (int(sx), int(feet)), (int(sx), int(feet - H * 0.62)), 255, max(1, int(H * 0.01)), AA)
                cv2.ellipse(m, (int(sx), int(feet - H * 0.62)), (int(H * 0.11), int(H * 0.015)), s * 8, 0, 360, 255, -1, AA)
            return
        hx = cx + sway
        head_y = feet - H * 0.925
        cv2.ellipse(m, (int(hx), int(head_y)), (int(H * 0.058), int(H * 0.07)), 0, 0, 360, 255, -1, AA)
        sh_y, hip_y = feet - H * 0.80, feet - H * 0.46
        cv2.line(m, (int(hx), int(head_y)), (int(hx), int(sh_y)), 255, max(2, int(H * 0.06)), AA)   # neck
        # torso: rounded shoulders tapering to the hips
        torso = np.array([(hx - H * 0.125, sh_y + H * 0.02), (hx - H * 0.09, sh_y - H * 0.012), (hx + H * 0.09, sh_y - H * 0.012),
                          (hx + H * 0.125, sh_y + H * 0.02), (cx + H * 0.095, hip_y), (cx - H * 0.095, hip_y)], np.int32)
        cv2.fillPoly(m, [torso], 255, AA)
        for s_ in (-1, 1):   # legs: thigh + shin, slightly apart, with shoes
            knee = (cx + s_ * H * 0.07, feet - H * 0.24)
            cv2.line(m, (int(cx + s_ * H * 0.05), int(hip_y)), (int(knee[0]), int(knee[1])), 255, max(2, int(H * 0.085)), AA)
            cv2.line(m, (int(knee[0]), int(knee[1])), (int(cx + s_ * H * 0.09), int(feet - H * 0.02)), 255, max(2, int(H * 0.07)), AA)
            cv2.ellipse(m, (int(cx + s_ * H * 0.11), int(feet - H * 0.012)), (int(H * 0.05), int(H * 0.018)), 0, 0, 360, 255, -1, AA)
        aw = max(2, int(H * 0.05))
        if role in ("guitar", "bass"):
            gx, gy = cx + H * 0.02, hip_y - H * 0.02
            cv2.ellipse(m, (int(gx), int(gy)), (int(H * 0.11), int(H * 0.085)), -20, 0, 360, 255, -1, AA)
            L = H * (0.42 if role == "bass" else 0.34)
            ang = math.radians(-28 + 3 * math.sin(t * 1.7))
            nx, ny = gx - L * math.cos(ang), gy + L * math.sin(ang)
            cv2.line(m, (int(gx), int(gy)), (int(nx), int(ny)), 255, max(2, int(H * 0.025)), AA)
            cv2.line(m, (int(hx - H * 0.11), int(sh_y + H * 0.03)), (int(nx + H * 0.06), int(ny + H * 0.01)), 255, aw, AA)
            strum = math.sin(t * 9.0) * H * 0.02
            cv2.line(m, (int(hx + H * 0.11), int(sh_y + H * 0.03)), (int(gx + H * 0.02), int(gy - H * 0.03 + strum)), 255, aw, AA)
        elif role == "singer":
            mx = cx - H * 0.02
            cv2.line(m, (int(mx), int(feet)), (int(mx), int(head_y + H * 0.06)), 255, max(1, int(H * 0.012)), AA)
            cv2.circle(m, (int(mx + H * 0.03), int(head_y + H * 0.06)), max(2, int(H * 0.018)), 255, -1, AA)
            for s in (-1, 1):
                cv2.line(m, (int(hx + s * H * 0.11), int(sh_y + H * 0.03)), (int(mx + s * H * 0.02), int(head_y + H * 0.12)),
                         255, aw, AA)
        elif role == "keys":
            ky = hip_y + H * 0.03
            cv2.rectangle(m, (int(cx - H * 0.24), int(ky - H * 0.03)), (int(cx + H * 0.24), int(ky)), 255, -1, AA)
            for s in (-1, 1):
                cv2.line(m, (int(cx + s * H * 0.2), int(ky)), (int(cx + s * H * 0.12), int(feet)), 255, max(1, int(H * 0.015)), AA)
                cv2.line(m, (int(hx + s * H * 0.11), int(sh_y + H * 0.03)), (int(cx + s * H * 0.12), int(ky - H * 0.03)), 255, aw, AA)

    def band_mask(self, n: int, wall: tuple, t: float) -> np.ndarray:
        """Float mask (h, w) of the band; members left to right in Band order."""
        m = np.zeros((self.h, self.w), np.uint8)
        if n <= 0:
            return m.astype(np.float32)
        x0, y0, x1, y1 = wall
        H = (y1 - y0) * 0.72                      # people are ~70 % of the wall height
        feet = y1 + (self.h - y1) * 0.30
        roles = roles_for(n)
        span = (x1 - x0) * (0.78 if n > 1 else 0.0)
        for i, role in enumerate(roles):
            cx = (x0 + x1) / 2 + (i / (n - 1) - 0.5) * span if n > 1 else (x0 + x1) / 2
            f = feet - (H * 0.08 if role == "drums" else 0.0)     # drum riser
            self._figure(m, cx, f, H * (0.95 if role == "drums" else 1.0), role, t)
        return m.astype(np.float32) / 255.0

    def audience_mask(self) -> np.ndarray:
        key = (self.w, self.h)
        if self._aud_key == key:
            return self._aud
        m = np.zeros((self.h, self.w), np.uint8)
        rng = np.random.default_rng(3)
        base = self.h
        for row in range(3):
            r = self.h * (0.045 - row * 0.008)
            y = base - row * r * 1.1
            x = -r
            while x < self.w + r:
                x += r * rng.uniform(1.4, 2.2)
                hy = y - r * rng.uniform(0.6, 1.4)
                cv2.circle(m, (int(x), int(hy)), int(r), 255, -1, AA)
                cv2.ellipse(m, (int(x), int(hy + r * 2.0)), (int(r * 1.9), int(r * 1.4)), 0, 180, 360, 255, -1, AA)
                if rng.random() < 0.06:   # a raised arm / phone
                    ax = x + r * rng.uniform(-1, 1)
                    cv2.line(m, (int(x), int(hy + r)), (int(ax), int(hy - r * 2.2)), 255, max(2, int(r * 0.35)), AA)
        m[int(base - self.h * 0.02):] = 255
        rows = np.nonzero(m.any(axis=1))[0]
        y0 = int(rows.min()) if len(rows) else self.h - 1
        a = m[y0:].astype(np.float32) / 255.0
        top = np.clip(a - np.roll(a, 3, axis=0), 0, 1)
        self._aud = (a, top, y0)
        self._aud_key = key
        return self._aud

    def _beam(self, rect) -> np.ndarray:
        key = (self.w, self.h, rect)
        if getattr(self, "_beam_key", None) != key:
            W, H = self.w, self.h
            x0, y0, x1, y1 = rect
            D = 4
            beam = np.zeros((H // D, W // D), np.float32)
            pts = np.array([(W * 0.47, H * 1.02), (W * 0.53, H * 1.02), (x1, y1), (x1, y0), (x0, y0), (x0, y1)]) / D
            cv2.fillPoly(beam, [pts.astype(np.int32)], 1.0, AA)
            beam = cv2.GaussianBlur(beam, (0, 0), W / D * 0.01)
            self._beam_img = cv2.resize(beam, (W, H), interpolation=cv2.INTER_LINEAR)
            self._beam_key = key
        return self._beam_img

    # ---- compose --------------------------------------------------------------------------------
    def compose(self, wall_img: np.ndarray, n_members: int, mode: str = "led", t: float = 0.0,
                show_band: bool = True, show_audience: bool = True, house: float = 0.0,
                beat: float = 0.0, env: float = 0.5) -> np.ndarray:
        W, H = self.w, self.h
        wh, ww = wall_img.shape[:2]
        rect = self.wall_rect(wh / ww)
        x0, y0, x1, y1 = rect
        wall = cv2.resize(wall_img, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA).astype(np.float32)
        deck_top, deck_bot = y1, int(y1 + (H - y1) * 0.42)
        out = self._background(deck_top, deck_bot).copy()                    # dark room + stage deck
        mean_col = np.array(cv2.mean(wall)[:3], np.float32)

        if mode == "projector":
            screen = wall * 0.82 + 16.0                                         # grey projector black
            out[y0:y1, x0:x1] = screen
            band = self.band_mask(n_members, rect, t) if show_band else None
            if band is not None:
                # shadows: the band scaled away from the projector (low, in the audience)
                px, py = W / 2, H * 1.08
                M = np.float32([[1.12, 0, px * (1 - 1.12)], [0, 1.12, py * (1 - 1.12)]])
                shadow = cv2.warpAffine(band, M, (W, H))
                shadow = cv2.blur(shadow, (3, 3))
                region = out[y0:y1, x0:x1]
                region *= (1.0 - 0.85 * shadow[y0:y1, x0:x1, None])
                region += 10.0 * shadow[y0:y1, x0:x1, None]
            # beam through the haze (static shape, cached)
            out += self._beam(rect)[..., None] * (mean_col * 0.10 + 2.0)
            out[deck_top:deck_bot] += mean_col * 0.10
            if band is not None:
                # the image lands on the band: bodies show the projected picture (slightly enlarged)
                lit = np.zeros_like(out)
                big = cv2.resize(wall_img, None, fx=(x1 - x0) / ww * 1.06, fy=(y1 - y0) / wh * 1.06,
                                 interpolation=cv2.INTER_AREA).astype(np.float32)
                bh, bw = big.shape[:2]
                cx0, cy0 = int((x0 + x1) / 2 - bw / 2), int((y0 + y1) / 2 - bh / 2)
                sx0, sy0 = max(0, cx0), max(0, cy0)
                sx1, sy1 = min(W, cx0 + bw), min(H, cy0 + bh)
                lit[sy0:sy1, sx0:sx1] = big[sy0 - cy0:sy1 - cy0, sx0 - cx0:sx1 - cx0]
                # below the screen only a little spill reaches the legs
                if sy1 < H:
                    lit[sy1:, sx0:sx1] = mean_col * 0.22
                body = 0.9 * lit + 10.0
                bx0, by0, bx1, by1 = self._bbox(band)
                b3 = band[by0:by1, bx0:bx1, None]
                o = out[by0:by1, bx0:bx1]
                out[by0:by1, bx0:bx1] = o + (body[by0:by1, bx0:bx1] - o) * b3
        else:   # LED wall
            out[y0:y1, x0:x1] = wall
            D = 8   # haze spill is very soft: compute it at 1/8 resolution
            glow_src = np.zeros((max(2, H // D), max(2, W // D), 3), np.float32)
            sx0, sy0, sx1, sy1 = x0 // D, y0 // D, max(x0 // D + 1, x1 // D), max(y0 // D + 1, y1 // D)
            glow_src[sy0:sy1, sx0:sx1] = cv2.resize(wall, (sx1 - sx0, sy1 - sy0), interpolation=cv2.INTER_AREA)
            spill = cv2.GaussianBlur(glow_src, (0, 0), W / D * 0.05) * 0.45 + cv2.GaussianBlur(glow_src, (0, 0), W / D * 0.18) * 0.35
            cv2.add(out, cv2.resize(spill, (W, H), interpolation=cv2.INTER_LINEAR), dst=out)
            # reflection on the stage deck
            dh = deck_bot - deck_top
            if dh > 2:
                refl = cv2.resize(wall[::-1], (x1 - x0, dh), interpolation=cv2.INTER_AREA)
                refl = cv2.resize(cv2.resize(refl, ((x1 - x0) // 4 + 1, dh // 4 + 1), interpolation=cv2.INTER_AREA),
                                  (x1 - x0, dh), interpolation=cv2.INTER_LINEAR)
                fade = np.linspace(0.32, 0.0, dh, dtype=np.float32)[:, None, None]
                out[deck_top:deck_bot, x0:x1] += refl * fade
            if show_band:
                band = self.band_mask(n_members, rect, t)
                # backlight: dark bodies with a rim in the colour of the wall right behind them
                bx0, by0, bx1, by1 = self._bbox(band)
                o = out[by0:by1, bx0:bx1]
                bw_, bh_ = bx1 - bx0, by1 - by0
                small = cv2.resize(o, (max(1, bw_ // 4), max(1, bh_ // 4)), interpolation=cv2.INTER_AREA)
                back = cv2.resize(cv2.GaussianBlur(small, (0, 0), max(1.0, W / 4 * 0.01)), (bw_, bh_), interpolation=cv2.INTER_LINEAR)
                bm = band[by0:by1, bx0:bx1]
                inner = cv2.erode(bm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
                rim = cv2.blur(np.clip(bm - inner, 0, 1), (3, 3))[..., None]
                b3 = bm[..., None]
                body = back * 0.05 + 4.0
                out[by0:by1, bx0:bx1] = o + (body - o) * b3 + back * rim * 0.9

        if house > 0:                   # behind the audience's heads, in front of the stage
            cv2.add(out, self._house_lights(beat, env, house, deck_top), dst=out)

        if show_audience:
            aud, top, ay0 = self.audience_mask()
            # a faint rim on the heads from the stage light
            light = mean_col * (0.35 if mode == "led" else 0.2) + 6.0
            o = out[ay0:]
            out[ay0:] = o + (3.0 - o) * aud[..., None] + top[..., None] * (light * 0.5)
        return cv2.convertScaleAbs(out)

    # ---- house lights ------------------------------------------------------------------------------
    def _house_lights(self, beat: float, env: float, amount: float, deck_top: int) -> np.ndarray:
        """Moving-head beams through the haze, drawn at 1/4 resolution. Each fixture jumps to a new
        random aim on some beats (eased), changes colour every bar or two, and flashes on the beat."""
        W, H = self.w, self.h
        D = 4
        sw, sh = max(2, W // D), max(2, H // D)
        buf = np.zeros((sh, sw, 3), np.float32)
        bi = int(math.floor(beat))
        ph = beat - bi
        kick = math.exp(-ph * 5.0)
        # fixtures: four on the overhead truss, two on the floor at the sides
        fixtures = [(0.14, 0.03, 90.0), (0.38, 0.02, 90.0), (0.62, 0.02, 90.0), (0.86, 0.03, 90.0),
                    (0.02, deck_top / H, -40.0), (0.98, deck_top / H, -140.0)]
        L = math.hypot(W, H) / D * 1.1
        flares = []
        for i, (fx, fy, base) in enumerate(fixtures):
            seg = bi // 2                                  # a new aim every 2 beats, eased in
            a0 = base + (_h01(seg - 1, i, 7) - 0.5) * 110.0
            a1 = base + (_h01(seg, i, 7) - 0.5) * 110.0
            u = min(1.0, ((beat / 2.0) % 1.0) * 2.2)
            u = u * u * (3 - 2 * u)
            ang = math.radians(a0 + (a1 - a0) * u)
            on = _h01(bi, i, 3) > 0.3                     # some fixtures sit out a beat
            if not on:
                continue
            col = HOUSE_COLORS[int(_h01(bi // 8, i, 11) * len(HOUSE_COLORS)) % len(HOUSE_COLORS)]
            lvl = amount * (0.35 + 0.65 * kick) * (0.6 + 0.6 * env)
            sx, sy = fx * sw, fy * sh
            dx, dy = math.cos(ang), math.sin(ang)
            nx, ny = -dy, dx
            # the cone fades along its length: a few quads, dimmer further away; a hot core inside
            n = 7
            for spread, gain in ((math.radians(3.0), 48.0), (math.radians(1.1), 70.0)):
                tn = math.tan(spread)
                for k in range(n):
                    d0, d1 = L * k / n, L * (k + 1) / n
                    w0, w1 = 0.8 + d0 * tn, 0.8 + d1 * tn
                    quad = np.array([[sx + dx * d0 + nx * w0, sy + dy * d0 + ny * w0],
                                     [sx + dx * d1 + nx * w1, sy + dy * d1 + ny * w1],
                                     [sx + dx * d1 - nx * w1, sy + dy * d1 - ny * w1],
                                     [sx + dx * d0 - nx * w0, sy + dy * d0 - ny * w0]])
                    f = lvl * gain * (1.0 - k / n) ** 1.3
                    cv2.fillConvexPoly(buf, np.round(quad * 16).astype(np.int32),
                                       (col[2] * f / 255.0, col[1] * f / 255.0, col[0] * f / 255.0), AA, 4)
            flares.append((sx, sy, col, lvl))
        buf = cv2.GaussianBlur(buf, (0, 0), 1.1)
        for sx, sy, col, lvl in flares:             # the lens of each fixture
            c = tuple(min(255.0, v * lvl * 1.4) for v in (col[2], col[1], col[0]))
            cv2.circle(buf, (int(round(sx * 16)), int(round(sy * 16))), int(3.5 * 16), c, -1, AA, 4)
        buf = cv2.GaussianBlur(buf, (0, 0), 0.8)
        return cv2.resize(buf, (W, H), interpolation=cv2.INTER_LINEAR)

    def _bbox(self, mask) -> tuple:
        ys, xs = np.nonzero(mask[::4, ::4])
        if len(xs) == 0:
            return 0, 0, 1, 1
        pad = 8
        return (max(0, xs.min() * 4 - pad), max(0, ys.min() * 4 - pad),
                min(self.w, xs.max() * 4 + pad + 4), min(self.h, ys.max() * 4 + pad + 4))

    def _background(self, deck_top, deck_bot) -> np.ndarray:
        key = (self.w, self.h, deck_top, deck_bot)
        if getattr(self, "_bg_key", None) != key:
            yy = np.linspace(0, 1, self.h, dtype=np.float32)[:, None, None]
            bg = np.broadcast_to(np.array([7, 6, 6], np.float32) * (0.6 + 0.4 * yy), (self.h, self.w, 3)).copy()
            bg[deck_top:deck_bot] = np.array([14, 13, 13], np.float32)
            self._bg, self._bg_key = bg, key
        return self._bg
