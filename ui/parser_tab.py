"""Parser tab — rule editor, PDF test runner, results table."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modules.parser import load_rules_bundle, parse_pdf, save_rules

RULES_DIR: Path = Path()
_current_file: Path | None = None


class ParserTab(QWidget):
    def __init__(self, rules_dir: Path):
        super().__init__()
        global RULES_DIR
        RULES_DIR = rules_dir

        self._current_file: Path | None = None
        self._rules: list[dict] = []
        self._pdf_path: str | None = None
        self._test_data_tokens: list[str] = []
        self._dirty = False

        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # --- Left: rule set file list + rule list ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        file_heading = QLabel("Rule Sets")
        file_heading.setObjectName("heading")
        left_layout.addWidget(file_heading)

        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        left_layout.addWidget(self.file_list)

        file_btns = QHBoxLayout()
        btn_new_file = QPushButton("New Set")
        btn_new_file.clicked.connect(self._new_rule_set)
        btn_del_file = QPushButton("Delete Set")
        btn_del_file.setObjectName("danger")
        btn_del_file.clicked.connect(self._delete_rule_set)
        file_btns.addWidget(btn_new_file)
        file_btns.addWidget(btn_del_file)
        left_layout.addLayout(file_btns)

        rule_heading = QLabel("Rules")
        rule_heading.setObjectName("heading")
        left_layout.addWidget(rule_heading)

        self.rule_list = QListWidget()
        self.rule_list.currentRowChanged.connect(self._on_rule_selected)
        left_layout.addWidget(self.rule_list)

        rule_btns = QHBoxLayout()
        btn_add = QPushButton("Add Rule")
        btn_add.clicked.connect(self._add_rule)
        btn_del = QPushButton("Delete Rule")
        btn_del.setObjectName("danger")
        btn_del.clicked.connect(self._delete_rule)
        rule_btns.addWidget(btn_add)
        rule_btns.addWidget(btn_del)
        left_layout.addLayout(rule_btns)

        splitter.addWidget(left)

        # --- Center: rule editor form ---
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)

        form_heading = QLabel("Rule Editor")
        form_heading.setObjectName("heading")
        center_layout.addWidget(form_heading)

        self._rule_editor_hint = QLabel("Select a rule from the list to edit.")
        self._rule_editor_hint.setObjectName("subtext")
        self._rule_editor_hint.setWordWrap(True)
        center_layout.addWidget(self._rule_editor_hint)

        self.mode_label = QLabel("Mode")
        center_layout.addWidget(self.mode_label)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Normal", "Test"])
        self.combo_mode.currentTextChanged.connect(self._on_mode_changed)
        center_layout.addWidget(self.combo_mode)

        self.rule_name_label = QLabel("Rule Name")
        center_layout.addWidget(self.rule_name_label)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g. customer_name")
        center_layout.addWidget(self.edit_name)

        self.anchor_label = QLabel("Anchor Text")
        center_layout.addWidget(self.anchor_label)
        self.edit_anchor = QLineEdit()
        self.edit_anchor.setPlaceholderText('e.g. Customer:')
        center_layout.addWidget(self.edit_anchor)

        self.direction_label = QLabel("Direction")
        center_layout.addWidget(self.direction_label)
        self.combo_direction = QComboBox()
        self.combo_direction.addItems(["right", "below"])
        center_layout.addWidget(self.combo_direction)

        self.offset_label = QLabel("Offset")
        center_layout.addWidget(self.offset_label)
        self.spin_offset = QSpinBox()
        self.spin_offset.setMinimum(1)
        self.spin_offset.setMaximum(50)
        self.spin_offset.setValue(1)
        center_layout.addWidget(self.spin_offset)

        self.word_count_label = QLabel("Word count")
        center_layout.addWidget(self.word_count_label)
        self.wc_hint = QLabel(
            "How many consecutive words to capture (e.g. 2 for first and last name)."
        )
        self.wc_hint.setObjectName("subtext")
        self.wc_hint.setWordWrap(True)
        center_layout.addWidget(self.wc_hint)
        self.spin_word_count = QSpinBox()
        self.spin_word_count.setMinimum(1)
        self.spin_word_count.setMaximum(50)
        self.spin_word_count.setValue(2)
        self.spin_word_count.setToolTip(
            "Joins this many words in reading order after the offset (same row or column)."
        )
        center_layout.addWidget(self.spin_word_count)

        self.data_label = QLabel("Data")
        center_layout.addWidget(self.data_label)
        self.edit_data = QLineEdit()
        self.edit_data.setPlaceholderText("e.g. invoice_no, customer_name total")
        center_layout.addWidget(self.edit_data)

        btn_save_rule = QPushButton("Save Rule")
        btn_save_rule.setObjectName("primary")
        btn_save_rule.clicked.connect(self._save_current_rule)
        center_layout.addWidget(btn_save_rule)

        self._rule_editor_widgets = (
            self.mode_label,
            self.combo_mode,
            self.rule_name_label,
            self.edit_name,
            self.anchor_label,
            self.edit_anchor,
            self.direction_label,
            self.combo_direction,
            self.offset_label,
            self.spin_offset,
            self.word_count_label,
            self.wc_hint,
            self.spin_word_count,
            self.data_label,
            self.edit_data,
            btn_save_rule,
        )

        center_layout.addStretch()
        splitter.addWidget(center)

        # --- Right: test PDF + results ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        test_heading = QLabel("Test Results")
        test_heading.setObjectName("heading")
        right_layout.addWidget(test_heading)

        pdf_row = QHBoxLayout()
        self.pdf_label = QLabel("No PDF loaded")
        self.pdf_label.setObjectName("subtext")
        self.btn_load_pdf = QPushButton("Load PDF")
        self.btn_load_pdf.clicked.connect(self._load_pdf)
        self.btn_run = QPushButton("Run Rules")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run_rules)
        pdf_row.addWidget(self.pdf_label, 1)
        pdf_row.addWidget(self.btn_load_pdf)
        pdf_row.addWidget(self.btn_run)
        right_layout.addLayout(pdf_row)

        self.results_table = QTableWidget()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        right_layout.addWidget(self.results_table)

        splitter.addWidget(right)
        splitter.setSizes([220, 260, 420])

        self._set_rule_editor_visible(False)
        self._on_mode_changed(self.combo_mode.currentText())
        self._refresh_file_list()

    # --- File management ---

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self._refresh_file_list()

    def _refresh_file_list(self):
        prev = self.file_list.currentItem()
        prev_name = prev.text() if prev else None
        self.file_list.clear()
        for f in sorted(RULES_DIR.glob("*.json")):
            self.file_list.addItem(f.stem)
        if prev_name:
            items = self.file_list.findItems(prev_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.file_list.setCurrentItem(items[0])

    def _on_file_selected(self, current: QListWidgetItem | None, _prev):
        if current is None:
            return
        self._save_if_dirty()
        path = RULES_DIR / f"{current.text()}.json"
        self._current_file = path
        if path.exists():
            self._rules, meta = load_rules_bundle(path)
            self._test_data_tokens = list(meta.get("test_data") or [])
            mode = (meta.get("editor_mode") or "normal").lower()
        else:
            self._rules = []
            self._test_data_tokens = []
            mode = "normal"
        self._dirty = False

        self.combo_mode.blockSignals(True)
        idx = self.combo_mode.findText("Test" if mode == "test" else "Normal")
        self.combo_mode.setCurrentIndex(max(0, idx))
        self.combo_mode.blockSignals(False)

        self._refresh_rule_list()
        if self.rule_list.count() > 0:
            self.rule_list.setCurrentRow(0)
        else:
            self._set_rule_editor_visible(False)
        self._on_mode_changed(self.combo_mode.currentText())

    def _new_rule_set(self):
        name, ok = _ask_text(self, "New Rule Set", "Name:")
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "_")
        path = RULES_DIR / f"{name}.json"
        save_rules([], path)
        self._refresh_file_list()
        items = self.file_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.file_list.setCurrentItem(items[0])

    def _delete_rule_set(self):
        item = self.file_list.currentItem()
        if not item:
            return
        path = RULES_DIR / f"{item.text()}.json"
        if path.exists():
            path.unlink()
        self._current_file = None
        self._rules = []
        self._refresh_file_list()
        self._refresh_rule_list()
        self._set_rule_editor_visible(False)

    # --- Rule list ---

    def _has_rule_selection(self) -> bool:
        row = self.rule_list.currentRow()
        return 0 <= row < len(self._rules)

    def _set_rule_editor_visible(self, visible: bool):
        self._rule_editor_hint.setVisible(not visible)
        for w in self._rule_editor_widgets:
            w.setVisible(visible)
        if visible:
            self._on_mode_changed(self.combo_mode.currentText())

    def _refresh_rule_list(self):
        self.rule_list.clear()
        for r in self._rules:
            self.rule_list.addItem(r.get("rule_name", "(unnamed)"))
        if not self._rules:
            self._set_rule_editor_visible(False)

    def _on_rule_selected(self, row: int):
        if row < 0 or row >= len(self._rules):
            self._set_rule_editor_visible(False)
            return
        self._set_rule_editor_visible(True)
        rule = self._rules[row]
        self.edit_name.setText(rule.get("rule_name", ""))
        self.edit_anchor.setText(rule.get("anchor", ""))
        direction = rule.get("direction", "right")
        idx = self.combo_direction.findText(direction)
        if idx >= 0:
            self.combo_direction.setCurrentIndex(idx)
        self.spin_offset.setValue(rule.get("offset", 1))
        self.spin_word_count.setValue(rule.get("word_count", 2))
        if self._is_test_mode():
            self.edit_data.setText(" ".join(self._test_data_tokens))

    def _add_rule(self):
        new_rule = {
            "rule_name": "new_rule",
            "anchor": "",
            "direction": "right",
            "offset": 1,
            "word_count": 2,
            "page": None,
        }
        self._rules.append(new_rule)
        self._dirty = True
        self._refresh_rule_list()
        self.rule_list.setCurrentRow(len(self._rules) - 1)

    def _delete_rule(self):
        row = self.rule_list.currentRow()
        if 0 <= row < len(self._rules):
            self._rules.pop(row)
            self._dirty = True
            self._refresh_rule_list()
            self._persist()
            if self._rules:
                self.rule_list.setCurrentRow(min(row, len(self._rules) - 1))

    def _save_current_rule(self):
        if self._is_test_mode():
            self._save_test_data_rules()
            return

        row = self.rule_list.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        self._rules[row] = {
            "rule_name": self.edit_name.text().strip(),
            "anchor": self.edit_anchor.text().strip(),
            "direction": self.combo_direction.currentText(),
            "offset": self.spin_offset.value(),
            "word_count": self.spin_word_count.value(),
            "page": None,
        }
        self._dirty = True
        self._persist()
        self._refresh_rule_list()
        self.rule_list.setCurrentRow(row)

    def _save_test_data_rules(self):
        rule_name = self.edit_name.text().strip()
        if not rule_name:
            QMessageBox.warning(self, "No Rule Name", "Enter a rule name first.")
            return

        self._test_data_tokens = self._parse_data_tokens(self.edit_data.text())
        if self._rules:
            self._rules[0]["rule_name"] = rule_name
            self._rules[0].setdefault("anchor", "")
            self._rules[0].setdefault("direction", "right")
            self._rules[0].setdefault("offset", 1)
            self._rules[0].setdefault("word_count", 1)
            self._rules[0].setdefault("page", None)
            self._rules = [self._rules[0]]
        else:
            self._rules = [
                {
                    "rule_name": rule_name,
                    "anchor": "",
                    "direction": "right",
                    "offset": 1,
                    "word_count": 1,
                    "page": None,
                }
            ]
        self._dirty = True
        self._persist()
        self._refresh_rule_list()
        self.rule_list.setCurrentRow(0)

    def _is_test_mode(self) -> bool:
        return self.combo_mode.currentText().lower() == "test"

    def _on_mode_changed(self, mode_text: str):
        is_test_mode = mode_text.lower() == "test"
        has_rule = self._has_rule_selection()
        if has_rule:
            for w in (
                self.anchor_label,
                self.edit_anchor,
                self.direction_label,
                self.combo_direction,
                self.offset_label,
                self.spin_offset,
                self.word_count_label,
                self.wc_hint,
                self.spin_word_count,
            ):
                w.setVisible(not is_test_mode)

            self.data_label.setVisible(is_test_mode)
            self.edit_data.setVisible(is_test_mode)
            if is_test_mode:
                self.edit_data.setText(" ".join(self._test_data_tokens))
        if is_test_mode:
            self.pdf_label.setText("PDF not required in Test mode")
            self.btn_load_pdf.setEnabled(False)
        else:
            self.btn_load_pdf.setEnabled(True)
            self.pdf_label.setText(
                Path(self._pdf_path).name if self._pdf_path else "No PDF loaded"
            )

    def _parse_data_tokens(self, raw_data: str) -> list[str]:
        parts = [p.strip() for p in re.split(r"[\s,]+", raw_data) if p.strip()]
        seen: set[str] = set()
        tokens: list[str] = []
        for part in parts:
            token = part.replace(" ", "_")
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        return tokens

    def _persist(self):
        if self._current_file:
            if self._is_test_mode():
                save_rules(
                    self._rules,
                    self._current_file,
                    meta={
                        "editor_mode": "test",
                        "test_data": self._test_data_tokens,
                    },
                )
            else:
                save_rules(self._rules, self._current_file)
            self._dirty = False

    def _save_if_dirty(self):
        if self._dirty:
            self._persist()

    # --- PDF test ---

    def _load_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF Files (*.pdf)"
        )
        if path:
            self._pdf_path = path
            self.pdf_label.setText(Path(path).name)

    def _run_rules(self):
        if self._is_test_mode():
            if not self._rules:
                QMessageBox.warning(self, "No Rules", "Add at least one rule.")
                return
            data_tokens = self._parse_data_tokens(self.edit_data.text())
            if not data_tokens:
                QMessageBox.warning(
                    self,
                    "No Data",
                    "Enter one or more values in Data (space or comma separated).",
                )
                return
            self._test_data_tokens = data_tokens
            self._show_results([{"data": token} for token in data_tokens])
            return

        if not self._pdf_path:
            QMessageBox.warning(self, "No PDF", "Load a PDF first.")
            return
        if not self._rules:
            QMessageBox.warning(self, "No Rules", "Add at least one rule.")
            return

        try:
            records = parse_pdf(self._pdf_path, self._rules)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", str(e))
            return

        self._show_results(records)

    def _show_results(self, records: list[dict]):
        if not records:
            self.results_table.clear()
            self.results_table.setRowCount(0)
            self.results_table.setColumnCount(0)
            return

        columns = list(records[0].keys())
        self.results_table.setColumnCount(len(columns))
        self.results_table.setHorizontalHeaderLabels(columns)
        self.results_table.setRowCount(len(records))

        for row_idx, record in enumerate(records):
            for col_idx, col in enumerate(columns):
                val = record.get(col, "")
                item = QTableWidgetItem(str(val) if val is not None else "")
                self.results_table.setItem(row_idx, col_idx, item)


def _ask_text(parent, title: str, label: str) -> tuple[str, bool]:
    from PyQt6.QtWidgets import QInputDialog
    return QInputDialog.getText(parent, title, label)
