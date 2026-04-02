"""Automations tab — step list builder with action-specific editors."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from ctypes import wintypes

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import pygetwindow as gw

from modules import screen
from ui.targets_tab import DetectionOverlay

ACTION_TYPES = [
    "move_to_image",
    "move_to_text",
    "simple_click",
    "type_value",
    "sleep",
]

ACTION_DESCRIPTIONS = {
    "move_to_image": "Wait for image & move mouse to it",
    "move_to_text": "OCR-find text on screen & move mouse",
    "simple_click": "Click at current cursor position",
    "type_value": "Type text into focused field",
    "sleep": "Pause for a fixed duration (seconds)",
}


def _collect_rule_names(rules_dir: Path) -> list[str]:
    """Union of all rule_name values from rules/*.json (Parser rule sets)."""
    names: set[str] = set()
    for path in sorted(rules_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict):
                rn = item.get("rule_name")
                if rn is not None and str(rn).strip():
                    names.add(str(rn).strip())
    return sorted(names)


class AutomationsTab(QWidget):
    def __init__(self, automations_dir: Path, targets_dir: Path, rules_dir: Path):
        super().__init__()
        self.automations_dir = automations_dir
        self.targets_dir = targets_dir
        self.rules_dir = rules_dir
        self._offset_pick_poll_timer: QTimer | None = None
        self._offset_pick_deadline_timer: QTimer | None = None
        self._offset_pick_center: tuple[int, int] | None = None
        self._offset_pick_step_row = -1
        self._offset_pick_target_path: Path | None = None
        self._offset_pick_confidence = 0.85
        self._offset_pick_overlay: DetectionOverlay | None = None
        self._offset_pick_click_timer: QTimer | None = None
        self._offset_pick_prev_left_down = False
        self._offset_pick_prev_right_down = False
        self._current_file: Path | None = None
        self._automation: dict = {"name": "", "steps": []}
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # --- Left: automation file list ---
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        heading = QLabel("Automations")
        heading.setObjectName("heading")
        ll.addWidget(heading)

        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        ll.addWidget(self.file_list)

        fb = QHBoxLayout()
        btn_new = QPushButton("New")
        btn_new.clicked.connect(self._new_automation)
        btn_rename = QPushButton("Rename")
        btn_rename.clicked.connect(self._rename_automation)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("danger")
        btn_del.clicked.connect(self._delete_automation)
        fb.addWidget(btn_new)
        fb.addWidget(btn_rename)
        fb.addWidget(btn_del)
        ll.addLayout(fb)

        ll.addWidget(QLabel("On Error:"))
        self.combo_on_error = QComboBox()
        self.combo_on_error.addItems(["abort", "skip_record", "retry_1", "retry_3"])
        self.combo_on_error.currentTextChanged.connect(self._on_error_strategy_changed)
        ll.addWidget(self.combo_on_error)

        splitter.addWidget(left)

        # --- Center: step list ---
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)

        step_heading = QLabel("Steps")
        step_heading.setObjectName("heading")
        cl.addWidget(step_heading)

        self.step_list = QListWidget()
        self.step_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        self.step_list.model().rowsMoved.connect(self._on_steps_reordered)
        cl.addWidget(self.step_list)

        sb = QHBoxLayout()
        btn_add_step = QPushButton("Add Step")
        btn_add_step.clicked.connect(self._add_step)
        btn_del_step = QPushButton("Remove")
        btn_del_step.setObjectName("danger")
        btn_del_step.clicked.connect(self._delete_step)
        btn_up = QPushButton("\u25b2")
        btn_up.setFixedWidth(36)
        btn_up.clicked.connect(self._move_step_up)
        btn_down = QPushButton("\u25bc")
        btn_down.setFixedWidth(36)
        btn_down.clicked.connect(self._move_step_down)
        sb.addWidget(btn_add_step)
        sb.addWidget(btn_del_step)
        sb.addWidget(btn_up)
        sb.addWidget(btn_down)
        cl.addLayout(sb)

        splitter.addWidget(center)

        # --- Right: step editor (stacked forms per action type) ---
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        editor_heading = QLabel("Step Editor")
        editor_heading.setObjectName("heading")
        rl.addWidget(editor_heading)

        self._step_editor_placeholder = QLabel(
            "Select a step in the list to edit its settings."
        )
        self._step_editor_placeholder.setWordWrap(True)
        self._step_editor_placeholder.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        rl.addWidget(self._step_editor_placeholder)

        self._step_editor_form = QWidget()
        form_layout = QVBoxLayout(self._step_editor_form)
        form_layout.setContentsMargins(0, 0, 0, 0)

        form_layout.addWidget(QLabel("Action Type"))
        self.combo_action = QComboBox()
        for a in ACTION_TYPES:
            self.combo_action.addItem(f"{a}  —  {ACTION_DESCRIPTIONS[a]}", a)
        self.combo_action.currentIndexChanged.connect(self._on_action_type_changed)
        form_layout.addWidget(self.combo_action)

        self.editor_stack = QStackedWidget()
        self._build_editors()
        form_layout.addWidget(self.editor_stack)

        btn_save_step = QPushButton("Save Step")
        btn_save_step.setObjectName("primary")
        btn_save_step.clicked.connect(self._save_step)
        form_layout.addWidget(btn_save_step)

        rl.addWidget(self._step_editor_form)

        rl.addStretch()
        self._set_step_editor_visible(False)
        splitter.addWidget(right)
        splitter.setSizes([200, 280, 340])

        self._refresh_file_list()

    # --- Editor forms per action type ---

    def _build_editors(self):
        self._editor_click = self._make_click_editor()
        self._editor_search_text = self._make_search_text_editor()
        self._editor_simple_click = self._make_simple_click_editor()
        self._editor_type = self._make_type_editor()
        self._editor_sleep = self._make_sleep_editor()

        self.editor_stack.addWidget(self._editor_click["widget"])        # 0: move_to_image
        self.editor_stack.addWidget(self._editor_search_text["widget"])  # 1: move_to_text
        self.editor_stack.addWidget(self._editor_simple_click["widget"]) # 2: simple_click
        self.editor_stack.addWidget(self._editor_type["widget"])         # 3: type_value
        self.editor_stack.addWidget(self._editor_sleep["widget"])        # 4: sleep

    def _make_click_editor(self) -> dict:
        """Editor for move_to_image: wait for target image then move mouse to it."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Target Image"))
        combo = QComboBox()
        layout.addWidget(combo)

        layout.addWidget(QLabel("Confidence"))
        spin_conf = QDoubleSpinBox()
        spin_conf.setRange(0.5, 1.0)
        spin_conf.setSingleStep(0.05)
        spin_conf.setValue(0.85)
        spin_conf.setDecimals(2)
        layout.addWidget(spin_conf)

        layout.addWidget(QLabel("Offset (pixels from image center)"))
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("X"))
        spin_ox = QSpinBox()
        spin_ox.setRange(-2000, 2000)
        spin_ox.setValue(0)
        spin_ox.setToolTip("Positive = right of image center")
        offset_row.addWidget(spin_ox)
        offset_row.addWidget(QLabel("Y"))
        spin_oy = QSpinBox()
        spin_oy.setRange(-2000, 2000)
        spin_oy.setValue(0)
        spin_oy.setToolTip("Positive = below image center")
        offset_row.addWidget(spin_oy)
        layout.addLayout(offset_row)

        pick_row = QHBoxLayout()
        btn_pick = QPushButton("Pick Offset Visually")
        btn_pick.clicked.connect(self._start_visual_offset_pick)
        pick_row.addWidget(btn_pick)
        pick_status = QLabel("Ready")
        pick_status.setWordWrap(True)
        pick_row.addWidget(pick_status, 1)
        layout.addLayout(pick_row)

        layout.addWidget(QLabel("Timeout (seconds, 0 = wait forever)"))
        spin_timeout = QSpinBox()
        spin_timeout.setRange(0, 9999)
        spin_timeout.setValue(0)
        spin_timeout.setToolTip("0 means wait indefinitely until the image appears")
        layout.addWidget(spin_timeout)

        layout.addWidget(QLabel("Move Duration (seconds, 0 = instant teleport)"))
        spin_move_duration = QDoubleSpinBox()
        spin_move_duration.setRange(0.0, 10.0)
        spin_move_duration.setSingleStep(0.05)
        spin_move_duration.setDecimals(2)
        spin_move_duration.setValue(0.0)
        layout.addWidget(spin_move_duration)

        layout.addStretch()
        return {
            "widget": w,
            "target": combo,
            "confidence": spin_conf,
            "offset_x": spin_ox,
            "offset_y": spin_oy,
            "pick_button": btn_pick,
            "pick_status": pick_status,
            "timeout": spin_timeout,
            "move_duration": spin_move_duration,
        }

    def _make_type_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Value (use {{variable}} for placeholders)"))
        edit = QLineEdit()
        edit.setPlaceholderText("{{customer_name}}")
        layout.addWidget(edit)

        layout.addWidget(QLabel("Insert variable"))
        var_combo = QComboBox()
        var_combo.setToolTip(
            "Choose a Parser rule name to insert {{rule_name}} at the text cursor."
        )

        def on_var_activated(index: int) -> None:
            if index <= 0:
                return
            name = var_combo.itemText(index)
            pos = edit.cursorPosition()
            token = f"{{{{{name}}}}}"
            edit.insert(token)
            edit.setCursorPosition(pos + len(token))
            var_combo.setCurrentIndex(0)

        var_combo.activated.connect(on_var_activated)
        layout.addWidget(var_combo)

        layout.addStretch()
        return {"widget": w, "value": edit, "variable_combo": var_combo}

    def _make_search_text_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Search Query (use {{variable}} for placeholders)"))
        edit = QLineEdit()
        edit.setPlaceholderText("{{customer_name}}")
        layout.addWidget(edit)

        layout.addWidget(QLabel("Insert variable"))
        var_combo = QComboBox()
        var_combo.setToolTip(
            "Choose a Parser rule name to insert {{rule_name}} at the text cursor."
        )

        def on_var_activated(index: int) -> None:
            if index <= 0:
                return
            name = var_combo.itemText(index)
            pos = edit.cursorPosition()
            token = f"{{{{{name}}}}}"
            edit.insert(token)
            edit.setCursorPosition(pos + len(token))
            var_combo.setCurrentIndex(0)

        var_combo.activated.connect(on_var_activated)
        layout.addWidget(var_combo)

        layout.addWidget(QLabel("Target window (optional)"))
        win_combo = QComboBox()
        win_combo.setEditable(True)
        win_combo.setToolTip(
            "Leave empty to search the full screen (all monitors). "
            "If set, OCR is limited to that window."
        )
        layout.addWidget(win_combo)

        btn_refresh_wins = QPushButton("Refresh Windows")
        btn_refresh_wins.clicked.connect(lambda: self._populate_window_combo(win_combo))
        layout.addWidget(btn_refresh_wins)

        layout.addWidget(QLabel("Match Mode"))
        match_combo = QComboBox()
        match_combo.addItems(["contains", "exact"])
        layout.addWidget(match_combo)

        case_cb = QCheckBox("Case sensitive")
        layout.addWidget(case_cb)

        layout.addWidget(QLabel("Timeout (seconds)"))
        spin_timeout = QSpinBox()
        spin_timeout.setRange(1, 120)
        spin_timeout.setValue(10)
        layout.addWidget(spin_timeout)

        layout.addWidget(QLabel("Move Duration (seconds, 0 = instant teleport)"))
        spin_move_duration = QDoubleSpinBox()
        spin_move_duration.setRange(0.0, 10.0)
        spin_move_duration.setSingleStep(0.05)
        spin_move_duration.setDecimals(2)
        spin_move_duration.setValue(0.0)
        layout.addWidget(spin_move_duration)

        layout.addStretch()
        return {
            "widget": w,
            "query": edit,
            "variable_combo": var_combo,
            "window_title": win_combo,
            "match": match_combo,
            "case_sensitive": case_cb,
            "timeout": spin_timeout,
            "move_duration": spin_move_duration,
        }

    def _make_simple_click_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Mouse Button"))
        btn_combo = QComboBox()
        btn_combo.addItems(["left", "right"])
        layout.addWidget(btn_combo)

        layout.addWidget(QLabel("Click Count"))
        spin_clicks = QSpinBox()
        spin_clicks.setRange(1, 3)
        spin_clicks.setValue(1)
        layout.addWidget(spin_clicks)

        layout.addStretch()
        return {
            "widget": w,
            "button": btn_combo,
            "clicks": spin_clicks,
        }

    def _make_sleep_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Duration (seconds)"))
        spin_sec = QDoubleSpinBox()
        spin_sec.setRange(0.0, 3600.0)
        spin_sec.setSingleStep(0.1)
        spin_sec.setDecimals(2)
        spin_sec.setValue(1.0)
        spin_sec.setToolTip("Pause before the next step.")
        layout.addWidget(spin_sec)

        layout.addStretch()
        return {"widget": w, "seconds": spin_sec}

    @staticmethod
    def _populate_window_combo(combo: QComboBox) -> None:
        prev = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("")
        for t in sorted({t for t in gw.getAllTitles() if t.strip()}):
            combo.addItem(t)
        combo.blockSignals(False)
        if not prev.strip():
            combo.setCurrentIndex(0)
        else:
            _set_combo(combo, prev)

    def _refresh_targets(self):
        combo = self._editor_click["target"]
        combo.clear()
        for f in sorted(self.targets_dir.glob("*.png")):
            combo.addItem(f.name)

    def _refresh_rule_variables(self):
        names = _collect_rule_names(self.rules_dir)
        for combo in (
            self._editor_type["variable_combo"],
            self._editor_search_text["variable_combo"],
        ):
            prev = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            for n in names:
                combo.addItem(n)
            combo.blockSignals(False)
            _set_combo(combo, prev)

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self._refresh_file_list()
        self._refresh_targets()
        self._refresh_rule_variables()
        self._populate_window_combo(self._editor_search_text["window_title"])
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if 0 <= row < len(steps):
            self._load_step_into_editor(steps[row])

    # --- Automation file management ---

    def _refresh_file_list(self):
        prev = self.file_list.currentItem()
        prev_name = prev.text() if prev else None
        self.file_list.clear()
        for f in sorted(self.automations_dir.glob("*.json")):
            self.file_list.addItem(f.stem)
        if prev_name:
            items = self.file_list.findItems(prev_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.file_list.setCurrentItem(items[0])

    def _on_file_selected(self, current: QListWidgetItem | None, _prev):
        if current is None:
            return
        path = self.automations_dir / f"{current.text()}.json"
        self._current_file = path
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._automation = json.load(f)
        else:
            self._automation = {"name": current.text(), "steps": []}
        self._refresh_step_list()
        self._refresh_targets()
        on_error = self._automation.get("on_error", "abort")
        idx = self.combo_on_error.findText(on_error)
        if idx >= 0:
            self.combo_on_error.setCurrentIndex(idx)

    def _new_automation(self):
        name, ok = QInputDialog.getText(self, "New Automation", "Name:")
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "_")
        path = self.automations_dir / f"{name}.json"
        data = {"name": name, "steps": []}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self._refresh_file_list()
        items = self.file_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.file_list.setCurrentItem(items[0])

    def _rename_automation(self):
        item = self.file_list.currentItem()
        if not item:
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip().replace(" ", "_")
        old_path = self.automations_dir / f"{old_name}.json"
        new_path = self.automations_dir / f"{new_name}.json"
        if old_path.exists():
            old_path.rename(new_path)
        self._automation["name"] = new_name
        self._persist()
        self._current_file = new_path
        self._refresh_file_list()

    def _delete_automation(self):
        item = self.file_list.currentItem()
        if not item:
            return
        path = self.automations_dir / f"{item.text()}.json"
        if path.exists():
            path.unlink()
        self._current_file = None
        self._automation = {"name": "", "steps": []}
        self._refresh_file_list()
        self._refresh_step_list()

    # --- Step list ---

    def _set_step_editor_visible(self, visible: bool):
        self._step_editor_form.setVisible(visible)
        self._step_editor_placeholder.setVisible(not visible)

    def _refresh_step_list(self):
        self.step_list.clear()
        for step in self._automation.get("steps", []):
            self.step_list.addItem(self._step_summary(step))
        self._on_step_selected(self.step_list.currentRow())

    @staticmethod
    def _step_summary(step: dict) -> str:
        action = step.get("action", "?")
        if action == "move_to_image":
            ox, oy = step.get("offset_x", 0), -step.get("offset_y", 0)
            offset = f" +({ox},{oy})" if ox or oy else ""
            return f"move \u2192 {step.get('target', '?')}{offset}"
        if action == "type_value":
            val = step.get("value", "")
            return f"type \u2192 {val[:30]}"
        if action == "move_to_text":
            q = step.get("query", "")
            win = step.get("window_title", "")
            win_tag = f" [{win}]" if win else ""
            return f"find text \u2192 {q[:30]}{win_tag}"
        if action == "simple_click":
            btn = step.get("button", "left")
            n = step.get("clicks", 1)
            return f"click \u2192 {btn}" + (f" x{n}" if n > 1 else "")
        if action == "sleep":
            sec = step.get("seconds", 0)
            return f"sleep \u2192 {sec}s"
        return action

    def _on_step_selected(self, row: int):
        steps = self._automation.get("steps", [])
        valid = 0 <= row < len(steps)
        self._set_step_editor_visible(valid)
        if not valid:
            return
        step = steps[row]
        action = step.get("action", "move_to_image")
        idx = ACTION_TYPES.index(action) if action in ACTION_TYPES else 0
        self.combo_action.setCurrentIndex(idx)
        self.editor_stack.setCurrentIndex(idx)
        self._load_step_into_editor(step)

    def _load_step_into_editor(self, step: dict):
        action = step.get("action", "move_to_image")
        if action == "move_to_image":
            _set_combo(self._editor_click["target"], step.get("target", ""))
            self._editor_click["confidence"].setValue(step.get("confidence", 0.85))
            self._editor_click["offset_x"].setValue(int(step.get("offset_x", 0)))
            self._editor_click["offset_y"].setValue(-int(step.get("offset_y", 0)))
            self._editor_click["timeout"].setValue(int(step.get("timeout", 0)))
            self._editor_click["move_duration"].setValue(float(step.get("move_duration", 0)))
            self._set_pick_status("Ready")
        elif action == "type_value":
            self._editor_type["value"].setText(step.get("value", ""))
        elif action == "move_to_text":
            self._editor_search_text["query"].setText(step.get("query", ""))
            _set_combo(
                self._editor_search_text["window_title"],
                step.get("window_title", ""),
            )
            _set_combo(
                self._editor_search_text["match"],
                step.get("match", "contains"),
            )
            self._editor_search_text["case_sensitive"].setChecked(
                step.get("case_sensitive", False)
            )
            self._editor_search_text["timeout"].setValue(step.get("timeout", 10))
            self._editor_search_text["move_duration"].setValue(
                float(step.get("move_duration", 0))
            )
        elif action == "simple_click":
            _set_combo(
                self._editor_simple_click["button"],
                step.get("button", "left"),
            )
            self._editor_simple_click["clicks"].setValue(step.get("clicks", 1))
        elif action == "sleep":
            self._editor_sleep["seconds"].setValue(float(step.get("seconds", 1)))

    def _on_action_type_changed(self, idx: int):
        self.editor_stack.setCurrentIndex(idx)

    def _add_step(self):
        new_step = {"action": "move_to_image", "target": "", "confidence": 0.85}
        self._automation.setdefault("steps", []).append(new_step)
        self._refresh_step_list()
        self.step_list.setCurrentRow(len(self._automation["steps"]) - 1)
        self._persist()

    def _delete_step(self):
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if 0 <= row < len(steps):
            steps.pop(row)
            self._refresh_step_list()
            self._persist()

    def _move_step_up(self):
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if row > 0:
            steps[row], steps[row - 1] = steps[row - 1], steps[row]
            self._refresh_step_list()
            self.step_list.setCurrentRow(row - 1)
            self._persist()

    def _move_step_down(self):
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if 0 <= row < len(steps) - 1:
            steps[row], steps[row + 1] = steps[row + 1], steps[row]
            self._refresh_step_list()
            self.step_list.setCurrentRow(row + 1)
            self._persist()

    def _on_steps_reordered(self):
        new_order = []
        for i in range(self.step_list.count()):
            text = self.step_list.item(i).text()
            new_order.append(text)
        # Reordering is already handled by the model; just persist
        self._persist()

    def _save_step(self):
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if row < 0 or row >= len(steps):
            return

        action = self.combo_action.currentData()
        step: dict = {"action": action}

        if action == "move_to_image":
            step["target"] = self._editor_click["target"].currentText()
            step["confidence"] = self._editor_click["confidence"].value()
            step["offset_x"] = self._editor_click["offset_x"].value()
            step["offset_y"] = -self._editor_click["offset_y"].value()
            step["timeout"] = self._editor_click["timeout"].value()
            step["move_duration"] = self._editor_click["move_duration"].value()
        elif action == "type_value":
            step["value"] = self._editor_type["value"].text()
        elif action == "move_to_text":
            step["query"] = self._editor_search_text["query"].text()
            wt = self._editor_search_text["window_title"].currentText().strip()
            if wt:
                step["window_title"] = wt
            step["match"] = self._editor_search_text["match"].currentText()
            step["case_sensitive"] = self._editor_search_text["case_sensitive"].isChecked()
            step["timeout"] = self._editor_search_text["timeout"].value()
            step["move_duration"] = self._editor_search_text["move_duration"].value()
        elif action == "simple_click":
            step["button"] = self._editor_simple_click["button"].currentText()
            step["clicks"] = self._editor_simple_click["clicks"].value()
        elif action == "sleep":
            step["seconds"] = self._editor_sleep["seconds"].value()

        steps[row] = step
        self._refresh_step_list()
        self.step_list.setCurrentRow(row)
        self._persist()

    def _on_error_strategy_changed(self, text: str):
        self._automation["on_error"] = text
        self._persist()

    @staticmethod
    def _is_mouse_down(vk_code: int) -> bool:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)

    @staticmethod
    def _cursor_pos() -> tuple[int, int] | None:
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (int(point.x), int(point.y))

    def _set_pick_status(self, text: str) -> None:
        self._editor_click["pick_status"].setText(text)

    def _start_visual_offset_pick(self) -> None:
        if self._offset_pick_poll_timer is not None:
            self._set_pick_status("Offset pick already in progress.")
            return
        row = self.step_list.currentRow()
        if row < 0:
            self._set_pick_status("Select a step first.")
            return
        if self.combo_action.currentData() != "move_to_image":
            self._set_pick_status("Visual picker is only available for move_to_image.")
            return
        target_name = self._editor_click["target"].currentText().strip()
        if not target_name:
            self._set_pick_status("Choose a target image first.")
            return
        target_path = self.targets_dir / target_name
        if not target_path.exists():
            self._set_pick_status(f"Target not found: {target_name}")
            return

        confidence = float(self._editor_click["confidence"].value())
        timeout = int(self._editor_click["timeout"].value())
        detect_timeout_ms = (timeout if timeout > 0 else 10) * 1000
        self._offset_pick_target_path = target_path
        self._offset_pick_confidence = confidence
        self._offset_pick_step_row = row
        self._set_pick_status("Searching for image on screen...")
        self._editor_click["pick_button"].setEnabled(False)
        self._offset_pick_overlay = DetectionOverlay()
        self._offset_pick_poll_timer = QTimer(self)
        self._offset_pick_poll_timer.setInterval(500)
        self._offset_pick_poll_timer.timeout.connect(self._poll_visual_offset_find)
        self._offset_pick_poll_timer.start()
        self._offset_pick_deadline_timer = QTimer(self)
        self._offset_pick_deadline_timer.setSingleShot(True)
        self._offset_pick_deadline_timer.setInterval(detect_timeout_ms)
        self._offset_pick_deadline_timer.timeout.connect(self._on_offset_pick_find_timeout)
        self._offset_pick_deadline_timer.start()
        self._poll_visual_offset_find()

    def _stop_visual_offset_pick(self) -> None:
        if self._offset_pick_poll_timer is not None:
            self._offset_pick_poll_timer.stop()
            self._offset_pick_poll_timer.deleteLater()
            self._offset_pick_poll_timer = None
        if self._offset_pick_deadline_timer is not None:
            self._offset_pick_deadline_timer.stop()
            self._offset_pick_deadline_timer.deleteLater()
            self._offset_pick_deadline_timer = None
        if self._offset_pick_click_timer is not None:
            self._offset_pick_click_timer.stop()
            self._offset_pick_click_timer.deleteLater()
            self._offset_pick_click_timer = None
        if self._offset_pick_overlay is not None:
            self._offset_pick_overlay.teardown()
            self._offset_pick_overlay = None
        self._offset_pick_target_path = None
        self._offset_pick_center = None
        self._editor_click["pick_button"].setEnabled(True)

    def _on_offset_pick_find_timeout(self) -> None:
        self._set_pick_status("Image not found in time. Try lower confidence.")
        self._stop_visual_offset_pick()

    def _poll_visual_offset_find(self) -> None:
        if self._offset_pick_target_path is None:
            return
        box = screen.find_image_box(self._offset_pick_target_path, self._offset_pick_confidence)
        if self._offset_pick_overlay is not None:
            self._offset_pick_overlay.update_box(box)
        if box is None:
            return
        left, top, width, height = box
        self._offset_pick_center = (left + width // 2, top + height // 2)
        if self._offset_pick_poll_timer is not None:
            self._offset_pick_poll_timer.stop()
        if self._offset_pick_deadline_timer is not None:
            self._offset_pick_deadline_timer.stop()
        self._set_pick_status("Waiting for click... left=save, right=cancel")
        self._offset_pick_prev_left_down = self._is_mouse_down(0x01)
        self._offset_pick_prev_right_down = self._is_mouse_down(0x02)
        self._offset_pick_click_timer = QTimer(self)
        self._offset_pick_click_timer.setInterval(20)
        self._offset_pick_click_timer.timeout.connect(self._offset_pick_click_tick)
        self._offset_pick_click_timer.start()

    def _offset_pick_click_tick(self) -> None:
        left_down = self._is_mouse_down(0x01)
        right_down = self._is_mouse_down(0x02)
        left_pressed = left_down and not self._offset_pick_prev_left_down
        right_pressed = right_down and not self._offset_pick_prev_right_down
        self._offset_pick_prev_left_down = left_down
        self._offset_pick_prev_right_down = right_down

        if right_pressed:
            self._set_pick_status("Offset pick cancelled.")
            self._stop_visual_offset_pick()
            return
        if not left_pressed:
            return

        pos = self._cursor_pos()
        center = self._offset_pick_center
        self._stop_visual_offset_pick()
        if pos is None or center is None:
            self._set_pick_status("Could not read cursor position.")
            return

        offset_x = int(pos[0] - center[0])
        offset_y = int(pos[1] - center[1])
        confirm = QMessageBox.question(
            self,
            "Confirm Click Offset",
            "Save click offset for this step?\n\n"
            f"Image center: ({center[0]}, {center[1]})\n"
            f"Clicked point: ({pos[0]}, {pos[1]})\n"
            f"Offset X: {offset_x}\n"
            f"Offset Y: {offset_y}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._set_pick_status("Offset pick cancelled.")
            return

        if self.step_list.currentRow() != self._offset_pick_step_row:
            self.step_list.setCurrentRow(self._offset_pick_step_row)
        self._editor_click["offset_x"].setValue(offset_x)
        self._editor_click["offset_y"].setValue(offset_y)
        self._save_step()
        self.step_list.setCurrentRow(self._offset_pick_step_row)
        self._set_pick_status(f"Saved offset ({offset_x}, {offset_y}).")

    def _persist(self):
        if self._current_file:
            with open(self._current_file, "w", encoding="utf-8") as f:
                json.dump(self._automation, f, indent=2)


def _set_combo(combo: QComboBox, text: str):
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    elif text:
        combo.addItem(text)
        combo.setCurrentIndex(combo.count() - 1)
