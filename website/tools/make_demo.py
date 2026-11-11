"""Builds the website's demo with the app itself: a 10-bar (~18.6 s) LED wall show on a no-copyright
percussion track, then renders the videos and posters the site uses.

    cd python && .venv/bin/python ../website/tools/make_demo.py "<song.mp3>" [--sheet]

Writes website/media/: wall.mp4 (with audio), stage-led.mp4, stage-projector.mp4, *.jpg posters,
and website/tools/demo.ledwall (the project, so it can be opened and tweaked in the app).
--sheet only writes a contact sheet of frames (website/tools/sheet.jpg) to check the look.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "python"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from ledwall import project  # noqa: E402
from ledwall.audio import FFMPEG, analyse_audio  # noqa: E402
from ledwall.engine import MotionEngine  # noqa: E402
from ledwall.model import (  # noqa: E402
    Clip, LyricClip, Note, Pattern, default_song, el_lane, new_id, preset_element,
)
from ledwall.renderer import FrameRenderer  # noqa: E402
from ledwall.resources import load_bundled_fonts  # noqa: E402
from ledwall.stage import StageSimulator  # noqa: E402

MEDIA = os.path.join(ROOT, "website", "media")
FIRST_BAR, BARS = 20, 10           # bars 20..29 of the track: groove, breakdown, drop, chorus, build, peak
FPS = 30


def build_song(song_path: str, analysis):
    s = default_song()             # keeps its six members and the Left side / Right side groups
    s.audio_path = os.path.abspath(song_path)
    s.bpm, s.beat_offset = analysis.bpm, analysis.detected_offset
    left, right = s.groups[0].id, s.groups[1].id
    lk = s.look
    lk.main_rgb, lk.accent_rgb = (255, 168, 118), (176, 120, 255)
    lk.logo_text, lk.logo_font, lk.logo_layout, lk.logo_color = "your band", "Anton", "line", "cutout"
    lk.logo_size, lk.logo_y, lk.logo_always = 0.27, 0.6, True
    lk.glow_intensity = 0.75
    lk.logo_peak, lk.logo_glow = 1.4, 1.2
    lk.lyrics_size = 0.09

    sun = preset_element("sun"); sun.size = 0.58; sun.idle = 0.5
    haze = preset_element("haze"); haze.glow = 2.2
    beam = preset_element("beam"); beam.count = 6; beam.y = 0.93; beam.size = 0.86; beam.haze = 0.7; beam.idle = 0.15
    hexa = preset_element("hexagon"); hexa.size = 0.4; hexa.y = 0.58; hexa.thickness = 5; hexa.spin = 3.0
    hexa.color = "accent"; hexa.haze = 0.45; hexa.idle = 0.0
    bars = preset_element("bars"); bars.count = 16; bars.size = 0.98; bars.height = 0.36; bars.gap = 0.9
    bars.glow = 0.12; bars.haze = 0.15; bars.idle = 0.0
    rays = preset_element("custom_rays"); rays.size = 0.5; rays.y = 0.985; rays.haze = 0.35; rays.idle = 0.0
    s.elements = [rays, beam, hexa, sun, bars, haze]          # top of the list = front
    E = {"sun": sun, "haze": haze, "beam": beam, "hexa": hexa, "bars": bars, "rays": rays}

    def n(el, sub, start, length, vel=1.0):
        return Note(el_lane(E[el].id, sub), start, length, vel)

    def fx(key, start, length, vel=1.0):
        return Note(f"fx:{key}", start, length, vel)

    every = lambda names: [E[k].id for k in names]
    pats = []

    def pat(name, bars_, color, names, notes, speed=1.0):
        p = Pattern(new_id(), name, bars_, color, notes, every(names), speed)
        pats.append(p)
        return p

    groove = pat("Groove", 1, (255, 170, 60), ["sun", "haze", "beam", "bars"], [
        n("sun", "all", 0, 16, 0.85), n("haze", "all", 0, 16, 0.8), n("beam", "pulse", 0, 16, 0.85),
        n("bars", "random", 0, 16, 0.45), fx("logo_glow", 0, 2, 0.8)])
    brk = pat("Breakdown (half time)", 3, (90, 170, 255), ["sun", "haze", "hexa", "rays"], [
        n("sun", "pulse", 0, 48, 1.0), n("haze", "all", 0, 48, 1.0),
        n("hexa", "all", 4, 12, 0.25), n("hexa", "all", 16, 16, 0.5), n("hexa", "all", 32, 12, 0.8),
        n("rays", "chase", 16, 28, 0.9), fx("logo_glow", 32, 12, 1.0), fx("blackout", 44, 4, 1.0)], speed=0.5)
    drop = pat("Drop", 1, (255, 90, 120), ["sun", "haze", "beam", "hexa", "bars", "rays"], [
        fx("flash", 0, 1, 1.0), fx("sparkle", 0, 16, 0.9), fx("logo_glow", 0, 1, 1.0),
        n("sun", "all", 0, 16, 0.8), n("haze", "all", 0, 16, 1.0), n("beam", "chase", 0, 16, 1.0),
        n("hexa", "pulse", 0, 16, 1.0), n("bars", "all", 0, 16, 0.55), n("rays", "all", 0, 16, 0.85)], speed=2.0)
    chorus_notes = [n("sun", "all", 0, 16, 0.8), n("haze", "all", 0, 16, 0.9), n("hexa", "all", 0, 16, 0.75),
                    n("bars", "chase", 0, 16, 0.6), n("rays", "pulse", 0, 16, 0.7), fx("sparkle", 0, 16, 0.35)]
    for st in (0, 8):
        chorus_notes += [n("beam", f"g:{left}", st, 4, 1.0), n("beam", f"g:{right}", st + 4, 4, 1.0),
                         fx("logo_glow", st, 1, 0.9)]
    chorus = pat("Chorus", 1, (255, 200, 90), ["sun", "haze", "beam", "hexa", "bars", "rays"], chorus_notes)
    build = pat("Build (double time)", 1, (120, 230, 140), ["sun", "haze", "beam", "hexa", "bars", "rays"], [
        n("sun", "all", 0, 16, 0.9), n("haze", "all", 0, 16, 1.0), n("beam", "strobe", 8, 8, 1.0),
        n("beam", "chase", 0, 8, 1.0), n("hexa", "random", 0, 16, 1.0), n("bars", "chase", 0, 16, 0.65),
        n("rays", "random", 0, 16, 0.8), fx("logo_glow", 8, 8, 1.0)], speed=2.0)
    peak = pat("Peak", 1, (200, 120, 255), ["sun", "haze", "beam", "hexa", "bars", "rays"], [
        fx("flash", 0, 1, 1.0), fx("sparkle", 0, 16, 0.6), fx("logo_glow", 0, 1, 1.0),
        n("sun", "all", 0, 16, 0.8), n("haze", "all", 0, 16, 1.0), n("beam", "all", 0, 16, 1.0),
        n("hexa", "pulse", 0, 16, 1.0), n("bars", "random", 0, 16, 0.65), n("rays", "all", 0, 16, 1.0)])
    s.patterns = pats
    b0 = (FIRST_BAR - 1) * 4
    order = [(groove, 4), (brk, 12), (drop, 4), (chorus, 12), (build, 4), (peak, 4)]
    s.clips, at = [], b0
    for p, length in order:
        s.clips.append(Clip(new_id(), p.id, 0, float(at), float(length)))
        at += length
    s.lyrics = [LyricClip(new_id(), "made for the stage", b0 + 6, 9.5)]
    eng = MotionEngine(s, analysis)
    s.region = (eng.b2t(b0), eng.b2t(b0 + BARS * 4))
    s.loop = True
    return s


def _ffmpeg(path, w, h, audio=None, t0=0.0, dur=0.0, crf=21):
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(FPS),
           "-i", "-"]
    if audio:
        cmd += ["-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", audio, "-map", "0:v", "-map", "1:a",
                "-c:a", "aac", "-b:a", "160k", "-af", f"afade=t=in:d=0.04,afade=t=out:st={dur - 0.08:.3f}:d=0.08"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-movflags", "+faststart", path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render(song, analysis, name, w, h, view="wall", audio=False):
    eng = MotionEngine(song, analysis)
    t0, t1 = song.region
    n = int(round((t1 - t0) * FPS))
    local = threading.local()

    def frame(i):
        t = t0 + i / FPS
        st = eng.state_at(t)
        if view == "wall":
            r = getattr(local, "r", None) or FrameRenderer(w, h)
            local.r = r
            return r.render(st, song)
        sim = getattr(local, "sim", None)
        if sim is None:
            sim = local.sim = StageSimulator(w, h)
            ww, wh = sim.wall_size(1080 / 3840)
            local.r = FrameRenderer(ww, wh)
        return sim.compose(local.r.render(st, song), len(song.members), view, t, True, True,
                           house=1.0, beat=st.beat, env=st.env)

    out = os.path.join(MEDIA, f"{name}.mp4")
    proc = _ffmpeg(out, w, h, song.audio_path if audio else None, t0, t1 - t0)
    poster = None
    with ThreadPoolExecutor(4) as pool:
        for i, img in enumerate(pool.map(frame, range(n))):
            proc.stdin.write(memoryview(np.ascontiguousarray(img)).cast("B"))
            if i == int(n * 0.36):              # the drop: a good still for the poster
                poster = img
    proc.stdin.close()
    proc.wait()
    cv2.imwrite(os.path.join(MEDIA, f"{name}.jpg"), poster, [cv2.IMWRITE_JPEG_QUALITY, 82])
    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB, {n} frames")


def sheet(song, analysis):
    eng = MotionEngine(song, analysis)
    r = FrameRenderer(960, 270)
    t0, t1 = song.region
    rows = []
    for k in range(10):
        row = [r.render(eng.state_at(t0 + (k + f) * (t1 - t0) / 10), song) for f in (0.02, 0.5)]
        rows.append(np.hstack(row))
    cv2.imwrite(os.path.join(HERE, "sheet.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    load_bundled_fonts()
    song_path = sys.argv[1]
    a = analyse_audio(song_path)
    song = build_song(song_path, a)
    if "--sheet" in sys.argv:
        sheet(song, a)
        return
    os.makedirs(MEDIA, exist_ok=True)
    keep = song.audio_path
    song.audio_path = os.path.basename(keep)      # no personal paths in the saved project
    project.save(song, os.path.join(HERE, "demo.ledwall"))
    song.audio_path = keep
    render(song, a, "wall", 2560, 720, "wall", audio=True)
    render(song, a, "stage-led", 1600, 900, "led")
    render(song, a, "stage-projector", 1600, 900, "projector")


if __name__ == "__main__":
    main()
