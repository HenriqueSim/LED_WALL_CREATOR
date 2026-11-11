"""App screenshots for the website, taken with the demo project loaded.

    cd python && .venv/bin/python ../website/tools/make_shots.py
Writes website/media/ui-*.jpg."""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "python"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setOrganizationName("LEDWallCreator-shots")
app.setStyle("Fusion")
from ledwall.resources import load_bundled_fonts, set_app_icon  # noqa: E402
load_bundled_fonts()
set_app_icon(app)
from ledwall.gui.common import DARK_QSS  # noqa: E402
app.setStyleSheet(DARK_QSS)
from ledwall.gui.main_window import MainWindow  # noqa: E402

MEDIA = os.path.join(ROOT, "website", "media")
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
for _n in ("information", "warning", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: None))

def find_song():
    """The demo song: SONG env var, else the first .mp3 at the repo root."""
    import glob
    p = os.environ.get("SONG")
    if p and os.path.isfile(p):
        return p
    found = sorted(glob.glob(os.path.join(ROOT, "*.mp3")))
    return found[0] if found else None


w = MainWindow()
w.resize(1600, 960)
w.show()
w.open_project(os.path.join(HERE, "demo.ledwall"))
if w.ctl.analysis is None and find_song():
    w.load_audio(find_song(), set_tempo=False)
for _ in range(600):                       # wait for the audio analysis (waveform, tempo)
    app.processEvents()
    time.sleep(0.02)
    if w.ctl.analysis is not None and not (w.audio_worker and w.audio_worker.isRunning()):
        break
song, eng = w.ctl.song, w.ctl.engine
by = {p.name: p for p in song.patterns}


def settle(n=8):
    for _ in range(n):
        app.processEvents()
        w._render_preview()
        app.processEvents()
        time.sleep(0.03)


def shot(name):
    settle()
    w.grab().save(os.path.join(MEDIA, f"ui-{name}.jpg"), "JPG", 86)
    print("ui-" + name)


drop_clip = next(c for c in song.clips if song.pattern(c.pattern_id).name == "Drop")
chorus_clip = next(c for c in song.clips if song.pattern(c.pattern_id).name == "Chorus")
w._edit_pattern(chorus_clip.pattern_id, 0, eng.b2t(chorus_clip.start + 0.3))
g = w.pattern_tab.grid
beam = next(e for e in song.elements if e.type == "beam")
g.collapsed = {e.id for e in song.elements if e.id != beam.id}
g.refresh_size()
settle()
g.fit()
shot("pattern")
w.tabs.setCurrentWidget(w.master_tab)
w.seek(eng.b2t(drop_clip.start + 0.6))
shot("master")
w.tabs.setCurrentWidget(w.elements_tab)
w.elements_tab.select(next(e for e in song.elements if e.type == "beam").id)
shot("elements")
lk = song.look
lk.logo_text, lk.logo_color, lk.logo_always = "your band", "white", True
w.elements_tab.select("__logo__")
w.elements_tab.reload()
shot("bandname")
lk.logo_text = ""
w.tabs.setCurrentWidget(w.export_tab)
w.export_tab.combo_view.setCurrentIndex(w.export_tab.combo_view.findData("led"))
w.seek(eng.b2t(drop_clip.start + 0.6))
for _ in range(60):
    app.processEvents()
    time.sleep(0.05)
    if "render threads" in w.export_tab.lbl_encoder.text():
        break
shot("export")


def dialog_over_app(dlg, name):
    """The dialog centred over the dimmed main window, like it appears in the app."""
    import numpy as np
    from PyQt6.QtGui import QColor, QPainter
    w.tabs.setCurrentWidget(w.pattern_tab)
    settle()
    base = w.grab()
    dlg.show()
    for _ in range(6):
        app.processEvents()
    d = dlg.grab()
    p = QPainter(base)
    p.fillRect(base.rect(), QColor(0, 0, 0, 150))
    x, y = (base.width() - d.width()) // 2, (base.height() - d.height()) // 2
    p.fillRect(x - 1, y - 1, d.width() + 2, d.height() + 2, QColor(70, 70, 90))
    p.drawPixmap(x, y, d)
    p.end()
    dlg.hide()
    base.save(os.path.join(MEDIA, f"ui-{name}.jpg"), "JPG", 86)
    print("ui-" + name)


from ledwall.gui.project_dialog import ProjectDialog  # noqa: E402
from ledwall.gui.update_dialog import UpdateDialog  # noqa: E402
dialog_over_app(ProjectDialog(w, "New project", "Summer tour", "~/Documents/LED Wall Creator", has_audio=True,
                              ok_text="Create"), "projects")
os.environ["LEDWALL_FAKE_INSTALL"] = "/Applications/LEDWallCreator.app"
info = {"version": "0.5.0", "page": "", "asset_url": "x", "asset_name": "x",
        "notes": "## What's new\n- New shapes for the Elements tab\n- Faster exports\n- Smoother preview"}
dialog_over_app(UpdateDialog(info, lambda: True, lambda: None, parent=w), "updates")

w.ctl.dirty = False
w.close()
