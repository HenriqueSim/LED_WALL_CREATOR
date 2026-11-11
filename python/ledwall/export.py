"""Video export.

Frames are rendered on several threads at once (each with its own FrameRenderer; OpenCV and NumPy
release the GIL) and piped straight into ffmpeg, which encodes them on the graphics card when it
can (NVIDIA NVENC, Apple VideoToolbox, Intel Quick Sync, AMD AMF) and with x264 otherwise.
Without ffmpeg, OpenCV's VideoWriter is used.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from .audio import FFMPEG, NO_WINDOW
from .engine import MotionEngine
from .renderer import FrameRenderer

# (ffmpeg encoder, label, extra options) tried in order; the first one that works on this computer wins
_GPU_ENCODERS = {
    "darwin": [("h264_videotoolbox", "Apple VideoToolbox (graphics chip)", ["-allow_sw", "0"])],
    "win32": [("h264_nvenc", "NVIDIA NVENC (graphics card)", ["-preset", "p5", "-tune", "hq"]),
              ("h264_qsv", "Intel Quick Sync (graphics chip)", ["-preset", "slow"]),
              ("h264_amf", "AMD AMF (graphics card)", ["-quality", "quality"])],
    "linux": [("h264_nvenc", "NVIDIA NVENC (graphics card)", ["-preset", "p5", "-tune", "hq"]),
              ("h264_qsv", "Intel Quick Sync (graphics chip)", ["-preset", "slow"])],
}
_SOFTWARE = ("libx264", "x264 (processor)", ["-preset", "medium"])
_encoder_cache: dict = {}


def _platform() -> str:
    return "darwin" if sys.platform == "darwin" else "win32" if sys.platform.startswith("win") else "linux"


def _encoder_works(name: str, opts: list) -> bool:
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.2",
           "-c:v", name] + opts + ["-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20, **NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_encoder(prefer_gpu: bool = True) -> tuple:
    """(ffmpeg encoder, label, options) for H.264 on this computer (probed once, then cached)."""
    if FFMPEG is None:
        return None
    key = bool(prefer_gpu)
    if key not in _encoder_cache:
        found = _SOFTWARE
        if prefer_gpu:
            for cand in _GPU_ENCODERS.get(_platform(), []):
                if _encoder_works(cand[0], cand[2]):
                    found = cand
                    break
        _encoder_cache[key] = found
    return _encoder_cache[key]


def render_threads() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))     # more threads don't help (memory bound)


def open_video_writer(path: str, fps: float, size: tuple):
    """Try H.264 first, then MPEG-4 Part 2."""
    for code in ("avc1", "H264", "mp4v"):
        wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*code), fps, size)
        if wr.isOpened():
            try:
                wr.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)
            except Exception:
                pass
            return wr, code
        wr.release()
    raise RuntimeError("OpenCV could not open a VideoWriter for MP4 (no avc1/mp4v encoder).")


class ExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, song, analysis, width: int, height: int, fps: int, out_path: str,
                 use_ffmpeg: bool, include_audio: bool, bitrate_mbps: int, parent=None, prefer_gpu: bool = True,
                 threads: int | None = None):
        super().__init__(parent)
        self.engine = MotionEngine(copy.deepcopy(song), analysis)   # immune to edits during the render
        self.width, self.height, self.fps = width, height, fps
        self.out_path = out_path
        self.use_ffmpeg = use_ffmpeg and FFMPEG is not None
        self.include_audio = include_audio
        self.bitrate = bitrate_mbps
        self.prefer_gpu = prefer_gpu
        self.threads = threads or render_threads()
        self.encoder_label = ""
        self._cancel = False

    def cancel(self):
        self._cancel = True

    # ---- frames -----------------------------------------------------------------------------
    def _frames(self, n_frames: int, start: float):
        """Yield the frames in order, rendering up to 2 x threads ahead on a pool."""
        local = threading.local()
        eng, song = self.engine, self.engine.song
        w, h, fps = self.width, self.height, self.fps

        def render(i):
            r = getattr(local, "r", None)
            if r is None:
                r = local.r = FrameRenderer(w, h)
            return r.render(eng.state_at(start + i / fps), song)

        with ThreadPoolExecutor(self.threads) as pool:
            pending: deque = deque()
            nxt = 0
            for i in range(n_frames):
                while nxt < n_frames and len(pending) < self.threads * 2:
                    pending.append(pool.submit(render, nxt))
                    nxt += 1
                if self._cancel:
                    for f in pending:
                        f.cancel()
                    raise RuntimeError("Export cancelled")
                yield pending.popleft().result()

    # ---- encoders ---------------------------------------------------------------------------
    def _ffmpeg_cmd(self, start: float, end: float, enc: tuple) -> list:
        name, _label, opts = enc
        br = self.bitrate
        eng = self.engine
        audio = self.include_audio and eng.analysis is not None
        cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{self.width}x{self.height}", "-r", str(self.fps), "-i", "-"]
        if audio:
            cmd += ["-ss", f"{start:.4f}", "-t", f"{end - start:.4f}", "-i", eng.analysis.path]
        cmd += ["-map", "0:v:0"]
        if audio:
            cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "320k", "-shortest"]
        cmd += ["-c:v", name] + opts
        if name == "libx264":
            cmd += ["-profile:v", "high"]
        cmd += ["-pix_fmt", "yuv420p", "-b:v", f"{br}M", "-maxrate", f"{int(br * 1.5)}M", "-bufsize", f"{br * 2}M",
                "-movflags", "+faststart", self.out_path]
        return cmd

    def run(self):
        proc = None
        try:
            eng = self.engine
            start, end = eng.export_range()
            n_frames = max(1, int(round((end - start) * self.fps)))
            if self.use_ffmpeg:
                self.progress.emit(0, "Finding the fastest encoder…")
                enc = pick_encoder(self.prefer_gpu)
                self.encoder_label = enc[1]
                proc = subprocess.Popen(self._ffmpeg_cmd(start, end, enc), stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **NO_WINDOW)
                errbuf: list = []
                threading.Thread(target=lambda: errbuf.append(proc.stderr.read()), daemon=True).start()

                def write(frame):
                    try:
                        proc.stdin.write(memoryview(frame).cast("B"))
                    except (BrokenPipeError, OSError):
                        proc.wait(10)
                        raise RuntimeError("ffmpeg stopped:\n" + (errbuf[0] if errbuf else b"").decode(errors="ignore")[-2000:])
                finish = None
            else:
                writer, code = open_video_writer(self.out_path, self.fps, (self.width, self.height))
                self.encoder_label = f"OpenCV {code}"
                write = writer.write
                finish = writer.release
            label = f"{self.encoder_label}, {self.threads} render threads"
            for i, frame in enumerate(self._frames(n_frames, start)):
                write(frame)
                if i % 5 == 0 or i == n_frames - 1:
                    self.progress.emit(int(98 * (i + 1) / n_frames), f"Frame {i + 1}/{n_frames} · {label}")
            if proc is not None:
                proc.stdin.close()
                if proc.wait() != 0:
                    raise RuntimeError("ffmpeg failed:\n" + (errbuf[0] if errbuf else b"").decode(errors="ignore")[-2000:])
                proc = None
            else:
                finish()
            self.progress.emit(100, "Done")
            self.finished_ok.emit(self.out_path)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if proc is not None:        # cancelled or failed: stop ffmpeg and drop the partial file
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                proc.kill()
                proc.wait()
                try:
                    os.remove(self.out_path)
                except OSError:
                    pass
