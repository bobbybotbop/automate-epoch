"""Import/Export tab — build EXE, export/import automation configs."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Callable

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

from modules.app_paths import application_base_dir
from ui.toast import ToastType, show_toast

BASE_DIR = application_base_dir()

META_FILENAME = "meta.json"


def _is_valid_automation(data) -> bool:
    return isinstance(data, dict) and "steps" in data


def _is_valid_ruleset(data) -> bool:
    if isinstance(data, list):
        return True
    return isinstance(data, dict) and isinstance(data.get("rules"), list)


class ImportExportTab(QWidget):
    def __init__(
        self,
        automations_dir: Path,
        rules_dir: Path,
        targets_dir: Path,
        *,
        on_import: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.automations_dir = automations_dir
        self.rules_dir = rules_dir
        self.targets_dir = targets_dir
        self._on_import = on_import
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
        export_group = QGroupBox("Export Config")
        export_layout = QVBoxLayout(export_group)

        export_desc = QLabel(
            "Save automations, parser rule sets, and screen targets "
            "as a single .zip archive."
        )
        export_desc.setWordWrap(True)
        export_desc.setObjectName("subtext")
        export_layout.addWidget(export_desc)

        btn_export = QPushButton("Export config")
        btn_export.setObjectName("primary")
        btn_export.clicked.connect(self._export_automations)
        export_layout.addWidget(btn_export)

        root.addWidget(export_group)

        # --- Import section ---
        import_group = QGroupBox("Import Config")
        import_layout = QVBoxLayout(import_group)

        import_desc = QLabel(
            "Load automations, rule sets, and targets from a .zip archive "
            "exported by FlowDesk. "
            "Conflicting names are auto-renamed to keep both versions."
        )
        import_desc.setWordWrap(True)
        import_desc.setObjectName("subtext")
        import_layout.addWidget(import_desc)

        btn_import = QPushButton("Import config")
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
        # Bundle Tesseract-OCR next to code in _MEIPASS (see modules/screen.py).
        if sys.platform == "win32":
            data_sep = ";"
        else:
            data_sep = ":"
        args = [
            "-m", "PyInstaller",
            "--noconfirm", "--clean",
            "--onefile", "--windowed",
            "--noupx",
            "--hidden-import", "pytesseract",
            "--add-data", f"Tesseract-OCR{data_sep}Tesseract-OCR",
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
    # Export
    # ------------------------------------------------------------------

    def _export_automations(self):
        auto_files = sorted(self.automations_dir.glob("*.json"))
        rule_files = sorted(self.rules_dir.glob("*.json"))
        target_files = sorted(self.targets_dir.glob("*.png"))
        meta_path = self.targets_dir / META_FILENAME

        if not auto_files and not rule_files and not target_files:
            show_toast("Nothing to export", ToastType.WARNING)
            return

        default_name = "flowdesk_config.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Config", default_name, "Zip Archives (*.zip)"
        )
        if not path:
            return

        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in auto_files:
                    zf.write(f, f"automations/{f.name}")
                for f in rule_files:
                    zf.write(f, f"rules/{f.name}")
                for f in target_files:
                    zf.write(f, f"targets/{f.name}")
                if meta_path.is_file():
                    zf.write(meta_path, f"targets/{META_FILENAME}")

            parts: list[str] = []
            if auto_files:
                parts.append(f"{len(auto_files)} automation(s)")
            if rule_files:
                parts.append(f"{len(rule_files)} rule set(s)")
            if target_files:
                parts.append(f"{len(target_files)} target(s)")
            show_toast(f"Exported {', '.join(parts)}", ToastType.SUCCESS)
        except Exception as exc:
            show_toast(f"Export failed: {exc}", ToastType.ERROR)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _import_automations(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Config", "", "Zip Archives (*.zip)"
        )
        if not path:
            return

        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                counts = self._do_import(zf, names)
        except zipfile.BadZipFile:
            show_toast("Selected file is not a valid zip archive", ToastType.ERROR)
            return
        except Exception as exc:
            show_toast(f"Import failed: {exc}", ToastType.ERROR)
            return

        n_auto, n_rules, n_targets, n_renamed = counts
        total = n_auto + n_rules + n_targets

        if total == 0:
            show_toast("No valid items found in zip", ToastType.WARNING)
            return

        parts: list[str] = []
        if n_auto:
            parts.append(f"{n_auto} automation(s)")
        if n_rules:
            parts.append(f"{n_rules} rule set(s)")
        if n_targets:
            parts.append(f"{n_targets} target(s)")
        summary = f"Imported {', '.join(parts)}"
        if n_renamed:
            summary += f" ({n_renamed} renamed to avoid conflicts)"
        show_toast(summary, ToastType.SUCCESS)

        if self._on_import:
            self._on_import()

    def _do_import(
        self, zf: zipfile.ZipFile, names: list[str]
    ) -> tuple[int, int, int, int]:
        n_auto = 0
        n_rules = 0
        n_targets = 0
        n_renamed = 0
        imported_meta: dict | None = None

        for name in names:
            parts = Path(name).parts

            if len(parts) == 2 and parts[0] == "automations" and name.lower().endswith(".json"):
                raw = zf.read(name)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not _is_valid_automation(data):
                    continue
                dest, was_renamed = self._safe_dest(self.automations_dir, parts[1])
                n_renamed += was_renamed
                dest.write_bytes(raw)
                n_auto += 1

            elif len(parts) == 2 and parts[0] == "rules" and name.lower().endswith(".json"):
                raw = zf.read(name)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not _is_valid_ruleset(data):
                    continue
                dest, was_renamed = self._safe_dest(self.rules_dir, parts[1])
                n_renamed += was_renamed
                dest.write_bytes(raw)
                n_rules += 1

            elif len(parts) == 2 and parts[0] == "targets" and parts[1].lower() == META_FILENAME.lower():
                raw = zf.read(name)
                try:
                    imported_meta = json.loads(raw)
                except json.JSONDecodeError:
                    pass

            elif len(parts) == 2 and parts[0] == "targets" and name.lower().endswith(".png"):
                raw = zf.read(name)
                dest, was_renamed = self._safe_dest(self.targets_dir, parts[1])
                n_renamed += was_renamed
                dest.write_bytes(raw)
                n_targets += 1

            elif len(parts) == 1 and name.lower().endswith(".json"):
                # Legacy: root-level automation JSON from older exports
                raw = zf.read(name)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not _is_valid_automation(data):
                    continue
                dest, was_renamed = self._safe_dest(self.automations_dir, parts[0])
                n_renamed += was_renamed
                dest.write_bytes(raw)
                n_auto += 1

        if imported_meta and isinstance(imported_meta, dict):
            self._merge_target_meta(imported_meta)

        return n_auto, n_rules, n_targets, n_renamed

    def _merge_target_meta(self, incoming: dict) -> None:
        meta_path = self.targets_dir / META_FILENAME
        existing: dict = {}
        if meta_path.is_file():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        existing.update(incoming)
        meta_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def _safe_dest(self, base_dir: Path, filename: str) -> tuple[Path, int]:
        """Return (dest_path, 1_if_renamed) after zip-slip validation."""
        dest = (base_dir / filename).resolve()
        if not str(dest).startswith(str(base_dir.resolve())):
            raise ValueError(f"Zip entry escapes target directory: {filename}")
        renamed = 0
        if dest.exists():
            dest = self._unique_path(dest)
            renamed = 1
        return dest, renamed

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """Return a path like name_imported_1.ext that doesn't exist yet."""
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_imported_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
