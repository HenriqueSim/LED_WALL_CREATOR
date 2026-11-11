# PyInstaller recipe for a standalone build (Windows / macOS / Linux).
#   pip install -r requirements.txt pyinstaller
#   pyinstaller LEDWallCreator.spec            -> dist/LEDWallCreator/  (macOS: dist/LEDWallCreator.app)
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, ".")
from ledwall.version import __version__

ICON = {"win32": "icons/app.ico", "darwin": "icons/app.icns"}.get(sys.platform, "icons/app.png")
ICON = ICON if os.path.isfile(ICON) else None

datas = [("fonts", "fonts")]
import glob
# window icon, .ledwall file icon and the UI's SVG icons (not the big logo source image)
for f in ["icons/app.png", "icons/app.ico"] + glob.glob("icons/*.svg"):
    if os.path.isfile(f):
        datas.append((f, "icons"))
datas += collect_data_files("librosa")            # includes the .pyi stubs librosa's lazy loader needs
datas += collect_data_files("imageio_ffmpeg")     # bundled ffmpeg binary
hidden = collect_submodules("librosa") + ["soundfile", "imageio_ffmpeg", "certifi"]

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
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LEDWallCreator")
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="LEDWallCreator.app", icon=ICON, bundle_identifier="io.github.ledwallcreator",
        info_plist={
            "CFBundleName": "LED Wall Creator",
            "CFBundleDisplayName": "LED Wall Creator",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            # double-clicking a .ledwall file opens it in the app
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "LED Wall Creator project",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": ["io.github.ledwallcreator.project"],
                **({"CFBundleTypeIconFile": "app.icns"} if ICON else {}),
            }],
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "io.github.ledwallcreator.project",
                "UTTypeDescription": "LED Wall Creator project",
                "UTTypeConformsTo": ["public.json", "public.data"],
                "UTTypeTagSpecification": {"public.filename-extension": ["ledwall"]},
            }],
        },
    )
