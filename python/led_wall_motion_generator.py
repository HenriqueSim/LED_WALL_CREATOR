#!/usr/bin/env python3
# =============================================================================
#  LED Wall Motion Generator
#  DAW-style light programming for LED wall backgrounds, rendered to .mp4
# =============================================================================
#
#  Setup:
#  ---------------------------------------------------------------------------
#    python3 -m venv .venv && source .venv/bin/activate
#    pip install PyQt6 opencv-python numpy librosa soundfile
#
#    # Optional but recommended (MP4/M4A audio import, H.264 high-bitrate
#    # encode and audio muxing on export):
#    #   Linux:   sudo apt install ffmpeg      macOS: brew install ffmpeg
#
#    python led_wall_motion_generator.py [project.json | audio file]
#  ---------------------------------------------------------------------------
#
#  Workflow
#    Band tab           members (left -> right on stage) and groups
#    Pattern Editor     drum-machine grid: one row per member, per group and per
#                       FX lane (Pulse, Chase, Random, Sparkle, Strobe, Flash
#                       Peak, Blackout, Band Name). Notes = when it is lit,
#                       velocity = how bright.
#    Master Track       place / copy / stretch pattern clips over the song
#                       waveform on 3 tracks; lyrics lane for the top band.
#    Look & Song        one colour per song, background design, band name layer.
#    Export             3840x1080 / 1920x1080 / custom, 30/60 fps, seamless loop.
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
#  Fonts: fonts/Anton-Regular.ttf (SIL Open Font License, see fonts/OFL.txt)
# =============================================================================

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PyQt6.QtGui import QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LED Wall Motion Generator")
    app.setStyle("Fusion")
    font_path = os.path.join(HERE, "fonts", "Anton-Regular.ttf")
    if QFontDatabase.addApplicationFont(font_path) < 0:
        print(f"warning: could not load {font_path}; band name will use a fallback font", file=sys.stderr)

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
