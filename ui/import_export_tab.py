"""Import/Export tab — build EXE, export/import automation configs."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.toast import ToastType, show_toast

BASE_DIR = Path(__file__).resolve().parent.parent


class ImportExportTab(QWidget):
    def __init__(self, automations_dir: Path):
        super().__init__()
        self.automations_dir = automations_dir
        self._build_process: QProcess | None = None
        self._pip_process: QProcess | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        heading = QLabel("Import / Export")
        heading.setObjectName("heading")
        root.addWidget(heading)

        # --- Build EXE section ---
        exe_group = QGroupBox("Build Standalone EXE")
        exe_layout = QVBoxLayout(exe_group)

        exe_desc = QLabel(
            "Package FlowDesk into a single .exe file using PyInstaller. "
            "PyInstaller must be installed (pip install pyinstaller)."
        )
        exe_desc.setWordWrap(True)
        exe_desc.setObjectName("subtext")
        exe_layout.addWidget(exe_desc)

        exe_btn_row = QHBoxLayout()
        self.btn_build = QPushButton("Build single-file EXE")
        self.btn_build.setObjectName("primary")
        self.btn_build.clicked.connect(self._start_build)
        exe_btn_row.addWidget(self.btn_build)

        self.btn_install_pyinstaller = QPushButton("Install PyInstaller")
        self.btn_install_pyinstaller.clicked.connect(self._install_pyinstaller)
        exe_btn_row.addWidget(self.btn_install_pyinstaller)

        self.btn_open_dist = QPushButton("Open output folder")
        self.btn_open_dist.setEnabled(False)
        self.btn_open_dist.clicked.connect(self._open_dist_folder)
        exe_btn_row.addWidget(self.btn_open_dist)

        exe_btn_row.addStretch()
        exe_layout.addLayout(exe_btn_row)

        self.build_log = QPlainTextEdit()
        self.build_log.setReadOnly(True)
        self.build_log.setMaximumHeight(200)
        exe_layout.addWidget(self.build_log)

        root.addWidget(exe_group)

        # --- Export section ---
        export_group = QGroupBox("Export Automations")
        export_layout = QVBoxLayout(export_group)

        export_desc = QLabel(
            "Save all automation configs (automations/*.json) as a .zip archive."
        )
        export_desc.setWordWrap(True)
        export_desc.setObjectName("subtext")
        export_layout.addWidget(export_desc)

        btn_export = QPushButton("Export automations config")
        btn_export.setObjectName("primary")
        btn_export.clicked.connect(self._export_automations)
        export_layout.addWidget(btn_export)

        root.addWidget(export_group)

        # --- Import section ---
        import_group = QGroupBox("Import Automations")
        import_layout = QVBoxLayout(import_group)

        import_desc = QLabel(
            "Load automation configs from a .zip archive exported by FlowDesk. "
            "Conflicting names are auto-renamed to keep both versions."
        )
        import_desc.setWordWrap(True)
        import_desc.setObjectName("subtext")
        import_layout.addWidget(import_desc)

        btn_import = QPushButton("Import automations config")
        btn_import.setObjectName("primary")
        btn_import.clicked.connect(self._import_automations)
        import_layout.addWidget(btn_import)

        root.addWidget(import_group)

        root.addStretch()

    # ------------------------------------------------------------------
    # Build EXE
    # ------------------------------------------------------------------

    def _start_build(self):
        if not self._has_pyinstaller():
            reply = QMessageBox.question(
                self,
                "PyInstaller missing",
                "PyInstaller is not installed in this Python environment.\n\nInstall it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._install_pyinstaller(auto_build_after=True)
            else:
                show_toast("Install PyInstaller to build an EXE", ToastType.WARNING)
            return

        if (
            self._build_process is not None
            and self._build_process.state() != QProcess.ProcessState.NotRunning
        ):
            QMessageBox.information(self, "Build", "A build is already running.")
            return

        python = sys.executable
        args = [
            "-m", "PyInstaller",
            "--noconfirm", "--clean",
            "--onefile", "--windowed",
            "--name", "FlowDesk",
            "main.py",
        ]

        self.build_log.clear()
        self.build_log.appendPlainText(f"> {python} {' '.join(args)}\n")
        self.btn_build.setEnabled(False)
        self.btn_open_dist.setEnabled(False)

        proc = QProcess(self)
        proc.setWorkingDirectory(str(BASE_DIR))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_build_output(proc))
        proc.finished.connect(lambda code, status: self._on_build_finished(code))
        proc.errorOccurred.connect(self._on_build_error)

        proc.start(python, args)
        self._build_process = proc

    def _has_pyinstaller(self) -> bool:
        return importlib.util.find_spec("PyInstaller") is not None

    def _install_pyinstaller(self, auto_build_after: bool = False) -> None:
        if (
            self._pip_process is not None
            and self._pip_process.state() != QProcess.ProcessState.NotRunning
        ):
            QMessageBox.information(self, "Install", "An install is already running.")
            return

        python = sys.executable
        args = ["-m", "pip", "install", "pyinstaller"]

        self.build_log.appendPlainText(f"\n> {python} {' '.join(args)}\n")
        self.btn_build.setEnabled(False)
        self.btn_install_pyinstaller.setEnabled(False)
        self.btn_open_dist.setEnabled(False)

        proc = QProcess(self)
        proc.setWorkingDirectory(str(BASE_DIR))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_build_output(proc))

        def on_finished(exit_code: int, _status) -> None:
            self.btn_install_pyinstaller.setEnabled(True)
            self.btn_build.setEnabled(True)
            if exit_code == 0 and self._has_pyinstaller():
                show_toast("PyInstaller installed", ToastType.SUCCESS)
                if auto_build_after:
                    self._start_build()
            else:
                show_toast("PyInstaller install failed — check the log", ToastType.ERROR)

        proc.finished.connect(on_finished)
        proc.errorOccurred.connect(self._on_build_error)
        proc.start(python, args)
        self._pip_process = proc

    def _on_build_output(self, proc: QProcess):
        data = proc.readAllStandardOutput()
        if data:
            text = bytes(data).decode("utf-8", errors="replace")
            self.build_log.appendPlainText(text.rstrip("\n"))

    def _on_build_finished(self, exit_code: int):
        self.btn_build.setEnabled(True)
        if exit_code == 0:
            self.build_log.appendPlainText("\nBuild succeeded.")
            self.btn_open_dist.setEnabled(True)
            show_toast("EXE build completed successfully", ToastType.SUCCESS)
        else:
            self.build_log.appendPlainText(f"\nBuild failed (exit code {exit_code}).")
            show_toast("EXE build failed — check the log", ToastType.ERROR)

    def _on_build_error(self, error: QProcess.ProcessError):
        self.btn_build.setEnabled(True)
        if error == QProcess.ProcessError.FailedToStart:
            self.build_log.appendPlainText(
                "Failed to start PyInstaller. "
                "Make sure it is installed: pip install pyinstaller"
            )
            show_toast("PyInstaller not found — pip install pyinstaller", ToastType.ERROR)

    def _open_dist_folder(self):
        dist = BASE_DIR / "dist"
        dist.mkdir(exist_ok=True)
        os.startfile(str(dist))

    # ------------------------------------------------------------------
    # Export automations
    # ------------------------------------------------------------------

    def _export_automations(self):
        json_files = sorted(self.automations_dir.glob("*.json"))
        if not json_files:
            show_toast("No automations to export", ToastType.WARNING)
            return

        default_name = "flowdesk_automations.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Automations", default_name, "Zip Archives (*.zip)"
        )
        if not path:
            return

        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in json_files:
                    zf.write(f, f.name)
            show_toast(
                f"Exported {len(json_files)} automation(s)", ToastType.SUCCESS
            )
        except Exception as exc:
            show_toast(f"Export failed: {exc}", ToastType.ERROR)

    # ------------------------------------------------------------------
    # Import automations
    # ------------------------------------------------------------------

    def _import_automations(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Automations", "", "Zip Archives (*.zip)"
        )
        if not path:
            return

        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()

            valid: list[str] = []
            for name in names:
                basename = Path(name).name
                if basename != name:
                    continue
                if not name.lower().endswith(".json"):
                    continue
                valid.append(name)

            if not valid:
                show_toast("Zip contains no valid automation JSON files", ToastType.WARNING)
                return

            imported = 0
            renamed = 0

            with zipfile.ZipFile(path, "r") as zf:
                for name in valid:
                    raw = zf.read(name)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict) or "steps" not in data:
                        continue

                    dest = self.automations_dir / name
                    if dest.exists():
                        dest = self._unique_path(dest)
                        renamed += 1

                    dest.write_bytes(raw)
                    imported += 1

            if imported:
                summary = f"Imported {imported} automation(s)"
                if renamed:
                    summary += f" ({renamed} renamed to avoid conflicts)"
                show_toast(summary, ToastType.SUCCESS)
            else:
                show_toast("No valid automations found in zip", ToastType.WARNING)

        except zipfile.BadZipFile:
            show_toast("Selected file is not a valid zip archive", ToastType.ERROR)
        except Exception as exc:
            show_toast(f"Import failed: {exc}", ToastType.ERROR)

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """Return a path like name_imported_1.json that doesn't exist yet."""
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_imported_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
