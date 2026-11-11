"""The app's look: a dark graphite theme with a violet accent (from the logo), Inter as the UI font,
card-style group boxes, pill buttons and thin scrollbars."""

from __future__ import annotations

import os

from ..resources import ICONS_DIR

BG = "#0d0e12"          # window
SURFACE = "#15171d"     # cards / panels
SURFACE_2 = "#1b1e26"   # buttons, inputs
FIELD = "#111318"
BORDER = "#252935"
BORDER_HI = "#363b4a"
TEXT = "#e7e8ee"
MUTED = "#8d93a3"
ACCENT = "#8b6cff"
ACCENT_HI = "#a68cff"
ACCENT_2 = "#c45cff"
ACCENT_BG = "#2a2450"
WARM = "#ffb35c"        # playhead / status, matches the wall's default warm colour


def _icon(name: str) -> str:
    return os.path.join(ICONS_DIR, name).replace("\\", "/")


def qss() -> str:
    check, down, up = _icon("check.svg"), _icon("chevron-down.svg"), _icon("chevron-up.svg")
    return f"""
* {{ font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QMainWindow::separator, QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:vertical {{ height: 8px; }}
QSplitter::handle:hover {{ background: {BORDER}; }}

QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
            margin-top: 26px; padding: 14px 12px 10px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 6px; top: 4px;
                   color: {MUTED}; font-weight: 600; font-size: 12px; letter-spacing: 0.6px; background: transparent; }}
QGroupBox QWidget {{ background: transparent; }}
QGroupBox QLineEdit, QGroupBox QComboBox, QGroupBox QSpinBox, QGroupBox QDoubleSpinBox,
QGroupBox QListWidget, QGroupBox QPushButton, QGroupBox QFontComboBox {{ background: {SURFACE_2}; }}

QLabel {{ background: transparent; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#timecode {{ font-family: "JetBrains Mono", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace;
                  font-size: 17px; font-weight: 600; color: {TEXT}; padding: 0 6px; }}
QLabel#modeTag {{ color: {WARM}; background: #2a1f12; border: 1px solid #4a3418; border-radius: 11px;
                 padding: 3px 12px; font-weight: 600; font-size: 12px; }}
QLabel#preview {{ background: #000; border: 1px solid {BORDER}; border-radius: 12px; padding: 1px; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
              padding: 6px 14px; min-height: 20px; font-weight: 500; }}
QPushButton[compact="true"] {{ padding: 4px 4px; }}
QPushButton:hover {{ background: #232733; border-color: {BORDER_HI}; }}
QPushButton:pressed {{ background: #2a2f3d; }}
QPushButton:checked {{ background: {ACCENT_BG}; border-color: {ACCENT}; color: #fff; }}
QPushButton:disabled {{ color: #4f5462; border-color: #1e2129; }}
QPushButton#primary, QPushButton#play {{ color: #fff; font-weight: 600; border: none;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}
QPushButton#primary:hover, QPushButton#play:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HI}, stop:1 #d27dff); }}
QPushButton#play {{ border-radius: 18px; padding: 0 18px; font-size: 14px; min-height: 36px; max-height: 36px; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {BORDER}; border-radius: 18px; padding: 0;
    min-height: 36px; max-height: 36px; min-width: 36px; max-width: 36px; }}
QPushButton#ghost:hover {{ background: {SURFACE_2}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QFontComboBox {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 9px; min-height: 20px;
    selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#inlineEdit {{ background: #0b0c10; border: 2px solid {ACCENT}; border-radius: 8px; padding: 4px 10px; font-size: 15px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER_HI}; border-radius: 8px;
    padding: 4px; selection-background-color: {ACCENT_BG}; outline: none; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 18px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}

QListWidget, QListView, QTreeView, QTextBrowser {{ background: {FIELD}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 4px; outline: none; }}
QListWidget::item {{ border-radius: 7px; padding: 2px 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: #1c1f28; }}
QListWidget::item:selected {{ background: {ACCENT_BG}; color: #fff; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QListView::indicator {{ width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid {BORDER_HI}; background: {FIELD}; }}
QCheckBox::indicator:hover, QListView::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QListView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url({check}); }}
QCheckBox::indicator:disabled {{ background: #15171c; border-color: #22252e; }}

QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: #262a35; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}
QSlider::handle:horizontal {{ background: #fff; border: 3px solid {ACCENT}; width: 10px; height: 10px;
    margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT_HI}; }}
QSlider::sub-page:horizontal:disabled {{ background: #34384a; }}

QProgressBar {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 7px; text-align: center;
    min-height: 14px; font-size: 11px; }}
QProgressBar::chunk {{ border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; border: none; border-bottom: 2px solid transparent;
    padding: 10px 18px 9px 18px; margin-right: 4px; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: #fff; border-bottom: 2px solid {ACCENT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: #2e3240; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::handle:hover {{ background: #424758; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenuBar {{ background: {BG}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER_HI}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_BG}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_HI}; border-radius: 6px; padding: 6px 8px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QDialog {{ background: {BG}; }}
QMessageBox QLabel {{ font-size: 13px; }}
"""
