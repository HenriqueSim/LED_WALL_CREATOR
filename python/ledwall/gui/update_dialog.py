"""Update check on start-up and the "Update available" dialog (download, install, restart)."""

from __future__ import annotations

import os
import tempfile

from PyQt6.QtCore import QSettings, QStandardPaths, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout,
)

from .. import updater
from ..version import __version__


class UpdateChecker(QThread):
    found = pyqtSignal(dict)       # a newer release
    none = pyqtSignal()            # up to date
    failed = pyqtSignal(str)

    def run(self):
        try:
            info = updater.fetch_latest()
        except Exception as exc:     # offline, GitHub down, rate limited...
            self.failed.emit(str(exc))
            return
        if updater.is_newer(info["version"]):
            self.found.emit(info)
        else:
            self.none.emit()


class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(str)          # path of the new install
    failed = pyqtSignal(str)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            info = self.info
            tmp = tempfile.mkdtemp(prefix="ledwall-update-")
            archive = os.path.join(tmp, info["asset_name"])
            mb = lambda n: n / 1048576

            def prog(done, total):
                total = total or info.get("asset_size") or 0
                pct = int(done * 90 / total) if total else 0
                self.progress.emit(pct, f"Downloading… {mb(done):.0f} of {mb(total):.0f} MB" if total
                                   else f"Downloading… {mb(done):.0f} MB")
            updater.download(info["asset_url"], archive, prog, lambda: self._cancel)
            self.progress.emit(92, "Installing…")
            fallback = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) \
                or os.path.expanduser("~")
            parent = updater.target_dir(updater.current_install(), fallback)
            new = updater.install_new(archive, info["version"], parent)
            try:
                os.remove(archive)
                os.rmdir(tmp)
            except OSError:
                pass
            self.progress.emit(100, "Installed")
            self.done.emit(new)
        except InterruptedError:
            self.failed.emit("")
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    """Shows what's new and installs it. `before_install()` (from the main window) asks to save
    unsaved work and returns False to stop; `quit_app(path)` closes the app."""

    def __init__(self, info: dict, before_install, quit_app, open_project=None, parent=None):
        super().__init__(parent)
        self.info, self.before_install, self.quit_app, self.open_project = info, before_install, quit_app, open_project
        self.worker = None
        self.setWindowTitle("Update available")
        self.setMinimumSize(660, 440)
        head = QLabel(f"<b>LED Wall Creator {info['version']}</b> is available. You have {__version__}.")
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.get("notes") or "No release notes.")
        self.frozen = updater.current_install() is not None
        sub = QLabel("Your projects are kept: they live in their own folders, and anything you saved "
                     "inside the app's folder is moved into the new version before the old one is removed."
                     if self.frozen else
                     "You're running the app from its source code: update it with <code>git pull</code> "
                     "in the app's folder (or download the new version from the release page).")
        sub.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.hide()
        self.lbl_status = QLabel()
        self.chk_auto = QCheckBox("Check for updates when the app starts")
        self.chk_auto.setChecked(QSettings().value("updates/auto_check", True, type=bool))
        self.chk_auto.toggled.connect(lambda v: QSettings().setValue("updates/auto_check", v))

        self.btn_install = QPushButton("Install update" if self.frozen else "Open release page")
        self.btn_install.setDefault(True)
        self.btn_install.clicked.connect(self._install if self.frozen and info.get("asset_url") else self._open_page)
        if self.frozen and not info.get("asset_url"):
            self.btn_install.setText("Open release page")
        self.btn_skip = QPushButton("Skip this version")
        self.btn_skip.clicked.connect(self._skip)
        self.btn_later = QPushButton("Later")
        self.btn_later.clicked.connect(self._later)
        row = QHBoxLayout()
        row.addWidget(self.chk_auto)
        row.addStretch(1)
        for b in (self.btn_skip, self.btn_later, self.btn_install):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(QLabel("What's new:"))
        lay.addWidget(notes, 1)
        lay.addWidget(sub)
        lay.addWidget(self.progress)
        lay.addWidget(self.lbl_status)
        lay.addLayout(row)

    def _open_page(self):
        QDesktopServices.openUrl(QUrl(self.info.get("page") or updater.RELEASES_PAGE))
        self.accept()

    def _skip(self):
        QSettings().setValue("updates/skip_version", self.info["version"])
        self.reject()

    def _later(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self.reject()

    def _install(self):
        if not self.before_install():
            return
        self.btn_install.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.btn_later.setText("Cancel")
        self.progress.show()
        self.worker = InstallWorker(self.info, self)
        self.worker.progress.connect(lambda v, m: (self.progress.setValue(v), self.lbl_status.setText(m)))
        self.worker.done.connect(self._installed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _installed(self, new_path):
        old = updater.current_install()
        same_place = old and os.path.dirname(os.path.abspath(old)) == os.path.dirname(new_path)
        if not same_place:
            QMessageBox.information(self, "Update installed",
                                    f"The new version was installed in\n{new_path}\n\nThe folder of the current "
                                    "version can't be written to, so it is kept: delete it yourself once the new "
                                    "version works.")
        self.lbl_status.setText("Starting the new version…")
        try:
            updater.launch_new(new_path, old if same_place else None,
                               self.open_project() if self.open_project else None)
        except Exception as exc:
            QMessageBox.critical(self, "Update", f"The new version is in\n{new_path}\nbut couldn't be started: {exc}")
            return
        self.accept()
        self.quit_app()

    def _failed(self, msg):
        self.progress.hide()
        self.btn_install.setEnabled(True)
        self.btn_skip.setEnabled(True)
        self.btn_later.setText("Later")
        self.lbl_status.setText("Cancelled." if not msg else "")
        if msg:
            QMessageBox.critical(self, "Update failed", f"{msg}\n\nNothing was changed. You can also download the "
                                 "new version from the release page.")


def should_auto_check() -> bool:
    return QSettings().value("updates/auto_check", True, type=bool)


def skipped(version: str) -> bool:
    return QSettings().value("updates/skip_version", "") == version


def _remap(path: str | None, moved: list) -> str | None:
    """Where a path went if it was (inside) one of the moved files / folders."""
    if not path:
        return path
    ap = os.path.abspath(path)
    for src, dst in moved:
        src = os.path.abspath(src)
        if ap == src or ap.startswith(src + os.sep):
            return dst + ap[len(src):]
    return path


def report_finished_update(win, result: dict):
    """After an update: follow moved files (open project, its audio, recent list), then say where
    rescued files went, or why the old version is still there."""
    moved, err = result.get("moved") or [], result.get("error")
    parent = win
    if moved:
        ctl = win.ctl
        ctl.project_path = _remap(ctl.project_path, moved)
        ctl.song.audio_path = _remap(ctl.song.audio_path, moved)
        st = QSettings()
        rec = st.value("recent_projects", [])
        if isinstance(rec, list):
            st.setValue("recent_projects", [_remap(p, moved) for p in rec])
        win._update_title()
    if not moved and not err:
        return
    lines = []
    if moved:
        lines.append("These files were inside the old version's folder and were moved into the new one:\n" +
                     "\n".join(f"• {dst}" for _src, dst in moved[:15]) + ("\n…" if len(moved) > 15 else ""))
    if err:
        lines.append(err)
    (QMessageBox.warning if err else QMessageBox.information)(parent, "Update finished", "\n\n".join(lines))


class FinishUpdateWorker(QThread):
    """Run in the new version after an update: removes the old one (see updater.finish_update)."""
    finished_with = pyqtSignal(dict)

    def __init__(self, old: str, pid: int, parent=None):
        super().__init__(parent)
        self.old, self.pid = old, pid

    def run(self):
        self.finished_with.emit(updater.finish_update(self.old, self.pid))
