"""Shared widgets + dark stylesheet."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QWidget

DARK_QSS = """
QWidget { background: #111; color: #ddd; font-size: 12px; }
QGroupBox { border: 1px solid #2a2a2a; border-radius: 6px; margin-top: 14px; padding: 8px 6px 6px 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #ffa040; font-weight: bold; }
QPushButton { background: #1d1d1d; border: 1px solid #333; border-radius: 4px; padding: 4px 9px; }
QPushButton:hover { border-color: #ff9a30; }
QPushButton:checked { background: #5a3208; border-color: #ff9a30; }
QPushButton:disabled { color: #555; }
QPushButton#primary { background: #ff8c1a; color: #000; font-weight: bold; border: none; }
QPushButton#primary:hover { background: #ffa64d; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #1a1a1a; border: 1px solid #333; border-radius: 3px; padding: 3px; }
QListWidget { background: #161616; border: 1px solid #2a2a2a; }
QListWidget::item:selected { background: #5a3208; color: #fff; }
QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
QSlider::handle:horizontal { background: #ff9a30; width: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #7a4410; border-radius: 2px; }
QProgressBar { border: 1px solid #333; border-radius: 3px; text-align: center; background: #1a1a1a; }
QProgressBar::chunk { background: #ff8c1a; }
QTabWidget::pane { border: 1px solid #2a2a2a; top: -1px; }
QTabBar::tab { background: #171717; border: 1px solid #2a2a2a; padding: 6px 16px; margin-right: 2px; }
QTabBar::tab:selected { background: #2a1a08; color: #ffb060; border-bottom-color: #2a1a08; }
QScrollArea { border: none; }
QLabel#preview { background: #000; border: 1px solid #222; }
QLabel#hint { color: #888; }
QCheckBox::indicator, QListView::indicator { width: 13px; height: 13px; border: 1px solid #777; background: #1a1a1a; border-radius: 2px; }
QCheckBox::indicator:checked, QListView::indicator:checked { background: #ff8c1a; border-color: #ffb060; }
QMenuBar { background: #151515; } QMenuBar::item:selected { background: #5a3208; }
QMenu { background: #1a1a1a; border: 1px solid #333; } QMenu::item:selected { background: #5a3208; }
"""


class ColorButton(QPushButton):
    colorChanged = pyqtSignal(tuple)

    def __init__(self, rgb: tuple, parent=None, show_text: bool = True):
        super().__init__(parent)
        self.rgb = tuple(int(c) for c in rgb)
        self.show_text = show_text
        self.setFixedHeight(26)
        self.clicked.connect(self._pick)
        self._refresh()

    def set_rgb(self, rgb):
        self.rgb = tuple(int(c) for c in rgb)
        self._refresh()

    def _refresh(self):
        r, g, b = self.rgb
        fg = "#000" if (r * 0.3 + g * 0.59 + b * 0.11) > 128 else "#fff"
        self.setText(f"#{r:02X}{g:02X}{b:02X}" if self.show_text else "")
        self.setStyleSheet(f"background: rgb({r},{g},{b}); color: {fg}; border: 1px solid #444;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(*self.rgb), self, "Pick colour")
        if c.isValid():
            self.set_rgb((c.red(), c.green(), c.blue()))
            self.colorChanged.emit(self.rgb)


class LabeledSlider(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, lo: float, hi: float, value: float, step: float = 0.01, fmt="{:.2f}", parent=None):
        super().__init__(parent)
        self.lo, self.step, self.fmt = lo, step, fmt
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.label = QLabel()
        self.label.setFixedWidth(52)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._changed)
        self.set_value(value)

    def value(self) -> float:
        return self.lo + self.slider.value() * self.step

    def set_value(self, v: float):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self.label.setText(self.fmt.format(self.value()))

    def _changed(self, _):
        self.label.setText(self.fmt.format(self.value()))
        self.valueChanged.emit(self.value())


class PreviewCanvas(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("preview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def preview_size(self, aspect: float, max_w: int = 1400) -> tuple:
        w = max(64, min(self.width() - 4, max_w))
        h = int(w / aspect)
        if h > self.height() - 4:
            h = max(32, self.height() - 4)
            w = int(h * aspect)
        return max(2, w - w % 2), max(2, h - h % 2)

    def show_frame(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_BGR888)
        self.setPixmap(QPixmap.fromImage(img))


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


LANE_COLORS = {
    "member": (255, 168, 60),
    "group": (70, 210, 200),
    "fx": (180, 130, 255),
    "logo": (255, 225, 120),
}
