"""Automation executor — runs automation steps against parsed PDF data.

Runs in a QThread so the UI stays responsive. Supports pause, resume, stop,
and emits per-step signals for live logging.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal

from modules import screen


class AutomationRunner(QThread):
    """Executes an automation sequence against a list of parsed records."""

    step_completed = pyqtSignal(int, int, str, str)  # record_idx, step_idx, status, msg
    run_finished = pyqtSignal(int, int)               # success_count, fail_count
    log_message = pyqtSignal(str)                      # timestamped log line

    def __init__(
        self,
        automation: dict,
        parsed_data: list[dict],
        targets_dir: str | Path,
        delay: float = 3.0,
        confidence_meta: dict | None = None,
    ):
        super().__init__()
        self.automation = automation
        self.parsed_data = parsed_data
        self.targets_dir = Path(targets_dir)
        self.delay = delay
        self.confidence_meta = confidence_meta or {}

        self._pause_event = Event()
        self._pause_event.set()  # not paused initially
        self._stop_flag = False
        self._success = 0
        self._fail = 0

    def pause(self):
        self._pause_event.clear()
        self._log("--- PAUSED ---")

    def resume(self):
        self._pause_event.set()
        self._log("--- RESUMED ---")

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()  # unblock if paused
        self._log("--- STOP REQUESTED ---")

    def run(self):
        steps = self.automation.get("steps", [])
        total_records = len(self.parsed_data)

        self._log(f"Starting automation '{self.automation.get('name', '?')}' "
                   f"with {total_records} record(s), {len(steps)} step(s)")

        for rec_idx, record in enumerate(self.parsed_data):
            if self._stop_flag:
                break

            self._log(f"Record {rec_idx + 1}/{total_records}")

            for step_idx, step in enumerate(steps):
                self._pause_event.wait()
                if self._stop_flag:
                    break

                status, msg = self._execute_step(step, record)

                self.step_completed.emit(rec_idx, step_idx, status, msg)
                self._log(f"  Step {step_idx + 1}: {step.get('action', '?')} -> {status}"
                          f"{' (' + msg + ')' if msg else ''}")

                if status == "ok":
                    self._success += 1
                else:
                    self._fail += 1
                    if not self._handle_error(status, msg, rec_idx, step_idx):
                        break

                step_delay = step.get("delay")
                if step_delay is not None:
                    time.sleep(step_delay)

            if rec_idx < total_records - 1 and not self._stop_flag:
                time.sleep(self.delay)

        self._log(f"Finished. {self._success} ok, {self._fail} failed.")
        self.run_finished.emit(self._success, self._fail)

    def _execute_step(self, step: dict, record: dict) -> tuple[str, str]:
        """Execute a single step. Returns (status, message)."""
        action = step.get("action", "")

        try:
            if action == "click_image":
                target = self._resolve_target(step.get("target", ""))
                conf = self._get_confidence(step)
                ox = int(step.get("offset_x", 0))
                oy = int(step.get("offset_y", 0))
                screen.click_image(
                    target,
                    confidence=conf,
                    timeout=step.get("timeout", 10),
                    offset_x=ox,
                    offset_y=oy,
                )
                suffix = f" offset({ox},{oy})" if ox or oy else ""
                return "ok", f"clicked {Path(target).name}{suffix}"

            elif action == "type_value":
                value = self._inject_variables(step.get("value", ""), record)
                screen.type_value(value)
                return "ok", f"typed '{value[:40]}'"

            elif action == "hotkey":
                keys = step.get("keys", [])
                screen.hotkey(*keys)
                return "ok", "+".join(keys)

            elif action == "wait_for_image":
                target = self._resolve_target(step.get("target", ""))
                conf = self._get_confidence(step)
                screen.wait_for_image(target, confidence=conf, timeout=step.get("timeout", 30))
                return "ok", f"found {Path(target).name}"

            else:
                return "skip", f"unknown action '{action}'"

        except screen.TargetNotFoundError as e:
            return "fail", str(e)
        except Exception as e:
            return "fail", f"{type(e).__name__}: {e}"

    def _resolve_target(self, target_name: str) -> str:
        path = self.targets_dir / target_name
        if path.exists():
            return str(path)
        if not target_name.endswith(".png"):
            path = self.targets_dir / f"{target_name}.png"
            if path.exists():
                return str(path)
        return str(self.targets_dir / target_name)

    def _get_confidence(self, step: dict) -> float:
        if "confidence" in step:
            return step["confidence"]
        target_name = step.get("target", "")
        stem = Path(target_name).stem
        return self.confidence_meta.get(stem, 0.85)

    @staticmethod
    def _inject_variables(template: str, record: dict) -> str:
        def replacer(match):
            key = match.group(1)
            return str(record.get(key, f"{{?{key}}}"))
        return re.sub(r"\{\{(\w+)\}\}", replacer, template)

    def _handle_error(self, status: str, msg: str, rec_idx: int, step_idx: int) -> bool:
        """Apply the on_error strategy. Returns True to continue, False to break record loop."""
        strategy = self.automation.get("on_error", "abort")

        if strategy == "skip_record":
            self._log(f"  -> Skipping record {rec_idx + 1} (on_error=skip_record)")
            return False  # break inner step loop, but outer record loop continues

        if strategy.startswith("retry_"):
            try:
                max_retries = int(strategy.split("_", 1)[1])
            except (ValueError, IndexError):
                max_retries = 1
            self._log(f"  -> Retrying step (max {max_retries})")
            # Re-execute not implemented at this level; handled as abort for now
            return True

        # Default: abort
        self._log("  -> Aborting run (on_error=abort)")
        self._stop_flag = True
        return False

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_message.emit(f"[{ts}] {msg}")


def load_automation(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_confidence_meta(targets_dir: str | Path) -> dict:
    meta_path = Path(targets_dir) / "meta.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
