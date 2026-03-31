"""Runner tab — select automation + PDF, control execution, view live log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from modules.parser import load_rules, parse_pdf
from modules.runner import AutomationRunner, load_automation, load_confidence_meta


class RunnerTab(QWidget):
    def __init__(
        self,
        automations_dir: Path,
        rules_dir: Path,
        targets_dir: Path,
        logs_dir: Path,
    ):
        super().__init__()
        self.automations_dir = automations_dir
        self.rules_dir = rules_dir
        self.targets_dir = targets_dir
        self.logs_dir = logs_dir

        self._pdf_path: str | None = None
        self._runner: AutomationRunner | None = None
        self.status_changed: callable = None  # set by main window for tray updates

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        heading = QLabel("Run Automation")
        heading.setObjectName("heading")
        root.addWidget(heading)

        # --- Selection row ---
        sel = QHBoxLayout()

        sel.addWidget(QLabel("Automation:"))
        self.combo_auto = QComboBox()
        self.combo_auto.setMinimumWidth(180)
        sel.addWidget(self.combo_auto)

        sel.addWidget(QLabel("Rule Set:"))
        self.combo_rules = QComboBox()
        self.combo_rules.setMinimumWidth(180)
        sel.addWidget(self.combo_rules)

        btn_pdf = QPushButton("Load PDF")
        btn_pdf.clicked.connect(self._load_pdf)
        sel.addWidget(btn_pdf)

        self.pdf_label = QLabel("No PDF")
        self.pdf_label.setObjectName("subtext")
        sel.addWidget(self.pdf_label)

        sel.addStretch()
        root.addLayout(sel)

        # --- Delay control ---
        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Loop Delay:"))
        self.delay_slider = QSlider(Qt.Orientation.Horizontal)
        self.delay_slider.setRange(5, 100)  # 0.5s to 10.0s in tenths
        self.delay_slider.setValue(30)
        self.delay_slider.setTickInterval(5)
        self.delay_slider.valueChanged.connect(self._on_delay_changed)
        delay_row.addWidget(self.delay_slider)
        self.delay_label = QLabel("3.0s")
        self.delay_label.setFixedWidth(48)
        delay_row.addWidget(self.delay_label)
        root.addLayout(delay_row)

        # --- Control buttons ---
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._start_run)
        btn_row.addWidget(self.btn_run)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_run)
        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # --- Live log ---
        log_heading = QLabel("Log")
        log_heading.setObjectName("heading")
        root.addWidget(log_heading)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        root.addWidget(self.log_view)

        self._refresh_combos()

    def _refresh_combos(self):
        self.combo_auto.clear()
        for f in sorted(self.automations_dir.glob("*.json")):
            self.combo_auto.addItem(f.stem, str(f))

        self.combo_rules.clear()
        for f in sorted(self.rules_dir.glob("*.json")):
            self.combo_rules.addItem(f.stem, str(f))

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_combos()

    def _on_delay_changed(self, val: int):
        self.delay_label.setText(f"{val / 10:.1f}s")

    def _load_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if path:
            self._pdf_path = path
            self.pdf_label.setText(Path(path).name)

    # --- Run control ---

    def _start_run(self):
        auto_path = self.combo_auto.currentData()
        rules_path = self.combo_rules.currentData()

        if not auto_path:
            QMessageBox.warning(self, "Missing", "Select an automation.")
            return
        if not rules_path:
            QMessageBox.warning(self, "Missing", "Select a rule set.")
            return
        if not self._pdf_path:
            QMessageBox.warning(self, "Missing", "Load a PDF file.")
            return

        try:
            automation = load_automation(auto_path)
            rules = load_rules(rules_path)
            parsed = parse_pdf(self._pdf_path, rules)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        if not parsed:
            QMessageBox.warning(self, "No Data", "PDF parsing returned no records.")
            return

        self.log_view.clear()
        self._append_log(f"Loaded {len(parsed)} record(s) from PDF.")

        meta = load_confidence_meta(self.targets_dir)
        delay = self.delay_slider.value() / 10.0

        self._runner = AutomationRunner(automation, parsed, self.targets_dir, delay, meta)
        self._runner.log_message.connect(self._append_log)
        self._runner.run_finished.connect(self._on_run_finished)
        self._runner.finished.connect(self._on_thread_done)

        self.btn_run.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self._notify_status("Running")

        self._runner.start()

    def _toggle_pause(self):
        if not self._runner:
            return
        if self._runner._pause_event.is_set():
            self._runner.pause()
            self.btn_pause.setText("Resume")
        else:
            self._runner.resume()
            self.btn_pause.setText("Pause")

    def _stop_run(self):
        if self._runner:
            self._runner.stop()

    def _on_run_finished(self, ok: int, fail: int):
        self._append_log(f"=== DONE: {ok} ok, {fail} failed ===")
        self._save_log()

    def _on_thread_done(self):
        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("Pause")
        self.btn_stop.setEnabled(False)
        self._runner = None
        self._notify_status("Idle")

    # --- Logging ---

    def _append_log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def _save_log(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = self.logs_dir / f"run_log_{ts}.txt"
        try:
            log_path.write_text(self.log_view.toPlainText(), encoding="utf-8")
        except OSError:
            pass

    def _notify_status(self, status: str):
        if callable(self.status_changed):
            self.status_changed(status)
