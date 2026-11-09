"""Audio decoding + librosa analysis (BPM, beat grid, energy envelopes) and its QThread."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

ANALYSIS_SR = 22050
HOP = 512
FFMPEG = shutil.which("ffmpeg")
N_BANDS = 6


@dataclass
class AudioAnalysis:
    path: str
    duration: float
    peaks_min: np.ndarray
    peaks_max: np.ndarray
    feat_times: np.ndarray
    env: np.ndarray            # overall energy 0..1
    bands: np.ndarray          # (N_BANDS, N) low -> high frequency energy 0..1
    bpm: float
    beat_times: np.ndarray

    @property
    def detected_offset(self) -> float:
        if not self.beat_times.size:
            return 0.0
        return float(self.beat_times[0] % (60.0 / self.bpm))


def _decode_ffmpeg(path: str, sr: int) -> np.ndarray:
    if not FFMPEG:
        raise RuntimeError("ffmpeg not found (needed to decode this file type)")
    cmd = [FFMPEG, "-v", "error", "-i", path, "-vn", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode(errors="ignore").strip() or "ffmpeg decode failed")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def load_audio_mono(path: str, sr: int = ANALYSIS_SR) -> np.ndarray:
    """WAV/MP3/FLAC via librosa (soundfile); MP4/M4A/video containers via ffmpeg."""
    import librosa
    ext = os.path.splitext(path)[1].lower()
    order = ("ffmpeg", "librosa") if ext in (".mp4", ".m4a", ".mov", ".aac", ".mkv", ".webm") else ("librosa", "ffmpeg")
    errors = []
    for method in order:
        try:
            y = librosa.load(path, sr=sr, mono=True)[0] if method == "librosa" else _decode_ffmpeg(path, sr)
            if y.size:
                return y.astype(np.float32)
        except Exception as exc:
            errors.append(f"{method}: {exc}")
    raise RuntimeError("Could not decode audio.\n" + "\n".join(errors))


def _norm(x: np.ndarray, lo_pct=5.0, hi_pct=98.0) -> np.ndarray:
    lo, hi = np.percentile(x, lo_pct), np.percentile(x, hi_pct)
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0).astype(np.float32)


def _attack_release(x: np.ndarray, fps: float, attack=0.01, release=0.18) -> np.ndarray:
    a = math.exp(-1.0 / (attack * fps))
    r = math.exp(-1.0 / (release * fps))
    out = np.empty_like(x)
    v = 0.0
    for i, s in enumerate(x):
        c = a if s > v else r
        v = c * v + (1 - c) * s
        out[i] = v
    return out


def analyse_audio(path: str, progress=lambda p, m: None) -> AudioAnalysis:
    import librosa
    progress(5, "Decoding audio…")
    y = load_audio_mono(path)
    sr = ANALYSIS_SR
    duration = len(y) / sr

    progress(20, "Building waveform…")
    n_bins = int(min(12000, max(1, len(y))))
    usable = (len(y) // n_bins) * n_bins
    blocks = y[:usable].reshape(n_bins, -1)
    peaks_min, peaks_max = blocks.min(axis=1), blocks.max(axis=1)

    progress(35, "Beat tracking…")
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP)
    bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    if not (40.0 <= bpm <= 250.0):
        bpm = 120.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)

    progress(60, "Energy envelopes…")
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=60, fmax=11000)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    n = min(len(rms), len(onset), mel_db.shape[1])
    fps = sr / HOP
    env = _attack_release(np.clip(0.65 * _norm(rms[:n]) + 0.35 * _norm(onset[:n]), 0, 1), fps)
    bands = mel_db[:, :n].reshape(N_BANDS, 60 // N_BANDS, n).mean(axis=1)
    bands = np.stack([_attack_release(_norm(b), fps, 0.01, 0.12) for b in bands])
    feat_times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=HOP)
    progress(100, "Done")
    return AudioAnalysis(path, duration, peaks_min, peaks_max, feat_times, env.astype(np.float32),
                         bands.astype(np.float32), bpm, beat_times)


class AudioAnalysisWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: str, parent: QObject | None = None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.finished_ok.emit(analyse_audio(self.path, lambda p, m: self.progress.emit(p, m)))
        except Exception as exc:
            self.failed.emit(str(exc))
