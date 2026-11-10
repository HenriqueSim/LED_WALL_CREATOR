"""Project files and folders, Reaper style:

    <Location>/<Name>/            the project folder (optional, on by default)
        <Name>.ledwall            the project (JSON inside)
        Media/                    audio copied into the project
        Backups/                  the previous versions, one per save (newest 20 kept)

The audio path is stored relative to the project when the audio is inside its folder, so the whole
folder can be moved or copied to another computer.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import shutil

from .model import Song

EXT = ".ledwall"
OLD_EXT = ".json"
MEDIA_DIR = "Media"
BACKUP_DIR = "Backups"
KEEP_BACKUPS = 20
OPEN_FILTER = "LED Wall projects (*.ledwall *.json);;All files (*)"


def default_location() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "LED Wall Creator")


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip().rstrip(".")
    return out or "Untitled"


def project_path_for(location: str, name: str, make_folder: bool) -> str:
    name = safe_name(name)
    folder = os.path.join(location, name) if make_folder else location
    return os.path.join(folder, name + EXT)


def create_folders(project_file: str, make_folder: bool):
    """Make the project folder with Media/ and Backups/ (or just the location when not making one)."""
    folder = os.path.dirname(project_file)
    os.makedirs(folder, exist_ok=True)
    if make_folder:
        os.makedirs(os.path.join(folder, MEDIA_DIR), exist_ok=True)
        os.makedirs(os.path.join(folder, BACKUP_DIR), exist_ok=True)


def media_dir(project_file: str | None) -> str | None:
    """The project's Media folder, if it has one."""
    if not project_file:
        return None
    d = os.path.join(os.path.dirname(project_file), MEDIA_DIR)
    return d if os.path.isdir(d) else None


def copy_into_media(src: str, project_file: str) -> str:
    """Copy an audio file into the project's Media folder (if it isn't there already); returns its new path."""
    md = os.path.join(os.path.dirname(project_file), MEDIA_DIR)
    os.makedirs(md, exist_ok=True)
    src_abs = os.path.abspath(src)
    if os.path.dirname(src_abs) == os.path.abspath(md):
        return src_abs
    base, ext = os.path.splitext(os.path.basename(src))
    dst, k = os.path.join(md, base + ext), 1
    while os.path.exists(dst):
        if os.path.getsize(dst) == os.path.getsize(src_abs):      # the same file was copied before
            return dst
        k += 1
        dst = os.path.join(md, f"{base} ({k}){ext}")
    shutil.copy2(src_abs, dst)
    return dst


# ---- saving / loading ------------------------------------------------------------------------
def song_to_json(song: Song, project_file: str) -> dict:
    d = song.to_dict()
    ap = song.audio_path
    if ap:
        folder = os.path.abspath(os.path.dirname(project_file))
        ap_abs = os.path.abspath(ap)
        if ap_abs.startswith(folder + os.sep):          # inside the project folder: store it relative
            d["audio_path"] = os.path.relpath(ap_abs, folder).replace(os.sep, "/")
    return d


def backup(project_file: str) -> str | None:
    """Copy the current file into Backups/ before it is overwritten (only in project folders)."""
    folder = os.path.dirname(project_file)
    bdir = os.path.join(folder, BACKUP_DIR)
    if not os.path.isfile(project_file) or not os.path.isdir(bdir):
        return None
    name = os.path.splitext(os.path.basename(project_file))[0]
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    dst = os.path.join(bdir, f"{name} {stamp}{EXT}")
    k = 2
    while os.path.exists(dst):
        dst = os.path.join(bdir, f"{name} {stamp} ({k}){EXT}")
        k += 1
    shutil.copy2(project_file, dst)
    olds = sorted(glob.glob(os.path.join(glob.escape(bdir), glob.escape(name) + " ????-??-?? ??-??-??*" + EXT)),
                  key=os.path.getmtime)
    for p in olds[:-KEEP_BACKUPS]:
        try:
            os.remove(p)
        except OSError:
            pass
    return dst


def save(song: Song, project_file: str):
    """Write the project safely: back up the previous version, write to a temporary file, then swap."""
    os.makedirs(os.path.dirname(os.path.abspath(project_file)), exist_ok=True)
    backup(project_file)
    tmp = project_file + ".saving"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(song_to_json(song, project_file), fh, indent=1)
    os.replace(tmp, project_file)


def resolve_audio(audio_path: str | None, project_file: str) -> str | None:
    """Find a project's audio: relative to the project, as stored, or by name in its Media folder."""
    if not audio_path:
        return None
    folder = os.path.dirname(os.path.abspath(project_file))
    cands = [audio_path] if os.path.isabs(audio_path) else [os.path.join(folder, audio_path)]
    cands.append(os.path.join(folder, MEDIA_DIR, os.path.basename(audio_path.replace("\\", "/"))))
    cands.append(os.path.join(folder, os.path.basename(audio_path.replace("\\", "/"))))
    for c in cands:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return None


def load(project_file: str) -> Song:
    with open(project_file, encoding="utf-8") as fh:
        song = Song.from_dict(json.load(fh))
    found = resolve_audio(song.audio_path, project_file)
    if found:
        song.audio_path = found
    elif song.audio_path and not os.path.isabs(song.audio_path):
        song.audio_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(project_file)), song.audio_path))
    return song
