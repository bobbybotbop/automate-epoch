"""Automations tab — step list builder with action-specific editors."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

ACTION_TYPES = [
    "click_image",
    "type_value",
    "hotkey",
    "wait_for_image",
    "loop_over_list",
]

ACTION_DESCRIPTIONS = {
    "click_image": "Find & click a screen target",
    "type_value": "Type text into focused field",
    "hotkey": "Send keyboard shortcut",
    "wait_for_image": "Wait for screen target to appear",
    "loop_over_list": "Loop over parsed data field",
}


class AutomationsTab(QWidget):
    def __init__(self, automations_dir: Path, targets_dir: Path):
        super().__init__()
        self.automations_dir = automations_dir
        self.targets_dir = targets_dir
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

        rl.addWidget(QLabel("Action Type"))
        self.combo_action = QComboBox()
        for a in ACTION_TYPES:
            self.combo_action.addItem(f"{a}  —  {ACTION_DESCRIPTIONS[a]}", a)
        self.combo_action.currentIndexChanged.connect(self._on_action_type_changed)
        rl.addWidget(self.combo_action)

        self.editor_stack = QStackedWidget()
        self._build_editors()
        rl.addWidget(self.editor_stack)

        btn_save_step = QPushButton("Save Step")
        btn_save_step.setObjectName("primary")
        btn_save_step.clicked.connect(self._save_step)
        rl.addWidget(btn_save_step)

        rl.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([200, 280, 340])

        self._refresh_file_list()

    # --- Editor forms per action type ---

    def _build_editors(self):
        self._editor_click = self._make_target_editor()
        self._editor_type = self._make_type_editor()
        self._editor_hotkey = self._make_hotkey_editor()
        self._editor_wait = self._make_target_editor()
        self._editor_loop = self._make_loop_editor()

        self.editor_stack.addWidget(self._editor_click["widget"])   # 0: click_image
        self.editor_stack.addWidget(self._editor_type["widget"])    # 1: type_value
        self.editor_stack.addWidget(self._editor_hotkey["widget"])  # 2: hotkey
        self.editor_stack.addWidget(self._editor_wait["widget"])    # 3: wait_for_image
        self.editor_stack.addWidget(self._editor_loop["widget"])    # 4: loop_over_list

    def _make_target_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Target Image"))
        combo = QComboBox()
        layout.addWidget(combo)

        layout.addWidget(QLabel("Confidence"))
        spin = QDoubleSpinBox()
        spin.setRange(0.5, 1.0)
        spin.setSingleStep(0.05)
        spin.setValue(0.85)
        spin.setDecimals(2)
        layout.addWidget(spin)

        layout.addStretch()
        return {"widget": w, "target": combo, "confidence": spin}

    def _make_type_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Value (use {{variable}} for placeholders)"))
        edit = QLineEdit()
        edit.setPlaceholderText("{{customer_name}}")
        layout.addWidget(edit)

        layout.addStretch()
        return {"widget": w, "value": edit}

    def _make_hotkey_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Keys (comma-separated, e.g. ctrl,p)"))
        edit = QLineEdit()
        edit.setPlaceholderText("ctrl,p")
        layout.addWidget(edit)

        layout.addStretch()
        return {"widget": w, "keys": edit}

    def _make_loop_editor(self) -> dict:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Field to loop over"))
        edit = QLineEdit()
        edit.setPlaceholderText("customer_name")
        layout.addWidget(edit)

        layout.addStretch()
        return {"widget": w, "field": edit}

    def _refresh_targets(self):
        for editor in (self._editor_click, self._editor_wait):
            combo = editor["target"]
            combo.clear()
            for f in sorted(self.targets_dir.glob("*.png")):
                combo.addItem(f.name)

    # --- Automation file management ---

    def _refresh_file_list(self):
        self.file_list.clear()
        for f in sorted(self.automations_dir.glob("*.json")):
            self.file_list.addItem(f.stem)

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

    def _refresh_step_list(self):
        self.step_list.clear()
        for step in self._automation.get("steps", []):
            self.step_list.addItem(self._step_summary(step))

    @staticmethod
    def _step_summary(step: dict) -> str:
        action = step.get("action", "?")
        if action in ("click_image", "wait_for_image"):
            return f"{action} \u2192 {step.get('target', '?')}"
        if action == "type_value":
            val = step.get("value", "")
            return f"type_value \u2192 {val[:30]}"
        if action == "hotkey":
            return f"hotkey \u2192 {'+'.join(step.get('keys', []))}"
        if action == "loop_over_list":
            return f"loop \u2192 {step.get('field', '?')}"
        return action

    def _on_step_selected(self, row: int):
        steps = self._automation.get("steps", [])
        if row < 0 or row >= len(steps):
            return
        step = steps[row]
        action = step.get("action", "click_image")
        idx = ACTION_TYPES.index(action) if action in ACTION_TYPES else 0
        self.combo_action.setCurrentIndex(idx)
        self.editor_stack.setCurrentIndex(idx)
        self._load_step_into_editor(step)

    def _load_step_into_editor(self, step: dict):
        action = step.get("action", "click_image")
        if action == "click_image":
            _set_combo(self._editor_click["target"], step.get("target", ""))
            self._editor_click["confidence"].setValue(step.get("confidence", 0.85))
        elif action == "wait_for_image":
            _set_combo(self._editor_wait["target"], step.get("target", ""))
            self._editor_wait["confidence"].setValue(step.get("confidence", 0.85))
        elif action == "type_value":
            self._editor_type["value"].setText(step.get("value", ""))
        elif action == "hotkey":
            self._editor_hotkey["keys"].setText(",".join(step.get("keys", [])))
        elif action == "loop_over_list":
            self._editor_loop["field"].setText(step.get("field", ""))

    def _on_action_type_changed(self, idx: int):
        self.editor_stack.setCurrentIndex(idx)

    def _add_step(self):
        new_step = {"action": "click_image", "target": "", "confidence": 0.85}
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

        if action == "click_image":
            step["target"] = self._editor_click["target"].currentText()
            step["confidence"] = self._editor_click["confidence"].value()
        elif action == "wait_for_image":
            step["target"] = self._editor_wait["target"].currentText()
            step["confidence"] = self._editor_wait["confidence"].value()
        elif action == "type_value":
            step["value"] = self._editor_type["value"].text()
        elif action == "hotkey":
            keys_text = self._editor_hotkey["keys"].text()
            step["keys"] = [k.strip() for k in keys_text.split(",") if k.strip()]
        elif action == "loop_over_list":
            step["field"] = self._editor_loop["field"].text()

        steps[row] = step
        self._refresh_step_list()
        self.step_list.setCurrentRow(row)
        self._persist()

    def _on_error_strategy_changed(self, text: str):
        self._automation["on_error"] = text
        self._persist()

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
