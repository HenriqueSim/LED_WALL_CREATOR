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


def _find_ffmpeg():
    """System ffmpeg if installed, else the one shipped with the imageio-ffmpeg package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = _find_ffmpeg()
# no console window flashing up for ffmpeg calls on Windows
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
N_BANDS = 6
TEMPO_MIN, TEMPO_MAX = 55.0, 210.0


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
    proc = subprocess.run(cmd, capture_output=True, check=False, **NO_WINDOW)
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


def _grid_scores(env: np.ndarray, fps: float, bpms: np.ndarray, phase_step: float = 0.5):
    """How well a rigid beat grid fits the onset curve, for each tempo: the mean onset strength at
    the grid's beats, at its best phase. Returns (scores, best phase in frames)."""
    n = len(env)
    x = np.arange(n, dtype=np.float64)
    scores, phases = np.empty(len(bpms)), np.empty(len(bpms))
    for i, b in enumerate(bpms):
        p = fps * 60.0 / b
        k = np.arange(max(1, int((n - 1 - p) / p)))
        ph = np.arange(0.0, p, phase_step)
        s = np.interp(ph[:, None] + k[None, :] * p, x, env).mean(axis=1)
        j = int(s.argmax())
        scores[i], phases[i] = s[j], ph[j]
    return scores, phases


def refine_tempo(y: np.ndarray, sr: int, coarse_bpm: float) -> tuple[float, float]:
    """Exact tempo and bar-1 position (coarse_bpm = librosa's guess, one of the candidates).

    librosa's beat tracker measures the beat period in whole
    analysis frames (23 ms), so it can only answer 117.45, 123.05, 129.20… and a song at 120 BPM
    comes out 2.5 % fast, drifting a whole beat every ~40 beats. Here a rigid grid is slid over the
    whole song's onset curve (5.8 ms frames, half-frame phase steps). Candidate tempos (tempogram
    peaks, their double / half, librosa's guess) are each scored with a prior favouring typical band
    tempos, and the winner is searched in 0.005 BPM steps; results within a hair of a whole number snap to it
    (most songs are recorded to a click). Bar 1 is then the beat of the four that carries the most
    kick / bass."""
    import librosa
    hop = 128
    fps = sr / hop
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    env = np.maximum(env - np.convolve(env, np.ones(64) / 64, "same"), 0.0)   # drop the slow swell
    if len(env) < fps * 4 or not env.any():
        return coarse_bpm, 0.0
    # candidates: the strongest tempogram peaks, their double / half, and the tracker's guess
    tg = librosa.feature.tempogram(onset_envelope=env, sr=sr, hop_length=hop, win_length=1024).mean(axis=1)
    freqs = librosa.tempo_frequencies(len(tg), sr=sr, hop_length=hop)
    peaks = [i for i in range(1, len(tg) - 1)
             if TEMPO_MIN / 2 <= freqs[i] <= TEMPO_MAX * 2 and tg[i] >= tg[i - 1] and tg[i] >= tg[i + 1]]
    peaks = sorted(peaks, key=lambda i: -tg[i])[:5]
    cands = []
    for c in [coarse_bpm] + [float(freqs[i]) for i in peaks]:
        for m in (1.0, 2.0, 0.5):
            b = c * m
            if TEMPO_MIN <= b <= TEMPO_MAX and all(abs(b / o - 1) > 0.02 for o in cands):
                cands.append(b)
    best = None
    for c in cands:
        bpms = np.arange(c * 0.97, c * 1.03, 0.1)
        s, _ = _grid_scores(env, fps, bpms)
        b = float(bpms[int(s.argmax())])
        # a grid at half speed hits only the accented beats, so it always fits a bit better; the
        # prior (centred on 130 BPM, ±0.6 octave) is what picks 170 over 85 or 120 over 60
        score = float(s.max()) * math.exp(-0.5 * (math.log2(b / 130.0) / 0.6) ** 2)
        if best is None or score > best[0]:
            best = (score, b)
    c = best[1]
    bpms = np.arange(c - 0.15, c + 0.15, 0.005)
    s, ph = _grid_scores(env, fps, bpms)
    j = int(s.argmax())
    bpm, phase = float(bpms[j]), float(ph[j])
    whole = round(bpm)
    d = abs(whole - bpm)
    if d <= 0.15:   # within 0.05 always (≈10 ms over a song); up to 0.15 if the fit is as good
        sw, phw = _grid_scores(env, fps, np.array([float(whole)]))
        if d <= 0.05 or sw[0] >= 0.985 * s[j]:
            bpm, phase = float(whole), float(phw[0])
    # bar 1: the beat (of 4) with the strongest low-end onsets over the song
    p = fps * 60.0 / bpm
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=40, fmax=8000)
    low = librosa.onset.onset_strength(S=librosa.power_to_db(mel[:4]), sr=sr, hop_length=hop)
    low = low[:len(env)]
    x = np.arange(len(low), dtype=np.float64)
    k = np.arange(max(1, int((len(low) - 1 - phase) / (4 * p))))
    downbeat = [np.interp(phase + i * p + k * 4 * p, x, low).mean() for i in range(4)]
    bar1 = (phase + int(np.argmax(downbeat)) * p) / fps
    return round(bpm, 3), float(bar1)


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
    coarse = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    if not (40.0 <= coarse <= 250.0):
        coarse = 120.0
    progress(45, "Refining tempo…")
    bpm, bar1 = refine_tempo(y, sr, coarse)
    period = 60.0 / bpm
    beat_times = bar1 + period * np.arange(max(0, int((duration - bar1) / period) + 1))

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
