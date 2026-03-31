"""PDF extraction engine using pdfplumber word-coordinate model.

Supports free-form PDFs with directional anchor-based rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pdfplumber

COORD_TOLERANCE = 5  # points – how close two words must be to count as "same row/col"


def load_rules(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules: list[dict], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)


def _extract_words(page: pdfplumber.page.Page) -> list[dict]:
    """Return list of word dicts with keys: text, x0, top, x1, bottom."""
    return page.extract_words(keep_blank_chars=False, extra_attrs=["top", "bottom"])


def _find_anchor(words: list[dict], anchor_text: str) -> list[dict]:
    """Find all words whose text matches the anchor (case-insensitive, stripped)."""
    anchor_lower = anchor_text.lower().strip()
    matches = []
    for w in words:
        if w["text"].lower().strip() == anchor_lower:
            matches.append(w)
    if not matches:
        joined = _try_joined_anchor(words, anchor_text)
        if joined:
            matches = joined
    return matches


def _try_joined_anchor(words: list[dict], anchor_text: str) -> list[dict]:
    """Handle multi-word anchors like 'Customer Name:' by joining adjacent words."""
    anchor_lower = anchor_text.lower().strip()
    tokens = anchor_lower.split()
    if len(tokens) < 2:
        return []

    matches = []
    for i in range(len(words) - len(tokens) + 1):
        candidate_words = words[i : i + len(tokens)]
        candidate_text = " ".join(w["text"].lower().strip() for w in candidate_words)
        if candidate_text == anchor_lower:
            combined = {
                "text": " ".join(w["text"] for w in candidate_words),
                "x0": candidate_words[0]["x0"],
                "top": candidate_words[0]["top"],
                "x1": candidate_words[-1]["x1"],
                "bottom": candidate_words[-1]["bottom"],
            }
            matches.append(combined)
    return matches


def _get_value_right(words: list[dict], anchor: dict, offset: int = 1) -> str | None:
    """Get the word N positions to the right of the anchor on the same row."""
    same_row = [
        w
        for w in words
        if abs(w["top"] - anchor["top"]) < COORD_TOLERANCE and w["x0"] > anchor["x1"]
    ]
    same_row.sort(key=lambda w: w["x0"])
    idx = offset - 1
    if 0 <= idx < len(same_row):
        return same_row[idx]["text"]
    return None


def _get_value_below(words: list[dict], anchor: dict, offset: int = 1) -> str | None:
    """Get the word N positions below the anchor in the same column."""
    same_col = [
        w
        for w in words
        if abs(w["x0"] - anchor["x0"]) < COORD_TOLERANCE
        and w["top"] > anchor["bottom"]
    ]
    same_col.sort(key=lambda w: w["top"])
    idx = offset - 1
    if 0 <= idx < len(same_col):
        return same_col[idx]["text"]
    return None


def _apply_rule(words: list[dict], rule: dict) -> str | None:
    """Apply a single rule against a word list, return first match value."""
    anchors = _find_anchor(words, rule["anchor"])
    if not anchors:
        return None

    direction = rule.get("direction", "right")
    offset = rule.get("offset", 1)

    for anchor in anchors:
        if direction == "right":
            val = _get_value_right(words, anchor, offset)
        elif direction == "below":
            val = _get_value_below(words, anchor, offset)
        else:
            val = None
        if val is not None:
            return val
    return None


def _segment_records(
    all_words: list[list[dict]], rules: list[dict]
) -> list[list[dict]]:
    """Segment words across pages into per-record groups.

    Uses the first rule's anchor as the record delimiter — each occurrence
    of that anchor starts a new record. Words are flattened across pages
    with a page-offset added to `top` so cross-page sorting works.
    """
    if not rules:
        return []

    PAGE_GAP = 2000
    flat_words: list[dict] = []
    for page_idx, page_words in enumerate(all_words):
        offset = page_idx * PAGE_GAP
        for w in page_words:
            flat_words.append(
                {
                    **w,
                    "top": w["top"] + offset,
                    "bottom": w["bottom"] + offset,
                    "_orig_top": w["top"],
                    "_orig_bottom": w["bottom"],
                }
            )

    primary_anchor = rules[0]["anchor"]
    anchor_positions = _find_anchor(flat_words, primary_anchor)

    if len(anchor_positions) <= 1:
        return [flat_words]

    anchor_positions.sort(key=lambda a: a["top"])

    segments: list[list[dict]] = []
    for i, anc in enumerate(anchor_positions):
        start_top = anc["top"] - COORD_TOLERANCE
        if i + 1 < len(anchor_positions):
            end_top = anchor_positions[i + 1]["top"] - COORD_TOLERANCE
        else:
            end_top = float("inf")
        segment = [w for w in flat_words if start_top <= w["top"] < end_top]
        segments.append(segment)

    return segments


def parse_pdf(pdf_path: str | Path, rules: list[dict]) -> list[dict[str, Any]]:
    """Parse a PDF and extract values for each record found.

    Returns a list of dicts — one dict per record, keys are rule_name values.
    """
    pdf_path = Path(pdf_path)
    all_words: list[list[dict]] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_rules = [r for r in rules if r.get("page") is None]
            page_specific = [r for r in rules if r.get("page") == page.page_number]
            _ = page_rules + page_specific  # noqa: F841 – reserved for future use
            all_words.append(_extract_words(page))

    segments = _segment_records(all_words, rules)
    records: list[dict[str, Any]] = []

    for segment in segments:
        record: dict[str, Any] = {}
        for rule in rules:
            record[rule["rule_name"]] = _apply_rule(segment, rule)
        records.append(record)

    return records


def parse_pdf_single(pdf_path: str | Path, rules: list[dict]) -> dict[str, Any]:
    """Parse a single-record PDF. Returns one dict of extracted values."""
    results = parse_pdf(pdf_path, rules)
    if results:
        return results[0]
    return {r["rule_name"]: None for r in rules}
