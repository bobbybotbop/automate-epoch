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

from modules.capture import start_capture
from modules.screen import find_image_box

META_FILENAME = "meta.json"


class TestResultOverlay(QWidget):
    """Fullscreen translucent overlay showing whether a target was detected."""

    DISPLAY_MS = 3000

    def __init__(
        self,
        found: bool,
        box: tuple[int, int, int, int] | None = None,
        parent_window=None,
    ):
        super().__init__()
        self._found = found
        self._box = box
        self._parent_window = parent_window

        screen = QApplication.primaryScreen()
        self._ratio = screen.devicePixelRatio() if screen else 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.activateWindow()

        QTimer.singleShot(self.DISPLAY_MS, self._dismiss)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))

        if self._found and self._box:
            self._paint_found(painter)
        else:
            self._paint_not_found(painter)

        painter.end()

    def _paint_found(self, painter: QPainter) -> None:
        left, top, w, h = self._box
        r = self._ratio
        left, top, w, h = int(left / r), int(top / r), int(w / r), int(h / r)

        pen = QPen(QColor(0, 220, 100), 3, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 220, 100, 45))
        painter.drawRect(left, top, w, h)

        font = painter.font()
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(0, 220, 100))

        label_y = top - 10 if top > 30 else top + h + 20
        painter.drawText(left, label_y, "FOUND")

    def _paint_not_found(self, painter: QPainter) -> None:
        font = painter.font()
        font.setPointSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 80, 80))
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignCenter, "NOT FOUND"
        )

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self._dismiss()

    def keyPressEvent(self, event) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        if self._parent_window:
            self._parent_window.showNormal()
            self._parent_window.activateWindow()
        self.close()


class TargetsTab(QWidget):
    def __init__(self, targets_dir: Path, parent_window=None):
        super().__init__()
        self.targets_dir = targets_dir
        self.parent_window = parent_window
        self._overlay = None       # prevent GC of capture overlay
        self._test_overlay = None  # prevent GC of test-result overlay

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
        """Minimize the window, detect the target on screen, show result overlay."""
        self._test_path = path
        self._test_confidence = confidence
        if self.parent_window:
            self.parent_window.showMinimized()
        QTimer.singleShot(400, self._do_test)

    def _do_test(self) -> None:
        box = find_image_box(self._test_path, self._test_confidence)
        self._test_overlay = TestResultOverlay(
            found=box is not None,
            box=box,
            parent_window=self.parent_window,
        )

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
