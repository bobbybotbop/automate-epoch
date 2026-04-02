"""Targets tab — thumbnail grid of saved screen target images."""

from __future__ import annotations

import ctypes
import json
import threading
from pathlib import Path

from ctypes import wintypes
from PyQt6.QtCore import QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from modules.capture import start_capture
from modules import screen
from ui.toast import ToastType, show_toast

META_FILENAME = "meta.json"

POLL_INTERVAL_MS = 500
TEST_TIMEOUT_MS = 15_000
ANIM_SETTLE_MS = 350


class _InputBridge(QObject):
    triggered = pyqtSignal()


class GlobalInputWatcher:
    """Windows global low-level input hook (mouse + keyboard)."""

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    HC_ACTION = 0
    WM_QUIT = 0x0012

    # Keyboard messages
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104

    # Mouse messages
    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_RBUTTONDOWN = 0x0204
    WM_MBUTTONDOWN = 0x0207
    WM_MOUSEWHEEL = 0x020A
    WM_XBUTTONDOWN = 0x020B

    def __init__(self, on_input) -> None:
        self._on_input = on_input
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._started = threading.Event()
        self._running = False

        self._mouse_hook = None
        self._keyboard_hook = None
        self._mouse_proc = None
        self._keyboard_proc = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started.wait(timeout=1.0)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = int(kernel32.GetCurrentThreadId())

        LowLevelProc = ctypes.WINFUNCTYPE(
            wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        mouse_messages = {
            self.WM_MOUSEMOVE,
            self.WM_LBUTTONDOWN,
            self.WM_RBUTTONDOWN,
            self.WM_MBUTTONDOWN,
            self.WM_MOUSEWHEEL,
            self.WM_XBUTTONDOWN,
        }
        key_messages = {self.WM_KEYDOWN, self.WM_SYSKEYDOWN}

        def mouse_proc(n_code, w_param, l_param):
            if n_code == self.HC_ACTION and int(w_param) in mouse_messages and self._running:
                self._on_input()
            return user32.CallNextHookEx(0, n_code, w_param, l_param)

        def keyboard_proc(n_code, w_param, l_param):
            if n_code == self.HC_ACTION and int(w_param) in key_messages and self._running:
                self._on_input()
            return user32.CallNextHookEx(0, n_code, w_param, l_param)

        self._mouse_proc = LowLevelProc(mouse_proc)
        self._keyboard_proc = LowLevelProc(keyboard_proc)

        self._mouse_hook = user32.SetWindowsHookExW(
            self.WH_MOUSE_LL, self._mouse_proc, kernel32.GetModuleHandleW(None), 0
        )
        self._keyboard_hook = user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL, self._keyboard_proc, kernel32.GetModuleHandleW(None), 0
        )
        self._started.set()

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
        if self._keyboard_hook:
            user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._keyboard_hook = None


class DetectionOverlay(QWidget):
    """Fullscreen click-through overlay that draws a highlight box.

    All mouse and keyboard input passes through to the desktop thanks to
    ``WindowTransparentForInput``.  Call :meth:`update_box` each poll tick;
    pass *None* to clear the highlight.
    """

    def __init__(self) -> None:
        super().__init__()
        self._box: tuple[int, int, int, int] | None = None
        self._point: tuple[int, int] | None = None

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

        screens = QApplication.screens()
        if screens:
            vr = QRect()
            for s in screens:
                vr = vr.united(s.geometry())
            self.setGeometry(vr)
        elif screen:
            self.setGeometry(screen.geometry())
        self.show()

    def update_detection(
        self,
        box: tuple[int, int, int, int] | None,
        point: tuple[int, int] | None = None,
    ) -> None:
        self._box = box
        self._point = point
        self.update()

    def update_box(self, box: tuple[int, int, int, int] | None) -> None:
        self.update_detection(box, None)

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

        if self._point is not None:
            px, py = self._point
            px, py = int(px / r), int(py / r)
            dot_r = 6
            painter.setPen(QPen(QColor(255, 60, 60), 2))
            painter.setBrush(QColor(255, 60, 60, 220))
            painter.drawEllipse(px - dot_r, py - dot_r, dot_r * 2, dot_r * 2)

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
        self._input_watcher: GlobalInputWatcher | None = None
        self._input_bridge = _InputBridge()
        self._input_bridge.triggered.connect(self._input_watch_tick)
        self._input_refresh_lock = threading.Lock()
        self._input_refresh_pending = False
        self._test_mode: str = "image"  # image|text

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

        text_group = QGroupBox("Test Text Search (OCR)")
        tl = QVBoxLayout(text_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Text:"))
        self._text_query = QLineEdit()
        self._text_query.setPlaceholderText("Invoice, Customer, etc.")
        row1.addWidget(self._text_query)

        btn_test_text = QPushButton("Test Text")
        btn_test_text.setObjectName("primary")
        btn_test_text.clicked.connect(self._on_test_text_clicked)
        row1.addWidget(btn_test_text)
        tl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Match:"))
        self._text_match = QComboBox()
        self._text_match.addItems(["contains", "exact"])
        row2.addWidget(self._text_match)

        self._text_case = QCheckBox("Case")
        row2.addWidget(self._text_case)
        row2.addStretch()
        tl.addLayout(row2)

        hint = QLabel(
            "Searches the full screen (all monitors), like image target tests."
        )
        hint.setObjectName("subtext")
        hint.setWordWrap(True)
        tl.addWidget(hint)

        root.addWidget(text_group)

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
        self._test_mode = "image"
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
        if self._test_mode == "text":
            self._poll_text_tick()
            return

        box = screen.find_image_box(self._test_path, self._test_confidence)
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

    def _on_test_text_clicked(self) -> None:
        query = self._text_query.text().strip()
        if not query:
            show_toast("Enter text to search for", ToastType.WARNING)
            return
        self._test_text(
            query=query,
            match_mode=self._text_match.currentText(),
            case_sensitive=self._text_case.isChecked(),
        )

    def _test_text(
        self,
        query: str,
        match_mode: str,
        case_sensitive: bool,
    ) -> None:
        self._stop_test()
        self._test_mode = "text"
        self._text_test_query = query
        self._text_test_match = match_mode
        self._text_test_case = case_sensitive
        self._test_name = query
        if self.parent_window:
            self.parent_window.showMinimized()
        QTimer.singleShot(400, self._start_live_test)

    def _poll_text_tick(self) -> None:
        point = None
        try:
            box, point = screen.find_text_box_and_point_on_screen(
                self._text_test_query,
                region=None,
                match_mode=self._text_test_match,
                case_sensitive=self._text_test_case,
            )
        except Exception as exc:
            box = None
            if self._test_toast:
                self._test_toast.update_message(str(exc), ToastType.ERROR)

        if self._detection_overlay:
            self._detection_overlay.update_detection(box, point)

        if self._test_toast:
            if box is not None:
                self._test_toast.update_message(
                    "Text detected  (Esc to close)",
                    ToastType.SUCCESS,
                )
                self._freeze_test()
            else:
                self._test_toast.update_message(
                    "Searching for text…  (Esc to cancel)",
                    ToastType.WARNING,
                )

    def _freeze_test(self) -> None:
        """Image found — stop polling, then re-check only on user input."""
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if hasattr(self, "_test_deadline") and self._test_deadline is not None:
            self._test_deadline.stop()
            self._test_deadline = None
        if self._test_toast:
            self._test_toast.activate()

        self._start_input_watch()

    def _start_input_watch(self) -> None:
        self._stop_input_watch()
        self._input_watcher = GlobalInputWatcher(self._on_global_input)
        self._input_watcher.start()

    def _stop_input_watch(self) -> None:
        if self._input_watcher is not None:
            self._input_watcher.stop()
            self._input_watcher = None

    def _on_global_input(self) -> None:
        with self._input_refresh_lock:
            if self._input_refresh_pending:
                return
            self._input_refresh_pending = True
        self._input_bridge.triggered.emit()

    def _input_watch_tick(self) -> None:
        """Re-detect current target after a real input event."""
        with self._input_refresh_lock:
            self._input_refresh_pending = False

        point = None
        if self._test_mode == "text":
            try:
                box, point = screen.find_text_box_and_point_on_screen(
                    self._text_test_query,
                    region=None,
                    match_mode=self._text_test_match,
                    case_sensitive=self._text_test_case,
                )
            except Exception:
                box = None
        else:
            box = screen.find_image_box(self._test_path, self._test_confidence)
        if self._detection_overlay:
            self._detection_overlay.update_detection(box, point)

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

    def _stop_test(self) -> None:
        self._stop_input_watch()
        with self._input_refresh_lock:
            self._input_refresh_pending = False
        self._test_mode = "image"
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
