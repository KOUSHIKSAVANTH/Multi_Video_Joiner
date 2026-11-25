"""Dark theme and the app icon (drawn in code, so no image files are needed at runtime)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPixmap

BG = "#111318"
PANEL = "#181b22"
RAISED = "#20242d"
BORDER = "#2b303b"
TEXT = "#e7e9ef"
MUTED = "#8c93a4"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#8f82ff"
ACCENT_2 = "#c16cff"
SUCCESS = "#3ecf8e"
WARNING = "#f5b44a"
DANGER = "#ff6b6b"


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(PANEL))
    pal.setColor(QPalette.AlternateBase, QColor("#1c2028"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(RAISED))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor("#646b7b"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5b6170"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5b6170"))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}

QMenuBar {{ background: {BG}; color: {MUTED}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QLabel#Title {{ font-size: 17pt; font-weight: 700; color: {TEXT}; }}
QLabel#Subtitle {{ color: {MUTED}; }}
QLabel#Hint {{ color: {MUTED}; font-size: 9pt; }}
QLabel#Summary {{ color: {MUTED}; }}
QLabel#Status {{ color: {TEXT}; }}
QLabel#Badge {{
    background: {RAISED}; color: {MUTED}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 2px 10px; font-size: 9pt;
}}

QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#CardTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {BG}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 8px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{ color: #5b6170; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent;
}}

QPushButton {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #282d38; border-color: #3a4150; }}
QPushButton:pressed {{ background: #1b1f27; }}
QPushButton:disabled {{ color: #5b6170; background: #1a1d24; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
    color: white; border: none; font-weight: 700; font-size: 11pt; padding: 10px 26px;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #cd85ff);
}}
QPushButton#Primary:disabled {{ background: #2e2a4d; color: #8f8aa8; }}
QPushButton#Accent {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Danger:hover {{ background: #3a2026; border-color: {DANGER}; color: {DANGER}; }}
QPushButton#Flat {{ background: transparent; border: none; color: {MUTED}; padding: 6px 8px; }}
QPushButton#Flat:hover {{ color: {TEXT}; background: {RAISED}; }}

QPushButton#Icon {{ padding: 0; font-weight: 700; }}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}

QTreeWidget {{
    background: {PANEL}; alternate-background-color: #1c2028; border: 1px solid {BORDER};
    border-radius: 10px; padding: 4px; outline: 0;
}}
QTreeWidget::item {{ padding: 7px 4px; border: none; }}
QTreeWidget::item:selected {{ background: #2d2a52; color: {TEXT}; }}
QTreeWidget::item:hover:!selected {{ background: #22262f; }}
QHeaderView::section {{
    background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 4px; font-weight: 600; font-size: 9pt;
}}

QProgressBar {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
    height: 12px; text-align: center; color: {TEXT}; font-size: 8.5pt;
}}
QProgressBar::chunk {{
    border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333948; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #434a5c; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #333948; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QPlainTextEdit#Log {{ font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt; color: #b7bdcc; }}
"""


def render_icon(size: int = 256) -> QPixmap:
    """App icon: a gradient tile with three timeline clips merging into a play button."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0

    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    grad.setColorAt(0.0, QColor("#6a5cff"))
    grad.setColorAt(1.0, QColor("#c35cff"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)

    # Three clips on a timeline (different lengths), slightly translucent.
    p.setBrush(QColor(255, 255, 255, 235))
    for y, width in ((70, 70), (116, 96), (162, 56)):
        p.drawRoundedRect(QRectF(40 * s, y * s, width * s, 26 * s), 9 * s, 9 * s)

    # Play triangle they merge into.
    path = QPainterPath()
    path.moveTo(150 * s, 74 * s)
    path.lineTo(214 * s, 128 * s)
    path.lineTo(150 * s, 182 * s)
    path.closeSubpath()
    p.setBrush(QColor("#ffffff"))
    p.drawPath(path)
    p.end()
    return pix


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_icon(size))
    return icon
