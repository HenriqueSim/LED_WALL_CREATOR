"""Make double-clicking a .ledwall file open the app (packaged app only, current user only, no admin).

Windows: per-user registry keys (HKCU\\Software\\Classes). Linux: a .desktop file and a MIME type in
~/.local/share. macOS: declared in the app's Info.plist (LEDWallCreator.spec), nothing to do here.
Re-run on every start so it follows the app when an update installs it in a new folder."""

from __future__ import annotations

import os
import subprocess
import sys

from .resources import APP_DIR

PROG_ID = "LEDWallCreator.Project"
MIME = "application/x-ledwall-project"


def _icon(ext: str) -> str | None:
    p = os.path.join(APP_DIR, "icons", "app" + ext)
    return p if os.path.isfile(p) else None


def register(exe: str | None = None) -> bool:
    exe = exe or sys.executable
    try:
        if sys.platform.startswith("win"):
            return _register_windows(exe)
        if sys.platform.startswith("linux"):
            return _register_linux(exe)
    except Exception as exc:     # never let this stop the app
        print(f"file association: {exc}", file=sys.stderr)
    return False


def _register_windows(exe: str) -> bool:
    import winreg
    cmd = f'"{exe}" "%1"'
    base = r"Software\Classes"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROG_ID}\shell\open\command") as k:
            if winreg.QueryValue(k, None) == cmd:
                return True          # already pointing at this copy
    except OSError:
        pass

    def setkey(path, value, name=None):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{path}") as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    setkey(".ledwall", PROG_ID)
    setkey(rf".ledwall\OpenWithProgids", "", PROG_ID)
    setkey(PROG_ID, "LED Wall Creator project")
    setkey(rf"{PROG_ID}\DefaultIcon", f'"{_icon(".ico")}",0' if _icon(".ico") else f'"{exe}",0')
    setkey(rf"{PROG_ID}\shell\open\command", cmd)
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)     # SHCNE_ASSOCCHANGED
    return True


def _register_linux(exe: str) -> bool:
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    apps, mime_pk = os.path.join(share, "applications"), os.path.join(share, "mime", "packages")
    desktop = os.path.join(apps, "ledwallcreator.desktop")
    icon = _icon(".png") or "video-display"
    entry = (f"[Desktop Entry]\nType=Application\nName=LED Wall Creator\nComment=Audio-synced LED wall videos\n"
             f'Exec="{exe}" %f\nIcon={icon}\nTerminal=false\nCategories=AudioVideo;Video;\nMimeType={MIME};\n')
    try:
        with open(desktop) as fh:
            if fh.read() == entry:
                return True
    except OSError:
        pass
    os.makedirs(apps, exist_ok=True)
    os.makedirs(mime_pk, exist_ok=True)
    with open(os.path.join(mime_pk, "ledwallcreator.xml"), "w") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
                 f'  <mime-type type="{MIME}">\n    <comment>LED Wall Creator project</comment>\n'
                 '    <glob pattern="*.ledwall"/>\n  </mime-type>\n</mime-info>\n')
    with open(desktop, "w") as fh:
        fh.write(entry)
    for cmd in (["update-mime-database", os.path.join(share, "mime")], ["update-desktop-database", apps],
                ["xdg-mime", "default", "ledwallcreator.desktop", MIME]):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return True
