#!/usr/bin/env python3
# =============================================================================
#  LED Wall Creator
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
#    python led_wall_motion_generator.py [project.ledwall | audio file]
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

from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

ORG = "LEDWallCreator"
PROJECT_EXTS = (".ledwall", ".json")


class App(QApplication):
    """Also receives macOS "open this file" events (double-clicked .ledwall files)."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending: list = []

    def event(self, ev):
        if ev.type() == QEvent.Type.FileOpen:
            path = ev.file()
            if self.window is not None:
                open_path(self.window, path)
            else:
                self.pending.append(path)
            return True
        return super().event(ev)


def _migrate_settings(org: str):
    """The app used to be called "LED Wall Motion Generator": carry its settings (recent projects,
    update choices, project location) over to the new name once."""
    from PyQt6.QtCore import QSettings
    new, old = QSettings(org, "LED Wall Creator"), QSettings(org, "LED Wall Motion Generator")
    if not new.allKeys() and old.allKeys():
        for k in old.allKeys():
            new.setValue(k, old.value(k))
        new.sync()


def open_path(win, path: str):
    if path.lower().endswith(PROJECT_EXTS):
        win.open_project(path)
    else:
        win.load_audio(path, set_tempo=True)


def selftest() -> int:
    """`--selftest`: exercise a packaged build end to end (window, fonts, audio analysis, rendering,
    stage view, video export) without user interaction. Prints SELFTEST OK and returns 0 on success."""
    import tempfile
    import traceback
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG + "-selftest")
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
        stage.compose(frame, len(song.members), "led", 2.0, house=1.0, beat=4.2, env=0.7)
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
        # project folder: save (with backup) and load back, audio stored relative to the project
        from ledwall import project as proj
        from ledwall.version import __version__
        print("version:", __version__)
        pf = proj.project_path_for(tmp, "Self test", True)
        proj.create_folders(pf, True)
        song.audio_path = proj.copy_into_media(wav, pf)
        proj.save(song, pf)
        proj.save(song, pf)
        back = proj.load(pf)
        if not (back.audio_path and os.path.isfile(back.audio_path) and os.listdir(os.path.join(tmp, "Self test", "Backups"))):
            raise RuntimeError("project save / load failed")
        print("project:", pf)
        # update check over HTTPS (catches missing certificates in the packaged app); required on CI only
        from ledwall import updater
        try:
            info = updater.fetch_latest()
            print(f"update check: latest {info['version']} ({info['asset_name']})")
        except Exception as exc:
            import ssl
            import urllib.error
            print("update check failed:", repr(exc))
            # GitHub answering at all (e.g. 403 rate limit on shared CI machines) means HTTPS works;
            # only certificate / TLS problems are build bugs
            reason = getattr(exc, "reason", exc)
            if os.environ.get("CI") and not isinstance(exc, urllib.error.HTTPError) and isinstance(reason, ssl.SSLError):
                raise
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
    args = sys.argv[1:]
    finish = None
    if "--finish-update" in args:            # started by the previous version after an update
        i = args.index("--finish-update")
        try:
            finish = (args[i + 1], int(args[i + 2]))
        except (IndexError, ValueError):
            pass
        del args[i:i + 3]
    app = App(sys.argv[:1] + args)
    app.setOrganizationName(ORG)
    _migrate_settings(ORG)
    app.setApplicationName("LED Wall Creator")
    app.setDesktopFileName("ledwallcreator")      # Wayland / GNOME: ties the window to the menu entry
    app.setStyle("Fusion")
    from ledwall.resources import load_bundled_fonts
    load_bundled_fonts()

    from ledwall.gui.common import DARK_QSS
    from ledwall.gui.main_window import MainWindow
    app.setStyleSheet(DARK_QSS)
    from ledwall.resources import set_app_icon
    set_app_icon(app)
    win = MainWindow()
    app.window = win
    win.show()
    files = [a for a in args if not a.startswith("-") and os.path.exists(a)] + app.pending
    if files:
        open_path(win, files[0])
    if getattr(sys, "frozen", False):
        from ledwall.file_assoc import register
        register()
    if finish:
        from ledwall.gui.update_dialog import FinishUpdateWorker, report_finished_update
        worker = FinishUpdateWorker(*finish, parent=win)
        worker.finished_with.connect(lambda r: report_finished_update(win, r))
        worker.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
