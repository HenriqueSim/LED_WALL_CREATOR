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


def set_app_icon(app):
    """Window / taskbar icon from icons/app.png, when the app has one."""
    p = os.path.join(ICONS_DIR, "app.png")
    if os.path.isfile(p):
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(p))
