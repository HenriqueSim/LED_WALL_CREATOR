"""Big preview window for watching the video (optionally on the simulated stage). F11 or a
double-click toggles fullscreen, Esc leaves fullscreen / closes, Space plays / pauses."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .common import PreviewCanvas


class StageWindow(QWidget):
    togglePlay = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("LED wall preview — F11 fullscreen · Space play/pause · Esc close")
        self.resize(1280, 760)
        self.setStyleSheet("background: #000;")
        self.canvas = PreviewCanvas()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key.Key_F11:
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_Escape:
            self.showNormal() if self.isFullScreen() else self.close()
        elif k == Qt.Key.Key_Space:
            self.togglePlay.emit()
        else:
            super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, _ev):
        self.toggle_fullscreen()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, ev):
        self.closed.emit()
        super().closeEvent(ev)
