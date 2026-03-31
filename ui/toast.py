"""Reusable toast notification system.

Provides non-blocking, stackable popup notifications that appear at the
bottom-right corner of the screen.  Each toast has a type (success, error,
info, warning) that controls its accent colour.  Toasts can auto-dismiss
after a configurable duration or stay persistent until the user closes them.

Usage::

    from ui.toast import show_toast, ToastType

    # Auto-dismiss after 4 seconds (default)
    show_toast("Record saved", ToastType.SUCCESS)

    # Persistent toast with a close callback
    t = show_toast("Searching…", ToastType.INFO, persistent=True,
                   on_close=lambda: print("closed"))
    # Later, update the message in-place:
    t.update_message("Found!", ToastType.SUCCESS)
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget


# ---------------------------------------------------------------------------
# Toast types
# ---------------------------------------------------------------------------

class ToastType(Enum):
    SUCCESS = auto()
    ERROR = auto()
    INFO = auto()
    WARNING = auto()


_ACCENT_COLOURS: dict[ToastType, QColor] = {
    ToastType.SUCCESS: QColor(0, 220, 100),
    ToastType.ERROR:   QColor(255, 80, 80),
    ToastType.INFO:    QColor(100, 160, 255),
    ToastType.WARNING: QColor(255, 180, 50),
}

_BG = QColor(40, 42, 58, 235)
_TEXT = QColor(205, 214, 244)

TOAST_W = 320
TOAST_H = 56
ACCENT_W = 5
MARGIN = 12
ANIM_MS = 250


# ---------------------------------------------------------------------------
# ToastWidget
# ---------------------------------------------------------------------------

class ToastWidget(QWidget):
    """A single toast notification window."""

    def __init__(
        self,
        message: str,
        toast_type: ToastType = ToastType.INFO,
        duration_ms: int = 4000,
        persistent: bool = False,
        on_close: Callable[[], None] | None = None,
    ):
        super().__init__()
        self._message = message
        self._type = toast_type
        self._on_close = on_close
        self._dismissed = False

        self.setFixedSize(TOAST_W, TOAST_H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._close_btn = QPushButton("×", self)
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.move(TOAST_W - 28, (TOAST_H - 24) // 2)
        self._close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #6c7086;"
            " font-size: 16px; font-weight: bold; border: none; }"
            "QPushButton:hover { color: #cdd6f4; }"
        )
        self._close_btn.clicked.connect(self.dismiss)

        if not persistent:
            QTimer.singleShot(duration_ms, self.dismiss)

    # -- public API --------------------------------------------------------

    def update_message(
        self, message: str, toast_type: ToastType | None = None
    ) -> None:
        """Change the displayed text and optionally the type/colour."""
        self._message = message
        if toast_type is not None:
            self._type = toast_type
        self.update()

    def activate(self) -> None:
        """Raise, activate, and grab keyboard focus so ESC works."""
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self.hide()
        if self._on_close:
            self._on_close()
        _manager.remove(self)
        self.close()
        self.deleteLater()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), 8.0, 8.0)
        painter.setClipPath(path)

        painter.fillRect(self.rect(), _BG)

        accent = _ACCENT_COLOURS.get(self._type, _ACCENT_COLOURS[ToastType.INFO])
        painter.fillRect(0, 0, ACCENT_W, self.height(), accent)

        font = QFont("Segoe UI", 11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QPen(_TEXT))

        text_rect = self.rect().adjusted(ACCENT_W + 12, 0, -36, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._message,
        )
        painter.end()

    # -- slide-in animation ------------------------------------------------

    def slide_in(self, target_pos: QPoint) -> None:
        start = QPoint(target_pos.x() + TOAST_W + 20, target_pos.y())
        self.move(start)
        self.show()
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(ANIM_MS)
        self._anim.setStartValue(start)
        self._anim.setEndValue(target_pos)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def reposition(self, target_pos: QPoint) -> None:
        """Smoothly move to a new stacking position."""
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(150)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(target_pos)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()


# ---------------------------------------------------------------------------
# ToastManager (module-level singleton)
# ---------------------------------------------------------------------------

class ToastManager:
    """Tracks active toasts and keeps them stacked at the bottom-right."""

    def __init__(self) -> None:
        self._toasts: list[ToastWidget] = []

    def show(
        self,
        message: str,
        toast_type: ToastType = ToastType.INFO,
        duration_ms: int = 4000,
        persistent: bool = False,
        on_close: Callable[[], None] | None = None,
    ) -> ToastWidget:
        toast = ToastWidget(message, toast_type, duration_ms, persistent, on_close)
        self._toasts.append(toast)
        pos = self._position_for(len(self._toasts) - 1)
        toast.slide_in(pos)
        return toast

    def remove(self, toast: ToastWidget) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._restack()

    def dismiss_all(self) -> None:
        for t in list(self._toasts):
            t.dismiss()

    def _position_for(self, index: int) -> QPoint:
        screen = QApplication.primaryScreen()
        if screen is None:
            return QPoint(100, 100)
        geo = screen.availableGeometry()
        x = geo.right() - TOAST_W - MARGIN
        y = geo.bottom() - (index + 1) * (TOAST_H + MARGIN)
        return QPoint(x, y)

    def _restack(self) -> None:
        for i, t in enumerate(self._toasts):
            t.reposition(self._position_for(i))


_manager = ToastManager()


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

def show_toast(
    message: str,
    toast_type: ToastType = ToastType.INFO,
    duration_ms: int = 4000,
    persistent: bool = False,
    on_close: Callable[[], None] | None = None,
) -> ToastWidget:
    """Show a toast notification. Returns the widget for later updates."""
    return _manager.show(message, toast_type, duration_ms, persistent, on_close)


def dismiss_all() -> None:
    """Dismiss every active toast."""
    _manager.dismiss_all()
