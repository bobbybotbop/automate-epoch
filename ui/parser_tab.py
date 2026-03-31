"""Parser tab — rule editor, PDF test runner, results table."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
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

from modules.parser import load_rules, parse_pdf, save_rules

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

        center_layout.addWidget(QLabel("Rule Name"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g. customer_name")
        center_layout.addWidget(self.edit_name)

        center_layout.addWidget(QLabel("Anchor Text"))
        self.edit_anchor = QLineEdit()
        self.edit_anchor.setPlaceholderText('e.g. Customer:')
        center_layout.addWidget(self.edit_anchor)

        center_layout.addWidget(QLabel("Direction"))
        self.combo_direction = QComboBox()
        self.combo_direction.addItems(["right", "below"])
        center_layout.addWidget(self.combo_direction)

        center_layout.addWidget(QLabel("Offset"))
        self.spin_offset = QSpinBox()
        self.spin_offset.setMinimum(1)
        self.spin_offset.setMaximum(50)
        self.spin_offset.setValue(1)
        center_layout.addWidget(self.spin_offset)

        center_layout.addWidget(QLabel("Word count"))
        wc_hint = QLabel(
            "How many consecutive words to capture (e.g. 2 for first and last name)."
        )
        wc_hint.setObjectName("subtext")
        wc_hint.setWordWrap(True)
        center_layout.addWidget(wc_hint)
        self.spin_word_count = QSpinBox()
        self.spin_word_count.setMinimum(1)
        self.spin_word_count.setMaximum(50)
        self.spin_word_count.setValue(2)
        self.spin_word_count.setToolTip(
            "Joins this many words in reading order after the offset (same row or column)."
        )
        center_layout.addWidget(self.spin_word_count)

        btn_save_rule = QPushButton("Save Rule")
        btn_save_rule.setObjectName("primary")
        btn_save_rule.clicked.connect(self._save_current_rule)
        center_layout.addWidget(btn_save_rule)

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
        btn_load_pdf = QPushButton("Load PDF")
        btn_load_pdf.clicked.connect(self._load_pdf)
        btn_run = QPushButton("Run Rules")
        btn_run.setObjectName("primary")
        btn_run.clicked.connect(self._run_rules)
        pdf_row.addWidget(self.pdf_label, 1)
        pdf_row.addWidget(btn_load_pdf)
        pdf_row.addWidget(btn_run)
        right_layout.addLayout(pdf_row)

        self.results_table = QTableWidget()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        right_layout.addWidget(self.results_table)

        splitter.addWidget(right)
        splitter.setSizes([220, 260, 420])

        self._refresh_file_list()

    # --- File management ---

    def _refresh_file_list(self):
        self.file_list.clear()
        for f in sorted(RULES_DIR.glob("*.json")):
            self.file_list.addItem(f.stem)

    def _on_file_selected(self, current: QListWidgetItem | None, _prev):
        if current is None:
            return
        self._save_if_dirty()
        path = RULES_DIR / f"{current.text()}.json"
        self._current_file = path
        self._rules = load_rules(path) if path.exists() else []
        self._dirty = False
        self._refresh_rule_list()

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

    # --- Rule list ---

    def _refresh_rule_list(self):
        self.rule_list.clear()
        for r in self._rules:
            self.rule_list.addItem(r.get("rule_name", "(unnamed)"))

    def _on_rule_selected(self, row: int):
        if row < 0 or row >= len(self._rules):
            return
        rule = self._rules[row]
        self.edit_name.setText(rule.get("rule_name", ""))
        self.edit_anchor.setText(rule.get("anchor", ""))
        direction = rule.get("direction", "right")
        idx = self.combo_direction.findText(direction)
        if idx >= 0:
            self.combo_direction.setCurrentIndex(idx)
        self.spin_offset.setValue(rule.get("offset", 1))
        self.spin_word_count.setValue(rule.get("word_count", 2))

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

    def _save_current_rule(self):
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

    def _persist(self):
        if self._current_file:
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
