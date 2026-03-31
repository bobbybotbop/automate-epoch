"""Screenshot capture overlay for selecting screen regions.

Provides a fullscreen translucent overlay where the user draws a rectangle
to capture a UI element, names it, and saves it to the targets directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import ImageGrab
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QInputDialog, QWidget


class CaptureOverlay(QWidget):
    """Fullscreen translucent overlay for region selection."""

    def __init__(
        self,
        targets_dir: str | Path,
        callback: Callable[[str, Path], None] | None = None,
        parent_window=None,
    ):
        super().__init__()
        self.targets_dir = Path(targets_dir)
        self.targets_dir.mkdir(parents=True, exist_ok=True)
        self.callback = callback
        self.parent_window = parent_window

        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._selecting = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def start(self) -> None:
        """Minimize parent, show fullscreen overlay."""
        if self.parent_window:
            self.parent_window.showMinimized()
        QTimer.singleShot(300, self._show_overlay)

    def _show_overlay(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.setGeometry(geo)
        self.showFullScreen()
        self.activateWindow()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self._origin and self._current:
            rect = QRect(self._origin, self._current).normalized()
            painter.setPen(QPen(QColor(0, 180, 255), 2, Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(0, 180, 255, 40))
            painter.drawRect(rect)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._current = event.pos()
            self._selecting = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._current = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self._current = event.pos()
            self.update()
            self._finish_capture()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _finish_capture(self) -> None:
        if not self._origin or not self._current:
            self._cancel()
            return

        rect = QRect(self._origin, self._current).normalized()
        if rect.width() < 5 or rect.height() < 5:
            self._cancel()
            return

        self.hide()

        screen = QApplication.primaryScreen()
        if screen:
            ratio = screen.devicePixelRatio()
        else:
            ratio = 1.0

        bbox = (
            int(rect.x() * ratio),
            int(rect.y() * ratio),
            int((rect.x() + rect.width()) * ratio),
            int((rect.y() + rect.height()) * ratio),
        )

        img = ImageGrab.grab(bbox=bbox)

        name, ok = QInputDialog.getText(
            None, "Name Target", "Enter a name for this target:"
        )

        if ok and name.strip():
            name = name.strip().replace(" ", "_")
            save_path = self.targets_dir / f"{name}.png"
            img.save(str(save_path))
            if self.callback:
                self.callback(name, save_path)

        if self.parent_window:
            self.parent_window.showNormal()
            self.parent_window.activateWindow()

        self.close()

    def _cancel(self) -> None:
        self.hide()
        if self.parent_window:
            self.parent_window.showNormal()
            self.parent_window.activateWindow()
        self.close()


def start_capture(
    targets_dir: str | Path,
    callback: Callable[[str, Path], None] | None = None,
    parent_window=None,
) -> CaptureOverlay:
    """Launch the capture overlay. Returns the overlay widget (caller must keep a reference)."""
    overlay = CaptureOverlay(targets_dir, callback, parent_window)
    overlay.start()
    return overlay
