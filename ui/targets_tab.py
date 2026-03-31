"""Targets tab — thumbnail grid of saved screen target images."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import pyautogui

from modules.capture import start_capture
from modules.screen import find_image_box
from ui.toast import ToastType, show_toast

META_FILENAME = "meta.json"

POLL_INTERVAL_MS = 500
TEST_TIMEOUT_MS = 15_000
ANIM_SETTLE_MS = 350
INPUT_WATCH_MS = 200
PIXEL_TOLERANCE = 12
CURSOR_MOVE_THRESHOLD = 5


class DetectionOverlay(QWidget):
    """Fullscreen click-through overlay that draws a highlight box.

    All mouse and keyboard input passes through to the desktop thanks to
    ``WindowTransparentForInput``.  Call :meth:`update_box` each poll tick;
    pass *None* to clear the highlight.
    """

    def __init__(self) -> None:
        super().__init__()
        self._box: tuple[int, int, int, int] | None = None

        screen = QApplication.primaryScreen()
        self._ratio = screen.devicePixelRatio() if screen else 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()

    def update_box(self, box: tuple[int, int, int, int] | None) -> None:
        self._box = box
        self.update()

    def paintEvent(self, event) -> None:
        if self._box is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        left, top, w, h = self._box
        r = self._ratio
        left, top, w, h = int(left / r), int(top / r), int(w / r), int(h / r)

        painter.setPen(QPen(QColor(0, 220, 100), 3, Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(0, 220, 100, 45))
        painter.drawRect(left, top, w, h)

        font = painter.font()
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(0, 220, 100))
        label_y = top - 10 if top > 30 else top + h + 20
        painter.drawText(left, label_y, "FOUND")

        painter.end()

    def teardown(self) -> None:
        self.hide()
        self.close()
        self.deleteLater()


class TargetsTab(QWidget):
    def __init__(self, targets_dir: Path, parent_window=None):
        super().__init__()
        self.targets_dir = targets_dir
        self.parent_window = parent_window
        self._overlay = None            # capture overlay ref
        self._detection_overlay = None  # live-test overlay ref
        self._test_toast = None         # live-test toast ref
        self._poll_timer: QTimer | None = None
        self._input_timer: QTimer | None = None
        self._last_cursor_pos: tuple[int, int] | None = None
        self._last_pixel: tuple[int, int, int] | None = None
        self._pixel_sample_pt: tuple[int, int] | None = None

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        heading = QLabel("Screen Targets")
        heading.setObjectName("heading")
        header.addWidget(heading)
        header.addStretch()

        btn_capture = QPushButton("Capture New Target")
        btn_capture.setObjectName("primary")
        btn_capture.clicked.connect(self._start_capture)
        header.addWidget(btn_capture)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh)
        header.addWidget(btn_refresh)

        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self._grid_container)
        root.addWidget(scroll)

    def refresh(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        meta = self._load_meta()
        targets = sorted(self.targets_dir.glob("*.png"))

        col_count = 4
        for i, path in enumerate(targets):
            card = self._make_card(path, meta.get(path.stem, 0.85))
            self._grid.addWidget(card, i // col_count, i % col_count)

    def _make_card(self, path: Path, confidence: float) -> QWidget:
        card = QWidget()
        card.setFixedSize(220, 200)
        card.setStyleSheet(
            "QWidget { background-color: #313244; border-radius: 8px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)

        thumb = QLabel()
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                200, 110, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(scaled)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(thumb)

        name_label = QLabel(path.stem)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(name_label)

        bottom = QHBoxLayout()
        spin = QDoubleSpinBox()
        spin.setRange(0.5, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(confidence)
        spin.setToolTip("Confidence threshold")
        spin.valueChanged.connect(lambda val, n=path.stem: self._save_confidence(n, val))
        bottom.addWidget(spin)

        btn_test = QPushButton("Test")
        btn_test.setFixedWidth(44)
        btn_test.setToolTip("Check if this target is visible on screen")
        btn_test.clicked.connect(
            lambda _, p=path, s=spin: self._test_target(p, s.value())
        )
        bottom.addWidget(btn_test)

        btn_del = QPushButton("Del")
        btn_del.setObjectName("danger")
        btn_del.setFixedWidth(44)
        btn_del.clicked.connect(lambda _, p=path: self._delete_target(p))
        bottom.addWidget(btn_del)

        layout.addLayout(bottom)
        return card

    def _test_target(self, path: Path, confidence: float) -> None:
        """Minimize the window, then live-poll the screen for the target."""
        self._stop_test()
        self._test_path = path
        self._test_confidence = confidence
        self._test_name = path.stem
        if self.parent_window:
            self.parent_window.showMinimized()
        QTimer.singleShot(400, self._start_live_test)

    def _start_live_test(self) -> None:
        self._detection_overlay = DetectionOverlay()
        self._test_toast = show_toast(
            f"Searching for {self._test_name}\u2026",
            ToastType.INFO,
            persistent=True,
            on_close=self._stop_test,
        )
        QTimer.singleShot(ANIM_SETTLE_MS, self._test_toast.activate)

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_timer.start()

        self._test_deadline = QTimer()
        self._test_deadline.setSingleShot(True)
        self._test_deadline.setInterval(TEST_TIMEOUT_MS)
        self._test_deadline.timeout.connect(self._stop_test)
        self._test_deadline.start()

        self._poll_tick()

    def _poll_tick(self) -> None:
        box = find_image_box(self._test_path, self._test_confidence)
        if self._detection_overlay:
            self._detection_overlay.update_box(box)
        if self._test_toast:
            if box is not None:
                self._test_toast.update_message(
                    f"{self._test_name} detected  (Esc to close)",
                    ToastType.SUCCESS,
                )
                self._freeze_test()
            else:
                self._test_toast.update_message(
                    f"Searching for {self._test_name}\u2026  (Esc to cancel)",
                    ToastType.WARNING,
                )

    def _freeze_test(self) -> None:
        """Image found — stop fast polling, start watching for user input."""
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if hasattr(self, "_test_deadline") and self._test_deadline is not None:
            self._test_deadline.stop()
            self._test_deadline = None
        if self._test_toast:
            self._test_toast.activate()

        pos = pyautogui.position()
        self._last_cursor_pos = (pos.x, pos.y)

        if self._detection_overlay and self._detection_overlay._box:
            left, top, w, h = self._detection_overlay._box
            cx, cy = left + w // 2, top + h // 2
            self._pixel_sample_pt = (cx, cy)
            try:
                self._last_pixel = pyautogui.pixel(cx, cy)
            except Exception:
                self._last_pixel = None
        else:
            self._pixel_sample_pt = None
            self._last_pixel = None

        self._input_timer = QTimer()
        self._input_timer.setInterval(INPUT_WATCH_MS)
        self._input_timer.timeout.connect(self._input_watch_tick)
        self._input_timer.start()

    def _input_watch_tick(self) -> None:
        """Lightweight check: re-detect when cursor moves or screen content changes."""
        pos = pyautogui.position()
        cursor_now = (pos.x, pos.y)
        cursor_moved = (
            self._last_cursor_pos is not None
            and (
                abs(cursor_now[0] - self._last_cursor_pos[0]) > CURSOR_MOVE_THRESHOLD
                or abs(cursor_now[1] - self._last_cursor_pos[1]) > CURSOR_MOVE_THRESHOLD
            )
        )

        pixel_changed = False
        if self._pixel_sample_pt is not None:
            try:
                px = pyautogui.pixel(*self._pixel_sample_pt)
                pixel_changed = not self._pixels_similar(px, self._last_pixel)
            except Exception:
                px = None

        if not cursor_moved and not pixel_changed:
            return
        self._last_cursor_pos = cursor_now
        if pixel_changed and px is not None:
            self._last_pixel = px

        box = find_image_box(self._test_path, self._test_confidence)
        if self._detection_overlay:
            self._detection_overlay.update_box(box)

        if box is not None:
            left, top, w, h = box
            cx, cy = left + w // 2, top + h // 2
            self._pixel_sample_pt = (cx, cy)
            try:
                self._last_pixel = pyautogui.pixel(cx, cy)
            except Exception:
                self._last_pixel = None

        if self._test_toast:
            if box is not None:
                self._test_toast.update_message(
                    f"{self._test_name} detected  (Esc to close)",
                    ToastType.SUCCESS,
                )
            else:
                self._test_toast.update_message(
                    f"{self._test_name} not visible  (Esc to close)",
                    ToastType.WARNING,
                )

    @staticmethod
    def _pixels_similar(
        a: tuple[int, int, int] | None,
        b: tuple[int, int, int] | None,
    ) -> bool:
        """True when two RGB tuples are within PIXEL_TOLERANCE per channel."""
        if a is None or b is None:
            return a is b
        return all(abs(ca - cb) <= PIXEL_TOLERANCE for ca, cb in zip(a, b))

    def _stop_test(self) -> None:
        if self._input_timer is not None:
            self._input_timer.stop()
            self._input_timer = None
        self._last_cursor_pos = None
        self._last_pixel = None
        self._pixel_sample_pt = None
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if hasattr(self, "_test_deadline") and self._test_deadline is not None:
            self._test_deadline.stop()
            self._test_deadline = None
        if self._detection_overlay is not None:
            self._detection_overlay.teardown()
            self._detection_overlay = None
        if self._test_toast is not None:
            toast, self._test_toast = self._test_toast, None
            toast._on_close = None
            toast.dismiss()
        if self.parent_window:
            self.parent_window.showNormal()
            self.parent_window.activateWindow()

    def _start_capture(self):
        self._overlay = start_capture(
            self.targets_dir,
            callback=self._on_capture_done,
            parent_window=self.parent_window,
        )

    def _on_capture_done(self, name: str, path: Path):
        self.refresh()

    def _delete_target(self, path: Path):
        reply = QMessageBox.question(
            self, "Delete Target",
            f"Delete '{path.stem}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            path.unlink(missing_ok=True)
            meta = self._load_meta()
            meta.pop(path.stem, None)
            self._save_meta(meta)
            self.refresh()

    def _save_confidence(self, name: str, value: float):
        meta = self._load_meta()
        meta[name] = round(value, 2)
        self._save_meta(meta)

    def _load_meta(self) -> dict:
        meta_path = self.targets_dir / META_FILENAME
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_meta(self, meta: dict):
        meta_path = self.targets_dir / META_FILENAME
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
