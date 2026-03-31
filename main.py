"""FlowDesk — Local desktop RPA tool.

Main entry point: launches PyQt6 application with tabbed interface.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_venv() -> None:
    """If a local .venv exists, re-run this file with that interpreter.

    Lets you run `python main.py` with a global Python and still use the
    project's virtualenv without manually activating it first.
    """
    base = Path(__file__).resolve().parent
    if sys.platform == "win32":
        venv_python = base / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = base / ".venv" / "bin" / "python"

    if not venv_python.is_file():
        return

    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        return

    os.execv(str(venv_python), [str(venv_python), *sys.argv])


_ensure_venv()

def _silence_known_qt_warnings() -> None:
    # Suppress noisy (but non-fatal) Qt Windows DPI warnings like:
    # "qt.qpa.window: SetProcessDpiAwarenessContext() failed: Access is denied."
    #
    # Must be set before importing PyQt6 so Qt picks it up during initialization.
    rule = "qt.qpa.window.warning=false"
    existing = os.environ.get("QT_LOGGING_RULES", "").strip()
    if not existing:
        os.environ["QT_LOGGING_RULES"] = rule
        return

    parts = [p.strip() for p in existing.split(";") if p.strip()]
    if rule not in parts:
        parts.append(rule)
        os.environ["QT_LOGGING_RULES"] = ";".join(parts)


_silence_known_qt_warnings()

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
)

from ui.automations_tab import AutomationsTab
from ui.import_export_tab import ImportExportTab
from ui.parser_tab import ParserTab
from ui.runner_tab import RunnerTab
from ui.targets_tab import TargetsTab

BASE_DIR = Path(__file__).resolve().parent
AUTOMATIONS_DIR = BASE_DIR / "automations"
TARGETS_DIR = BASE_DIR / "targets"
RULES_DIR = BASE_DIR / "rules"
LOGS_DIR = BASE_DIR / "logs"

for d in (AUTOMATIONS_DIR, TARGETS_DIR, RULES_DIR, LOGS_DIR):
    d.mkdir(exist_ok=True)

DARK_STYLESHEET = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QMainWindow {
    background-color: #1e1e2e;
}
QTabWidget::pane {
    border: 1px solid #45475a;
    background-color: #1e1e2e;
    border-radius: 4px;
}
QTabBar::tab {
    background-color: #313244;
    color: #bac2de;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    min-width: 100px;
}
QTabBar::tab:selected {
    background-color: #45475a;
    color: #cdd6f4;
    font-weight: bold;
}
QTabBar::tab:hover {
    background-color: #585b70;
}
QPushButton {
    background-color: #45475a;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 6px;
    padding: 6px 16px;
    min-height: 28px;
}
QPushButton:hover {
    background-color: #585b70;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #313244;
}
QPushButton:disabled {
    background-color: #313244;
    color: #6c7086;
    border-color: #45475a;
}
QPushButton#primary {
    background-color: #89b4fa;
    color: #1e1e2e;
    border-color: #89b4fa;
    font-weight: bold;
}
QPushButton#primary:hover {
    background-color: #b4d0fb;
}
QPushButton#danger {
    background-color: #f38ba8;
    color: #1e1e2e;
    border-color: #f38ba8;
}
QPushButton#danger:hover {
    background-color: #f5a3b8;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 4px;
    padding: 5px 8px;
    min-height: 26px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #89b4fa;
}
QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #585b70;
}
QListWidget {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 4px;
    padding: 4px;
    outline: none;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 3px;
}
QListWidget::item:selected {
    background-color: #45475a;
    color: #cdd6f4;
}
QListWidget::item:hover {
    background-color: #3b3d50;
}
QTableWidget {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 4px;
    gridline-color: #45475a;
}
QTableWidget::item {
    padding: 4px 8px;
}
QTableWidget::item:selected {
    background-color: #45475a;
}
QHeaderView::section {
    background-color: #45475a;
    color: #cdd6f4;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #585b70;
    border-bottom: 1px solid #585b70;
    font-weight: bold;
}
QPlainTextEdit {
    background-color: #181825;
    color: #a6e3a1;
    border: 1px solid #585b70;
    border-radius: 4px;
    padding: 6px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
QSlider::groove:horizontal {
    height: 6px;
    background-color: #45475a;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #89b4fa;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background-color: #89b4fa;
    border-radius: 3px;
}
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QLabel#heading {
    font-size: 15px;
    font-weight: bold;
    color: #89b4fa;
}
QLabel#subtext {
    color: #6c7086;
    font-size: 11px;
}
"""


class FlowDeskWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FlowDesk")
        self.resize(1100, 750)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.parser_tab = ParserTab(RULES_DIR)
        self.automations_tab = AutomationsTab(
            AUTOMATIONS_DIR, TARGETS_DIR, RULES_DIR
        )
        self.targets_tab = TargetsTab(TARGETS_DIR, parent_window=self)
        self.runner_tab = RunnerTab(AUTOMATIONS_DIR, RULES_DIR, TARGETS_DIR, LOGS_DIR)
        self.import_export_tab = ImportExportTab(AUTOMATIONS_DIR)

        self.tabs.addTab(self.parser_tab, "Parser")
        self.tabs.addTab(self.automations_tab, "Automations")
        self.tabs.addTab(self.targets_tab, "Targets")
        self.tabs.addTab(self.runner_tab, "Runner")
        self.tabs.addTab(self.import_export_tab, "Import/Export")

        self._setup_emergency_stop()
        self._setup_tray_icon()

    def _setup_emergency_stop(self):
        shortcut = QShortcut(QKeySequence("Ctrl+Shift+Q"), self)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(self._emergency_stop)

    def _emergency_stop(self):
        runner = self.runner_tab._runner
        if runner and runner.isRunning():
            runner.stop()
            self.statusBar().showMessage("Emergency stop triggered", 5000)
        else:
            self.statusBar().showMessage("No automation running", 3000)

    def _setup_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("FlowDesk")
        # QSystemTrayIcon warns if no icon is assigned.
        self.tray_icon.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        )

        tray_menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.showNormal)
        tray_menu.addAction(show_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        self.runner_tab.status_changed = self._update_tray_tooltip

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _update_tray_tooltip(self, status: str):
        if hasattr(self, "tray_icon"):
            self.tray_icon.setToolTip(f"FlowDesk — {status}")


def main():
    # Helps Qt pick the expected DPI behavior on Windows.
    # (Do this before QApplication() so Qt reads the environment at startup.)
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = FlowDeskWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
