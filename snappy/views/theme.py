"""Dark theme (charcoal + accent color).

The accent color can be changed live (`reapply`): the stylesheet is regenerated, and the
hand-drawn widgets read `theme.ACCENT` again every time they paint.
"""

import hashlib

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..i18n import tr

BG = "#0e1014"          # window background
SURFACE = "#161920"     # sidebar, panels
SURFACE_2 = "#1d212a"   # cards, fields
SURFACE_3 = "#272c37"   # hover
BORDER = "#2a2f3a"
TEXT = "#eef0f4"
TEXT_DIM = "#9aa1ad"
TEXT_FAINT = "#646b78"
ACCENT = "#ff5a1f"      # orange (default)
ACCENT_HOVER = "#ff7442"
ACCENT_DIM = "#3a1d12"
ACCENT_TEXT = "white"   # text readable on the accent (dark if the accent is light)
DANGER = "#ff4d5e"
SUCCESS = "#3ddc84"
FAVORITE = "#ffc53d"

FONT_FAMILY = "Segoe UI Variable Text, Segoe UI, Inter, Arial"

# Offered accent colors: (saved key, name, color). Older versions saved French keys: see
# `models.settings.LEGACY_ACCENTS`.
ACCENTS = [
    ("orange", tr("Orange"), "#ff5a1f"),
    ("red", tr("Red"), "#e5484d"),
    ("pink", tr("Pink"), "#e03d8c"),
    ("violet", tr("Violet"), "#9161eb"),
    ("blue", tr("Blue"), "#3685e3"),
    ("cyan", tr("Cyan"), "#1cb3bf"),
    ("green", tr("Green"), "#2ec27e"),
    ("amber", tr("Amber"), "#e6a60a"),
]


def system_accent() -> str:
    """System accent color (on Windows: Personalization › Colors)."""
    from ..platforms import system

    try:
        return system.accent_color()
    except Exception:
        return "#0078d4"


def resolve_accent(value: str) -> str:
    """"system", a key of ACCENTS or a "#rrggbb" color -> color to use."""
    from ..models.settings import LEGACY_ACCENTS

    value = LEGACY_ACCENTS.get(value, value)
    if value == "system":
        return system_accent()
    for key, _name, color in ACCENTS:
        if key == value:
            return color
    if QColor.isValidColorName(value or ""):
        return QColor(value).name()
    return ACCENTS[0][2]


def set_accent(color: str) -> None:
    global ACCENT, ACCENT_HOVER, ACCENT_DIM, ACCENT_TEXT
    c = QColor(color)
    bg = QColor(BG)
    ACCENT = c.name()
    ACCENT_HOVER = c.lighter(115).name()
    mix = 0.24   # accent strongly faded into the background: selection, checked button
    ACCENT_DIM = QColor(round(bg.red() + (c.red() - bg.red()) * mix), round(bg.green() + (c.green() - bg.green()) * mix),
                        round(bg.blue() + (c.blue() - bg.blue()) * mix)).name()
    luminance = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    ACCENT_TEXT = "#111318" if luminance > 0.62 else "white"


def stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT_FAMILY};
    color: {TEXT};
    outline: none;
}}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#brand {{ font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#brandDot {{ color: {ACCENT}; font-size: 20px; font-weight: 800; }}
QLabel#sectionLabel {{ color: {TEXT_FAINT}; font-size: 11px; font-weight: 600;
                       letter-spacing: 1.2px; padding: 14px 18px 6px 18px; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
QLineEdit#titleEdit {{
    font-size: 22px; font-weight: 700; background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 0 4px; selection-background-color: {ACCENT};
}}
QLineEdit#titleEdit:hover {{ background: {SURFACE}; }}
QLineEdit#titleEdit[editing="true"] {{ background: {SURFACE_2}; border-color: {ACCENT}; }}
QLabel#pageSubtitle {{ color: {TEXT_DIM}; font-size: 13px; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#faint {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#empty {{ color: {TEXT_DIM}; font-size: 15px; }}

/* Side navigation */
QPushButton#navButton {{
    text-align: left; padding: 9px 18px; border: none; border-radius: 8px;
    margin: 1px 10px; background: transparent; color: {TEXT_DIM}; font-size: 14px;
}}
QPushButton#navButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton#navButton:checked {{ background: {ACCENT_DIM}; color: {TEXT}; font-weight: 600; }}

/* Buttons */
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 14px; font-size: 13px;
}}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#primary {{
    background: {ACCENT}; border: none; color: {ACCENT_TEXT}; font-weight: 600; padding: 9px 18px;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#danger:hover {{ background: {DANGER}; border-color: {DANGER}; color: white; }}
QPushButton#ghost {{ background: transparent; border: none; color: {TEXT_DIM}; }}
QPushButton#ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#iconButton {{ padding: 6px; min-width: 34px; min-height: 34px; border-radius: 17px; }}

/* Segmented buttons (presets, period) */
QPushButton#seg_first, QPushButton#seg_mid, QPushButton#seg_last {{
    border-radius: 0; padding: 6px 12px; background: {SURFACE_2}; color: {TEXT_DIM};
}}
/* No negative margin (Qt would cut the first pixel column): no left border instead, except on
   the chosen button, fully outlined with the accent. */
QPushButton#seg_mid, QPushButton#seg_last {{ border-left-width: 0; }}
QPushButton#seg_mid:checked, QPushButton#seg_last:checked {{ border-left-width: 1px; }}
QPushButton#seg_first {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton#seg_last {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
QPushButton#seg_first:checked, QPushButton#seg_mid:checked, QPushButton#seg_last:checked {{
    background: {ACCENT_DIM}; color: {TEXT}; border-color: {ACCENT}; font-weight: 600;
}}
QPushButton#seg_first:disabled, QPushButton#seg_mid:disabled, QPushButton#seg_last:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#trackToggle {{ padding: 0; border-radius: 6px; font-weight: 700; }}
QPushButton#trackToggle:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QPushButton#ghost:checked {{ color: {ACCENT}; background: {ACCENT_DIM}; border: 1px solid {ACCENT}; }}
QPushButton#ghost:checked:hover {{ background: {ACCENT_DIM}; }}
QToolButton#toolChip {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; font-size: 13px;
}}
QToolButton#toolChip::menu-indicator {{ image: none; }}
QCalendarWidget QWidget {{ background: {SURFACE_2}; }}

/* Status pill */
QLabel#statusPill {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 5px 12px; font-size: 12px; color: {TEXT_DIM};
}}

/* Fields */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QKeySequenceEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {ACCENT}; font-size: 13px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#search {{ padding-left: 12px; min-width: 220px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox:hover, QSpinBox:hover {{ border-color: {TEXT_FAINT}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM};
}}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 4px 4px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 4px 4px 0; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {SURFACE_3}; border-radius: 4px; }}

QCheckBox {{ spacing: 10px; font-size: 13px; }}
QCheckBox::indicator {{
    width: 36px; height: 20px; border-radius: 10px; background: {SURFACE_3}; border: 1px solid {BORDER};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover, QSplitter::handle:pressed {{ background: {ACCENT_DIM}; }}
QSlider::handle:horizontal {{
    background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}

/* Lists and scrolling */
QListView {{ background: transparent; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Settings groups */
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#settingHint {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel#rowLabel[modified="true"] {{ color: {TEXT}; font-weight: 600; }}

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
QMessageBox, QInputDialog, QDialog {{ background: {SURFACE}; }}
QColorDialog QWidget {{ font-size: 12px; }}
"""


def apply(app: QApplication, accent: str | None = None) -> None:
    """accent: saved setting ("orange", "system", "#rrggbb"...)."""
    if accent:
        set_accent(resolve_accent(accent))
    app.setStyle("Fusion")
    _apply_palette(app)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyleSheet(stylesheet() + _assets_style())


def reapply(app: QApplication, accent: str) -> None:
    """Changes the accent color of the running application."""
    set_accent(resolve_accent(accent))
    _apply_palette(app)
    app.setStyleSheet(stylesheet() + _assets_style())
    for w in app.allWidgets():
        w.update()


def _apply_palette(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor(SURFACE_2))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(SURFACE_2))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor(SURFACE_2))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    app.setPalette(pal)


def _switch(track: str, knob: str, on: bool, border: str) -> str:
    cx = 26 if on else 10
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20" viewBox="0 0 36 20">'
            f'<rect x="0.5" y="0.5" width="35" height="19" rx="9.5" fill="{track}" stroke="{border}"/>'
            f'<circle cx="{cx}" cy="10" r="7" fill="{knob}"/></svg>')


def _chevron(color: str, up: bool) -> str:
    path = "M2 6.5 L6 2.5 L10 6.5" if up else "M2 3.5 L6 7.5 L10 3.5"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="10" viewBox="0 0 12 10">'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg>')


def _assets_style() -> str:
    """Stylesheet images, drawn as SVG: switches (colored track + white knob) and drop-down arrows.
    The file name depends on the content: Qt caches images by path, so a color change must
    change the path."""
    from ..models.settings import cache_dir

    folder = cache_dir() / "ui"
    variants = {
        "off": _switch(SURFACE_3, "#c9ced8", False, BORDER),
        "on": _switch(ACCENT, "white", True, ACCENT),
        "off_disabled": _switch(SURFACE_2, TEXT_FAINT, False, BORDER),
        "on_disabled": _switch(ACCENT_DIM, TEXT_DIM, True, ACCENT_DIM),
        "down": _chevron(TEXT_DIM, False),
        "down_disabled": _chevron(TEXT_FAINT, False),
        "up": _chevron(TEXT_DIM, True),
    }
    urls = {}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, svg in variants.items():
            digest = hashlib.sha1(svg.encode()).hexdigest()[:10]
            path = folder / f"{name}_{digest}.svg"
            if not path.is_file():
                path.write_text(svg, encoding="utf-8")
            urls[name] = path.as_posix()
    except OSError:
        return ""
    return f"""
QCheckBox::indicator {{ border: none; background: transparent; image: url({urls["off"]}); }}
QCheckBox::indicator:checked {{ image: url({urls["on"]}); }}
QCheckBox::indicator:disabled {{ image: url({urls["off_disabled"]}); }}
QCheckBox::indicator:checked:disabled {{ image: url({urls["on_disabled"]}); }}
QComboBox::down-arrow {{ image: url({urls["down"]}); width: 12px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({urls["down_disabled"]}); }}
QSpinBox::up-arrow {{ image: url({urls["up"]}); width: 10px; height: 8px; }}
QSpinBox::down-arrow {{ image: url({urls["down"]}); width: 10px; height: 8px; }}
"""
