"""Targets tab — thumbnail grid of saved screen target images."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
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

META_FILENAME = "meta.json"


class TargetsTab(QWidget):
    def __init__(self, targets_dir: Path, parent_window=None):
        super().__init__()
        self.targets_dir = targets_dir
        self.parent_window = parent_window
        self._overlay = None  # prevent GC of overlay widget

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

        btn_del = QPushButton("Del")
        btn_del.setObjectName("danger")
        btn_del.setFixedWidth(44)
        btn_del.clicked.connect(lambda _, p=path: self._delete_target(p))
        bottom.addWidget(btn_del)

        layout.addLayout(bottom)
        return card

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
