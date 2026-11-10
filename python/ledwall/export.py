"""Video export: cv2.VideoWriter (+ optional ffmpeg high-bitrate H.264 encode and audio mux)."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from .audio import FFMPEG, NO_WINDOW
from .engine import MotionEngine
from .renderer import FrameRenderer


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
                 use_ffmpeg: bool, include_audio: bool, bitrate_mbps: int, parent=None):
        super().__init__(parent)
        self.engine = MotionEngine(copy.deepcopy(song), analysis)   # immune to edits during the render
        self.width, self.height, self.fps = width, height, fps
        self.out_path = out_path
        self.use_ffmpeg = use_ffmpeg and FFMPEG is not None
        self.include_audio = include_audio
        self.bitrate = bitrate_mbps
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        tmp_dir = None
        try:
            eng = self.engine
            start, end = eng.export_range()
            n_frames = max(1, int(round((end - start) * self.fps)))
            renderer = FrameRenderer(self.width, self.height)
            size = (self.width, self.height)
            if self.use_ffmpeg:
                tmp_dir = tempfile.mkdtemp(prefix="ledwall_")
                video_path = os.path.join(tmp_dir, "frames.avi")
                writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"MJPG"), self.fps, size)
                if not writer.isOpened():
                    raise RuntimeError("Could not open MJPG intermediate writer")
                writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)
                codec = "MJPG→H.264"
            else:
                video_path = self.out_path
                writer, codec = open_video_writer(video_path, self.fps, size)

            scale = 90 if self.use_ffmpeg else 100
            for i in range(n_frames):
                if self._cancel:
                    writer.release()
                    raise RuntimeError("Export cancelled")
                t = start + i / self.fps
                writer.write(renderer.render(eng.state_at(t), eng.song))
                if i % 5 == 0 or i == n_frames - 1:
                    self.progress.emit(int(scale * (i + 1) / n_frames), f"Rendering frame {i + 1}/{n_frames} ({codec})")
            writer.release()

            if self.use_ffmpeg:
                self.progress.emit(92, "Encoding H.264 with ffmpeg…")
                br = self.bitrate
                audio = self.include_audio and eng.analysis is not None
                cmd = [FFMPEG, "-y", "-v", "error", "-i", video_path]
                if audio:
                    cmd += ["-ss", f"{start:.4f}", "-t", f"{end - start:.4f}", "-i", eng.analysis.path]
                cmd += ["-map", "0:v:0"]
                if audio:
                    cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "320k", "-shortest"]
                cmd += ["-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-pix_fmt", "yuv420p",
                        "-b:v", f"{br}M", "-maxrate", f"{int(br * 1.5)}M", "-bufsize", f"{br * 2}M",
                        "-r", str(self.fps), "-movflags", "+faststart", self.out_path]
                proc = subprocess.run(cmd, capture_output=True, **NO_WINDOW)
                if proc.returncode != 0:
                    raise RuntimeError("ffmpeg failed:\n" + proc.stderr.decode(errors="ignore")[-2000:])
            self.progress.emit(100, "Done")
            self.finished_ok.emit(self.out_path)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
