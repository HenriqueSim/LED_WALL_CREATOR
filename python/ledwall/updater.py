"""In-app updates for the packaged app (no GUI code here, see gui/update_dialog.py).

How an update works:
1. `fetch_latest()` asks GitHub for the newest release; `is_newer()` compares it with __version__.
2. The archive for this system is downloaded (`download()`) and unpacked next to the installed app
   as "LEDWallCreator <version>" (`install_new()`).
3. The new version is started with `--finish-update <old install> <pid>` and the old one quits.
4. The new version waits for the old one to exit, moves anything the user kept inside the old
   install (projects, audio, videos...) into the new one, and only then deletes the old install
   (`finish_update()`). If anything can't be moved, the old install is left alone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

from .version import GITHUB_REPO, __version__

APP_NAME = "LEDWallCreator"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"{APP_NAME}-updater/{__version__}"
# files the user may have put inside the app folder; never deleted with an old version
USER_EXTS = {".ledwall", ".json", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".aif", ".aiff",
             ".mp4", ".mov", ".mkv", ".avi", ".png", ".jpg", ".jpeg", ".txt", ".ttf", ".otf"}


# ---- versions ----------------------------------------------------------------------------------
def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


# ---- where the app is installed ----------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_install() -> str | None:
    """The folder (Windows / Linux) or .app bundle (macOS) of the running packaged app, None from source.
    LEDWALL_FAKE_INSTALL overrides it (for testing the updater from source)."""
    fake = os.environ.get("LEDWALL_FAKE_INSTALL")
    if fake:
        return fake
    if not is_frozen():
        return None
    exe = os.path.realpath(sys.executable)
    if sys.platform == "darwin":
        app = os.path.dirname(os.path.dirname(os.path.dirname(exe)))     # X.app/Contents/MacOS/exe
        return app if app.endswith(".app") else os.path.dirname(exe)
    return os.path.dirname(exe)


def platform_key() -> str:
    return "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"


def executable_in(install: str) -> str:
    if install.endswith(".app"):
        return os.path.join(install, "Contents", "MacOS", APP_NAME)
    return os.path.join(install, APP_NAME + (".exe" if platform_key() == "windows" else ""))


def looks_like_install(path: str) -> bool:
    """Only folders that are clearly an installed copy of the app may ever be deleted."""
    if not path or not os.path.isdir(path):
        return False
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    if real in (home, os.path.dirname(home), os.path.abspath(os.sep)) or len(real) < 6:
        return False
    if path.endswith(".app"):
        return os.path.isfile(os.path.join(path, "Contents", "MacOS", APP_NAME))
    exe = any(os.path.isfile(os.path.join(path, APP_NAME + s)) for s in ("", ".exe"))
    return exe and os.path.isdir(os.path.join(path, "_internal"))


# ---- GitHub ------------------------------------------------------------------------------------
def _ssl_context():
    """Packaged apps can't always find the system's certificates (macOS): use certifi's bundle."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _open(url: str, timeout: float, accept: str | None = None):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    if url.startswith("https:"):
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(timeout: float = 6.0) -> dict:
    """{'version', 'tag', 'notes', 'page', 'asset_url', 'asset_name', 'asset_size'} of the newest release."""
    with _open(API_LATEST, timeout, "application/vnd.github+json") as r:
        data = json.load(r)
    return release_info(data)


def release_info(data: dict) -> dict:
    tag = data.get("tag_name", "")
    key = platform_key()
    asset = next((a for a in data.get("assets", []) if key in a.get("name", "").lower()), None)
    return {
        "version": ".".join(str(n) for n in parse_version(tag)),
        "tag": tag,
        "notes": data.get("body") or "",
        "page": data.get("html_url") or RELEASES_PAGE,
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
        "asset_size": asset.get("size", 0) if asset else 0,
    }


def download(url: str, dest: str, progress=None, cancelled=lambda: False, chunk: int = 1 << 16) -> str:
    """Download to `dest` (via a .part file). progress(done_bytes, total_bytes). Raises on cancel."""
    part = dest + ".part"
    with _open(url, 30) as r, open(part, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled():
                raise InterruptedError("Download cancelled")
            buf = r.read(chunk)
            if not buf:
                break
            fh.write(buf)
            done += len(buf)
            if progress:
                progress(done, total)
    if total and os.path.getsize(part) != total:
        os.remove(part)
        raise IOError("The download was incomplete. Please try again.")
    os.replace(part, dest)
    return dest


# ---- unpacking ---------------------------------------------------------------------------------
def _extract(archive: str, dest: str):
    if archive.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(dest, filter="tar")       # keeps the executable bits, blocks unsafe paths
            except TypeError:                           # Python without extraction filters
                tf.extractall(dest)
    elif sys.platform == "darwin" and shutil.which("ditto"):
        subprocess.run(["ditto", "-x", "-k", archive, dest], check=True)   # keeps the .app's symlinks
    else:
        with zipfile.ZipFile(archive) as zf:
            root = os.path.realpath(dest)
            for name in zf.namelist():
                if not os.path.realpath(os.path.join(dest, name)).startswith(root):
                    raise IOError(f"Unsafe path in the update archive: {name}")
            zf.extractall(dest)
            for info in zf.infolist():                  # zipfile drops the executable bit
                mode = info.external_attr >> 16
                if mode & 0o111:
                    os.chmod(os.path.join(dest, info.filename), mode & 0o7777)


def target_dir(old_install: str | None, fallback_parent: str) -> str:
    """Where the new version goes: next to the installed one, if that folder is writable."""
    if old_install:
        parent = os.path.dirname(os.path.abspath(old_install))
        if "AppTranslocation" not in parent and os.access(parent, os.W_OK):
            return parent
    os.makedirs(fallback_parent, exist_ok=True)
    return fallback_parent


def install_new(archive: str, version: str, parent: str) -> str:
    """Unpack the downloaded archive into `parent` as "LEDWallCreator <version>[.app]"; returns its path."""
    stage = os.path.join(parent, f".{APP_NAME}-update-{version}")
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    try:
        _extract(archive, stage)
        items = [n for n in os.listdir(stage) if not n.startswith(("__MACOSX", "."))]
        if len(items) != 1:
            raise IOError("The update archive has an unexpected layout.")
        src = os.path.join(stage, items[0])
        final = os.path.join(parent, f"{APP_NAME} {version}" + (".app" if src.endswith(".app") else ""))
        if os.path.exists(final):
            if not looks_like_install(final):
                raise IOError(f"{final} already exists and isn't a copy of the app; move it away and retry.")
            rescue_user_files(final, src)            # a previous attempt; keep anything put in it
            shutil.rmtree(final)
        os.replace(src, final)
        if not os.path.isfile(executable_in(final)):
            raise IOError("The update doesn't contain the app.")
        return final
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def launch_new(new_install: str, old_install: str | None, open_path: str | None = None):
    """Start the new version, which finishes the update (removes the old one) once this one quits."""
    args = []
    if old_install:
        args += ["--finish-update", old_install, str(os.getpid())]
    if open_path:
        args.append(open_path)
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if new_install.endswith(".app"):
        subprocess.Popen(["open", "-n", new_install, "--args"] + args, **quiet)
    elif platform_key() == "windows":
        flags = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([executable_in(new_install)] + args, creationflags=flags, close_fds=True,
                         cwd=new_install, **quiet)
    else:
        subprocess.Popen([executable_in(new_install)] + args, start_new_session=True, close_fds=True,
                         cwd=new_install, **quiet)


# ---- cleaning up the old version -------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform_key() == "windows":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)   # QUERY_LIMITED | SYNCHRONIZE
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102          # WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def user_files(old: str, new: str) -> list:
    """Relative paths inside the old install that belong to the user, not to the app: anything at
    the top level besides the app itself, plus project / media files deeper in that the new
    version doesn't ship."""
    out = []
    app_own = {APP_NAME, APP_NAME + ".exe", "_internal", "Contents"}
    for name in os.listdir(old):
        if name not in app_own:
            out.append(name)
    for root, _dirs, files in os.walk(old):
        rel_root = os.path.relpath(root, old)
        if rel_root == "." or rel_root.split(os.sep)[0] not in app_own:
            continue
        for f in files:
            rel = os.path.join(rel_root, f)
            if os.path.splitext(f)[1].lower() in USER_EXTS and not os.path.exists(os.path.join(new, rel)):
                out.append(rel)
    return out


def rescue_user_files(old: str, new: str) -> list:
    """Move the user's files from the old install into the new one (same relative place). Returns
    [(old path, new path)]. Raises if one can't be moved, so the caller keeps the old install."""
    moved = []
    for rel in user_files(old, new):
        src = os.path.join(old, rel)
        dst = os.path.join(new, rel)
        if os.path.exists(dst):
            base, ext = os.path.splitext(dst)
            k = 1
            while os.path.exists(f"{base} (from old version{'' if k == 1 else f' {k}'}){ext}"):
                k += 1
            dst = f"{base} (from old version{'' if k == 1 else f' {k}'}){ext}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        moved.append((src, dst))
    return moved


def finish_update(old: str, pid: int, new: str | None = None, wait_s: float = 60.0) -> dict:
    """Run by the new version: wait for the old one to quit, rescue the user's files, delete it.
    Returns {'moved': [(old, new)], 'removed': bool, 'error': str | None}."""
    new = new or current_install()
    result = {"moved": [], "removed": False, "error": None}
    try:
        old_r, new_r = os.path.realpath(old), os.path.realpath(new or "")
        if not looks_like_install(old):
            result["error"] = f"{old} doesn't look like an installed copy of the app, so it was kept."
            return result
        if not new or old_r == new_r or new_r.startswith(old_r + os.sep) or old_r.startswith(new_r + os.sep):
            result["error"] = "The old and new versions overlap, so the old one was kept."
            return result
        t_end = time.time() + wait_s
        while _pid_alive(pid) and time.time() < t_end:
            time.sleep(0.25)
        result["moved"] = rescue_user_files(old, new)
        last = None
        for _ in range(20):                 # Windows can hold the old files for a moment after exit
            try:
                shutil.rmtree(old)
                result["removed"] = True
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        if not result["removed"]:
            result["error"] = f"Couldn't remove the old version ({last}). You can delete {old} yourself."
    except Exception as exc:     # never lose files: report and keep whatever is left
        result["error"] = f"{exc}. The old version was kept at {old}."
    return result
