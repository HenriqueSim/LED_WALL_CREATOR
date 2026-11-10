# PyInstaller recipe for a standalone build (Windows / macOS / Linux).
#   pip install -r requirements.txt pyinstaller
#   pyinstaller LEDWallCreator.spec            -> dist/LEDWallCreator/  (macOS: dist/LEDWallCreator.app)
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("fonts", "fonts")]
datas += collect_data_files("librosa")            # includes the .pyi stubs librosa's lazy loader needs
datas += collect_data_files("imageio_ffmpeg")     # bundled ffmpeg binary
hidden = collect_submodules("librosa") + ["soundfile", "imageio_ffmpeg"]

a = Analysis(
    ["led_wall_motion_generator.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    runtime_hooks=["pyi_rth_cv2.py"],    # macOS .app: load OpenCV's compiled module directly
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LEDWallCreator",
    console=False,                 # GUI app: no terminal window
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LEDWallCreator")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="LEDWallCreator.app", bundle_identifier="io.github.ledwallcreator")
