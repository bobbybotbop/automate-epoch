"""Automations tab — step list builder with action-specific editors."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
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

ACTION_TYPES = [
    "click_image",
    "type_value",
    "hotkey",
    "wait_for_image",
    "search_by_text",
    "simple_click",
]

ACTION_DESCRIPTIONS = {
    "click_image": "Find & click a screen target",
    "type_value": "Type text into focused field",
    "hotkey": "Send keyboard shortcut",
    "wait_for_image": "Wait for screen target to appear",
    "search_by_text": "OCR-find text on screen & move mouse",
    "simple_click": "Click at current cursor position",
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
        self._editor_type = self._make_type_editor()
        self._editor_hotkey = self._make_hotkey_editor()
        self._editor_wait = self._make_wait_editor()
        self._editor_search_text = self._make_search_text_editor()
        self._editor_simple_click = self._make_simple_click_editor()

        self.editor_stack.addWidget(self._editor_click["widget"])        # 0: click_image
        self.editor_stack.addWidget(self._editor_type["widget"])         # 1: type_value
        self.editor_stack.addWidget(self._editor_hotkey["widget"])       # 2: hotkey
        self.editor_stack.addWidget(self._editor_wait["widget"])         # 3: wait_for_image
        self.editor_stack.addWidget(self._editor_search_text["widget"])  # 4: search_by_text
        self.editor_stack.addWidget(self._editor_simple_click["widget"]) # 5: simple_click

    def _make_click_editor(self) -> dict:
        """Editor for click_image with target, confidence, offset, and loop variable."""
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

        layout.addWidget(QLabel("Click Offset (pixels from image center)"))
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

        layout.addWidget(QLabel("Loop Variable (optional — one click per value)"))
        loop_combo = QComboBox()
        loop_combo.setToolTip(
            "Parser rule names from rules/*.json. If the field has several values "
            "(comma/newline-separated, or a list), this step clicks once per value. "
            "Leave empty for a single click. Unknown names can be added when loading old automations."
        )
        layout.addWidget(loop_combo)

        layout.addStretch()
        return {
            "widget": w,
            "target": combo,
            "confidence": spin_conf,
            "offset_x": spin_ox,
            "offset_y": spin_oy,
            "loop_variable": loop_combo,
        }

    def _make_wait_editor(self) -> dict:
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

        layout.addStretch()
        return {
            "widget": w,
            "query": edit,
            "variable_combo": var_combo,
            "window_title": win_combo,
            "match": match_combo,
            "case_sensitive": case_cb,
            "timeout": spin_timeout,
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
        for ed in (self._editor_click, self._editor_wait):
            combo = ed["target"]
            combo.clear()
            for f in sorted(self.targets_dir.glob("*.png")):
                combo.addItem(f.name)

    def _refresh_rule_variables(self):
        names = _collect_rule_names(self.rules_dir)
        for combo in (
            self._editor_click["loop_variable"],
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
        self._refresh_targets()
        self._refresh_rule_variables()
        self._populate_window_combo(self._editor_search_text["window_title"])
        row = self.step_list.currentRow()
        steps = self._automation.get("steps", [])
        if 0 <= row < len(steps):
            self._load_step_into_editor(steps[row])

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
        if action == "click_image":
            ox, oy = step.get("offset_x", 0), -step.get("offset_y", 0)
            offset = f" +({ox},{oy})" if ox or oy else ""
            loop = step.get("loop_variable", "")
            loop_tag = f" [loop:{loop}]" if loop else ""
            return f"click \u2192 {step.get('target', '?')}{offset}{loop_tag}"
        if action == "wait_for_image":
            return f"wait \u2192 {step.get('target', '?')}"
        if action == "type_value":
            val = step.get("value", "")
            return f"type \u2192 {val[:30]}"
        if action == "hotkey":
            return f"hotkey \u2192 {'+'.join(step.get('keys', []))}"
        if action == "search_by_text":
            q = step.get("query", "")
            win = step.get("window_title", "")
            win_tag = f" [{win}]" if win else ""
            return f"find text \u2192 {q[:30]}{win_tag}"
        if action == "simple_click":
            btn = step.get("button", "left")
            n = step.get("clicks", 1)
            return f"click \u2192 {btn}" + (f" x{n}" if n > 1 else "")
        return action

    def _on_step_selected(self, row: int):
        steps = self._automation.get("steps", [])
        valid = 0 <= row < len(steps)
        self._set_step_editor_visible(valid)
        if not valid:
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
            self._editor_click["offset_x"].setValue(int(step.get("offset_x", 0)))
            # Flip Y sign so editor direction matches expected click direction.
            self._editor_click["offset_y"].setValue(-int(step.get("offset_y", 0)))
            _set_combo(
                self._editor_click["loop_variable"],
                step.get("loop_variable", ""),
            )
        elif action == "wait_for_image":
            _set_combo(self._editor_wait["target"], step.get("target", ""))
            self._editor_wait["confidence"].setValue(step.get("confidence", 0.85))
        elif action == "type_value":
            self._editor_type["value"].setText(step.get("value", ""))
        elif action == "hotkey":
            self._editor_hotkey["keys"].setText(",".join(step.get("keys", [])))
        elif action == "search_by_text":
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
        elif action == "simple_click":
            _set_combo(
                self._editor_simple_click["button"],
                step.get("button", "left"),
            )
            self._editor_simple_click["clicks"].setValue(step.get("clicks", 1))

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
            step["offset_x"] = self._editor_click["offset_x"].value()
            # Flip Y sign on save so the stored offset matches the runner.
            step["offset_y"] = -self._editor_click["offset_y"].value()
            loop_var = self._editor_click["loop_variable"].currentText().strip()
            if loop_var:
                step["loop_variable"] = loop_var
        elif action == "wait_for_image":
            step["target"] = self._editor_wait["target"].currentText()
            step["confidence"] = self._editor_wait["confidence"].value()
        elif action == "type_value":
            step["value"] = self._editor_type["value"].text()
        elif action == "hotkey":
            keys_text = self._editor_hotkey["keys"].text()
            step["keys"] = [k.strip() for k in keys_text.split(",") if k.strip()]
        elif action == "search_by_text":
            step["query"] = self._editor_search_text["query"].text()
            wt = self._editor_search_text["window_title"].currentText().strip()
            if wt:
                step["window_title"] = wt
            step["match"] = self._editor_search_text["match"].currentText()
            step["case_sensitive"] = self._editor_search_text["case_sensitive"].isChecked()
            step["timeout"] = self._editor_search_text["timeout"].value()
        elif action == "simple_click":
            step["button"] = self._editor_simple_click["button"].currentText()
            step["clicks"] = self._editor_simple_click["clicks"].value()

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
