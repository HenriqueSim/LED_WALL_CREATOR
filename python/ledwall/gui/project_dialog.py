"""New / Save As dialog, Reaper style: project name, where to keep it, and an optional project folder
with Media/ and Backups/ inside."""

from __future__ import annotations

import os

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .. import project as proj


class ProjectDialog(QDialog):
    def __init__(self, parent=None, title="Save project", name="", location=None, has_audio=False,
                 ok_text="Save"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        st = QSettings()
        self.edit_name = QLineEdit(name or "My song")
        self.edit_name.selectAll()
        self.edit_loc = QLineEdit(location or st.value("project/location", proj.default_location()))
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        loc_row = QHBoxLayout()
        loc_row.addWidget(self.edit_loc, 1)
        loc_row.addWidget(b)
        self.chk_folder = QCheckBox("Create a folder for the project (with Media and Backups folders inside)")
        self.chk_folder.setChecked(st.value("project/make_folder", True, type=bool))
        self.chk_media = QCheckBox("Copy the audio into the project's Media folder")
        self.chk_media.setChecked(st.value("project/copy_media", True, type=bool))
        self.chk_media.setVisible(has_audio)
        self.lbl_path = QLabel()
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setStyleSheet("color: #9aa;")

        form = QFormLayout()
        form.addRow("Project name", self.edit_name)
        form.addRow("Location", loc_row)
        form.addRow(self.chk_folder)
        form.addRow(self.chk_media)
        form.addRow(self.lbl_path)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)
        for w in (self.edit_name, self.edit_loc):
            w.textChanged.connect(self._update)
        self.chk_folder.toggled.connect(self._update)
        self._update()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Where to keep the project", self.edit_loc.text())
        if d:
            self.edit_loc.setText(d)

    @property
    def path(self) -> str:
        return proj.project_path_for(self.edit_loc.text().strip(), self.edit_name.text().strip(),
                                     self.chk_folder.isChecked())

    @property
    def make_folder(self) -> bool:
        return self.chk_folder.isChecked()

    @property
    def copy_media(self) -> bool:
        return self.chk_media.isChecked() and self.chk_media.isEnabled()

    def _update(self):
        self.chk_media.setEnabled(self.chk_folder.isChecked() or proj.media_dir(self.path) is not None)
        p = self.path
        extra = ""
        if self.chk_folder.isChecked():
            extra = f"\nwith {proj.MEDIA_DIR}{os.sep} and {proj.BACKUP_DIR}{os.sep} next to it"
        self.lbl_path.setText(f"Will save: {p}{extra}")

    def _accept(self):
        if not self.edit_loc.text().strip():
            QMessageBox.warning(self, "Location", "Choose where to keep the project.")
            return
        p = self.path
        if os.path.exists(p):
            r = QMessageBox.question(self, "Project exists", f"{p}\nalready exists. Replace it?\n"
                                     "(Its current version is kept in Backups when it has that folder.)")
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            proj.create_folders(p, self.make_folder)
        except OSError as exc:
            QMessageBox.critical(self, "Can't create the project", str(exc))
            return
        st = QSettings()
        st.setValue("project/location", self.edit_loc.text().strip())
        st.setValue("project/make_folder", self.make_folder)
        if self.chk_media.isVisible():
            st.setValue("project/copy_media", self.chk_media.isChecked())
        self.accept()
