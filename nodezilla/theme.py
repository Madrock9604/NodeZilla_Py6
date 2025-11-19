from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication

def _is_dark_from_palette(pal: QPalette) -> bool:
    col = pal.color(QPalette.ColorRole.Window)
    luma = 0.2126 * col.redF() + 0.7152 * col.greenF() + 0.0722 * col.blueF()
    return luma < 0.5

def detect_dark_mode(app: QApplication) -> bool:
    # Qt ≥ 6.5 has colorScheme(); we fall back to palette if unavailable.
    scheme = getattr(app.styleHints(), "colorScheme", None)
    if callable(scheme):
        try:
            return scheme() == Qt.ColorScheme.Dark
        except Exception:
            pass
    return _is_dark_from_palette(app.palette())

def _auto_text_color(bg: QColor) -> QColor:
    r, g, b = bg.red(), bg.green(), bg.blue()
    luma = (299*r + 587*g + 114*b) / 1000.0
    return QColor(20, 20, 20) if luma > 128 else QColor(235, 235, 235)

class Theme:
    def __init__(
        self,
        name: str,
        bg: QColor,
        component_fill: QColor,
        component_stroke: QColor,
        wire: QColor,
        wire_selected: QColor,
        text: QColor | None = None,   # <-- allow None
    ):
        self.name = name
        self.bg = bg
        self.component_fill = component_fill
        self.component_stroke = component_stroke
        self.wire = wire
        self.wire_selected = wire_selected
        # <-- guarantee a QColor here
        self.text = text if isinstance(text, QColor) else _auto_text_color(bg)

LIGHT = Theme(
    name="light",
    bg=QColor(245, 246, 248),
    component_fill=QColor(240, 240, 240),
    component_stroke=QColor(30, 30, 30),
    wire=QColor("#0068a6"),
    wire_selected=QColor("#b05f00"),
)

DARK = Theme(
    name="dark",
    bg=QColor(32, 33, 36),
    component_fill=QColor(255, 255, 255),
    component_stroke=QColor(200, 200, 200),
    wire=QColor(220, 220, 220),        # light gray on dark bg  ← NOT black
    wire_selected=QColor(0, 180, 255), # brighter highlight
)

class ThemeWatcher(QObject):
    """Listens for OS theme changes and calls a callback with the active Theme."""
    def __init__(self, app: QApplication, on_theme_changed):
        super().__init__()
        self._app = app
        self._callback = on_theme_changed
        app.installEventFilter(self)
        # initial apply
        self._callback(self.current_theme())

    def current_theme(self) -> Theme:
        return DARK if detect_dark_mode(self._app) else LIGHT

    def eventFilter(self, obj, event):
        if event.type() in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        ):
            self._callback(self.current_theme())
        return super().eventFilter(obj, event)
