"""Where the app's bundled files live (works from a source checkout and from a packaged build)."""

from __future__ import annotations

import glob
import os
import sys

# PyInstaller unpacks data next to sys._MEIPASS; from source it is the folder above this package
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONTS_DIR = os.path.join(APP_DIR, "fonts")

_loaded: list | None = None


def load_bundled_fonts() -> list:
    """Register the fonts in fonts/ with Qt (once). Returns their family names."""
    global _loaded
    if _loaded is None:
        from PyQt6.QtGui import QFontDatabase
        _loaded = []
        for path in sorted(glob.glob(os.path.join(FONTS_DIR, "*.ttf")) + glob.glob(os.path.join(FONTS_DIR, "*.otf"))):
            fid = QFontDatabase.addApplicationFont(path)
            if fid < 0:
                print(f"warning: could not load font {path}", file=sys.stderr)
                continue
            for fam in QFontDatabase.applicationFontFamilies(fid):
                if fam not in _loaded:
                    _loaded.append(fam)
    return list(_loaded)


ICONS_DIR = os.path.join(APP_DIR, "icons")


# Windows groups taskbar buttons and picks their icon by this ID; the installer gives the Start menu
# and desktop shortcuts the same one (installer/LEDWallCreator.iss), so pinned and running match.
APP_USER_MODEL_ID = "LEDWallCreator.App"


def set_windows_app_id():
    """Call before any window exists. Without it Windows may show the python / generic icon on the
    taskbar and not group the window with its pinned shortcut."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


def set_app_icon(app):
    """Window / taskbar icon: the multi-size .ico on Windows (crisp at 16-256 px), else app.png."""
    from PyQt6.QtGui import QIcon
    names = ["app.ico", "app.png"] if sys.platform.startswith("win") else ["app.png"]
    for name in names:
        p = os.path.join(ICONS_DIR, name)
        if os.path.isfile(p):
            ic = QIcon(p)
            if not ic.isNull():
                app.setWindowIcon(ic)
                return
