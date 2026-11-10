#!/usr/bin/env python3
# =============================================================================
#  LED Wall Motion Generator
#  DAW-style light programming for LED wall backgrounds, rendered to .mp4
# =============================================================================
#
#  Setup:
#  ---------------------------------------------------------------------------
#    python3 -m venv .venv && source .venv/bin/activate
#    pip install -r requirements.txt   (PyQt6 opencv-python numpy librosa soundfile)
#
#    # Optional but recommended (MP4/M4A audio import, H.264 high-bitrate
#    # encode and audio muxing on export):
#    #   Linux:   sudo apt install ffmpeg      macOS: brew install ffmpeg
#
#    python led_wall_motion_generator.py [project.json | audio file]
#  ---------------------------------------------------------------------------
#
#  Workflow (full guide in README.md)
#    Look & Song        audio, BPM, one colour per song, band name layer
#    Band               members (left -> right on stage) and groups
#    Elements           the shapes on the wall (sun, beam, hexagon, bars...)
#    Pattern Editor     one folder of lanes per element (All, members, groups,
#                       Pulse / Chase / Random / Strobe) + global lanes; notes
#                       bring elements in and out of the scene
#    Master Track       place / copy / stretch pattern clips over the waveform
#    Export             3840x1080 / 1920x1080 / custom, 30/60 fps, seamless loop
#
#  Package layout (ledwall/):
#    model.py       song data (members, groups, patterns, clips, lyrics, look)
#    audio.py       decoding + librosa analysis thread
#    engine.py      time -> FrameState (pattern/clip evaluation, envelopes, FX)
#    renderer.py    FrameState -> frame (designs, bloom, band name, lyrics)
#    export.py      VideoWriter / ffmpeg export thread
#    controller.py  undo/redo + change notifications
#    gui/           one module per tab + main window
#
#  Fonts: fonts/*.ttf (SIL Open Font License, see fonts/README.md), loaded at start
# =============================================================================

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PyQt6.QtWidgets import QApplication  # noqa: E402


def selftest() -> int:
    """`--selftest`: exercise a packaged build end to end (window, fonts, audio analysis, rendering,
    stage view, video export) without user interaction. Prints SELFTEST OK and returns 0 on success."""
    import tempfile
    import traceback
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    try:
        import numpy as np
        import soundfile as sf
        from ledwall.audio import FFMPEG, analyse_audio
        from ledwall.engine import MotionEngine
        from ledwall.export import ExportWorker
        from ledwall.gui.main_window import MainWindow
        from ledwall.model import default_song
        from ledwall.renderer import FrameRenderer
        from ledwall.resources import load_bundled_fonts
        from ledwall.stage import StageSimulator
        fonts = load_bundled_fonts()
        print("fonts:", ", ".join(fonts))
        print("ffmpeg:", FFMPEG)
        import cv2
        print("opencv:", cv2.__version__, getattr(cv2, "__file__", ""))
        tmp = tempfile.mkdtemp()
        sr = 22050                                   # 6 s of clicks at 120 BPM
        y = np.zeros(sr * 6, np.float32)
        for k in range(12):
            i = int(k * 0.5 * sr)
            y[i:i + 800] += np.sin(np.arange(800) * 0.3).astype(np.float32) * np.linspace(1, 0, 800, dtype=np.float32)
        wav = os.path.join(tmp, "clicks.wav")
        sf.write(wav, y, sr)
        a = analyse_audio(wav)
        print(f"audio analysis: {a.duration:.1f}s, {a.bpm:.1f} BPM")
        song = default_song()
        eng = MotionEngine(song, a)
        frame = FrameRenderer(960, 270).render(eng.state_at(2.0), song)
        stage = StageSimulator(640, 360)
        stage.compose(frame, len(song.members), "led", 2.0)
        stage.compose(frame, len(song.members), "projector", 2.0)
        win = MainWindow()
        win.show()
        app.processEvents()
        win.close()
        song.region = (0.0, 1.0)
        out = os.path.join(tmp, "selftest.mp4")
        worker = ExportWorker(song, a, 640, 180, 30, out, FFMPEG is not None, FFMPEG is not None, 8)
        worker.run()
        size = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"export: {size} bytes")
        if size < 1000:
            raise RuntimeError("export produced no video")
        print("SELFTEST OK")
        return 0
    except Exception:
        traceback.print_exc()
        print("SELFTEST FAILED")
        return 1


def main():
    if "--selftest" in sys.argv:
        # GUI builds on Windows have no console: --selftest-log=<file> writes the report to a file
        log = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--selftest-log=")), None)
        if log:
            sys.stdout = sys.stderr = open(log, "w", buffering=1)
        sys.exit(selftest())
    app = QApplication(sys.argv)
    app.setApplicationName("LED Wall Motion Generator")
    app.setStyle("Fusion")
    from ledwall.resources import load_bundled_fonts
    load_bundled_fonts()

    from ledwall.gui.common import DARK_QSS
    from ledwall.gui.main_window import MainWindow
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        arg = sys.argv[1]
        if arg.lower().endswith(".json"):
            win.open_project(arg)
        else:
            win.load_audio(arg, set_tempo=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
