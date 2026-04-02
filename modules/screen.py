"""PyAutoGUI wrapper for screen automation actions.

Provides high-level functions for image-based mouse movement, typing,
waiting for screen elements, OCR text search, and simple clicks.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pyautogui
import pygetwindow as gw

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


class TargetNotFoundError(Exception):
    """Raised when a screen target image cannot be located within the timeout."""

    def __init__(self, target: str, timeout: float):
        self.target = target
        self.timeout = timeout
        super().__init__(
            f"Target '{target}' not found on screen after {timeout:.1f}s"
        )


def find_image(
    target_path: str | Path, confidence: float = 0.85
) -> tuple[int, int] | None:
    """Single non-blocking screen check. Returns (x, y) center or None."""
    try:
        location = pyautogui.locateOnScreen(str(target_path), confidence=confidence)
    except pyautogui.ImageNotFoundException:
        return None
    if location is None:
        return None
    center = pyautogui.center(location)
    return (int(center.x), int(center.y))


def find_image_box(
    target_path: str | Path, confidence: float = 0.85
) -> tuple[int, int, int, int] | None:
    """Single non-blocking screen check. Returns (left, top, width, height) or None."""
    try:
        location = pyautogui.locateOnScreen(str(target_path), confidence=confidence)
    except pyautogui.ImageNotFoundException:
        return None
    if location is None:
        return None
    return (int(location.left), int(location.top), int(location.width), int(location.height))


def wait_for_image(
    target_path: str | Path,
    confidence: float = 0.85,
    timeout: float = 0,
    poll_interval: float = 0.5,
    on_search_begin: Callable[[], None] | None = None,
    on_found: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Poll the screen until the target image appears. Returns (x, y) center.

    *timeout* in seconds.  ``0`` (default) means wait indefinitely.
    Raises TargetNotFoundError if a finite timeout elapses without a match.
    """
    if on_search_begin:
        on_search_begin()
    infinite = timeout <= 0
    deadline = None if infinite else time.monotonic() + timeout
    while True:
        coords = find_image(target_path, confidence)
        if coords is not None:
            if on_found:
                on_found()
            return coords
        if not infinite and time.monotonic() >= deadline:
            raise TargetNotFoundError(str(target_path), timeout)
        time.sleep(poll_interval)


def click_image(
    target_path: str | Path,
    confidence: float = 0.85,
    timeout: float = 0,
    offset_x: int = 0,
    offset_y: int = 0,
    move_duration: float = 0,
    on_search_begin: Callable[[], None] | None = None,
    on_found: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Wait for a target image then move the mouse to it (no click).

    Polls the screen until the image is found, then moves the cursor to
    the image center (shifted by *offset_x* / *offset_y*).

    *timeout* in seconds.  ``0`` (default) means wait indefinitely.
    Returns the (x, y) coordinates the mouse was moved to.
    Raises TargetNotFoundError if a finite timeout elapses without a match.
    """
    coords = wait_for_image(
        target_path, confidence, timeout,
        on_search_begin=on_search_begin, on_found=on_found,
    )
    dest_x = coords[0] + offset_x
    dest_y = coords[1] + offset_y
    move_to(dest_x, dest_y, duration=move_duration)
    return (dest_x, dest_y)


def type_value(text: str, interval: float = 0.02) -> None:
    """Type text into the currently focused field."""
    pyautogui.typewrite(text, interval=interval) if text.isascii() else _type_unicode(text, interval)


def _type_unicode(text: str, interval: float) -> None:
    """Handle non-ASCII text by using the clipboard as a fallback."""
    import subprocess
    subprocess.run(
        ["powershell", "-command", f"Set-Clipboard -Value '{text}'"],
        check=True,
        capture_output=True,
    )
    pyautogui.hotkey("ctrl", "v")
    time.sleep(interval * len(text))


def move_to(x: int, y: int, duration: float = 0) -> None:
    """Move the mouse to absolute screen coordinates."""
    pyautogui.moveTo(x, y, duration=duration)


def screenshot(region: tuple[int, int, int, int] | None = None):
    """Take a screenshot, optionally of a specific region (x, y, w, h)."""
    return pyautogui.screenshot(region=region)


def simple_click(button: str = "left", clicks: int = 1) -> None:
    """Click at the current cursor position without moving."""
    pyautogui.click(button=button, clicks=clicks)


# ---------------------------------------------------------------------------
# OCR text search (ported from pytesseract-coordinate-finder)
# ---------------------------------------------------------------------------

class TextNotFoundError(Exception):
    """Raised when OCR text search fails within timeout."""

    def __init__(self, query: str, timeout: float):
        self.query = query
        self.timeout = timeout
        super().__init__(
            f"Text '{query}' not found on screen after {timeout:.1f}s"
        )


class TesseractMissingError(Exception):
    """Raised when pytesseract / Tesseract-OCR is not available."""

    def __init__(self, details: str | None = None) -> None:
        msg = (
            "Tesseract OCR is not available.\n\n"
            "Place the bundled Tesseract-OCR folder (with tesseract.exe + tessdata/)\n"
            "next to main.py or inside the PyInstaller bundle."
        )
        if details:
            msg += f"\n\nDetails:\n{details}"
        super().__init__(msg)


def get_window_region(
    title_substring: str,
) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) for a window whose title contains
    *title_substring* (case-insensitive).

    Raises ``TargetNotFoundError`` if no matching window is found or the
    window is minimized / zero-sized.
    """
    matches = [
        w
        for w in gw.getAllWindows()
        if title_substring.lower() in (w.title or "").lower() and w.title.strip()
    ]
    if not matches:
        raise TargetNotFoundError(f"window '{title_substring}'", 0)

    win = matches[0]
    if win.isMinimized:
        win.restore()
        time.sleep(0.4)

    left, top = win.left, win.top
    w, h = win.width, win.height
    if w <= 0 or h <= 0:
        raise TargetNotFoundError(f"window '{title_substring}' (zero size)", 0)

    return (max(left, 0), max(top, 0), w, h)


def _resource_path(relative_path: str) -> str:
    """Resolve a path for dev runs and PyInstaller one-file builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return str(Path(base) / relative_path)
    return str(Path(__file__).resolve().parent.parent / relative_path)


def _configure_tesseract_cmd(pytesseract) -> None:
    """Point pytesseract at the bundled tesseract.exe and tessdata."""
    existing = str(
        getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    ).strip()
    if existing and existing != "tesseract" and Path(existing).is_file():
        return

    exe_path = Path(_resource_path("Tesseract-OCR/tesseract.exe"))
    if not exe_path.is_file():
        raise TesseractMissingError(
            f"Bundled Tesseract-OCR not found.\n"
            f"Expected: {exe_path}"
        )

    pytesseract.pytesseract.tesseract_cmd = str(exe_path)

    tessdata = exe_path.parent / "tessdata"
    if tessdata.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)


def _ensure_pytesseract():
    """Lazy-import pytesseract, raising a friendly error if missing."""
    try:
        import pytesseract  # noqa: F811
        _configure_tesseract_cmd(pytesseract)
        return pytesseract
    except ImportError:
        raise TesseractMissingError(
            "Python package `pytesseract` is not installed."
        )


def ocr_screenshot(region: tuple[int, int, int, int] | None = None):
    """Image for OCR. *region=None* captures the full virtual desktop (all
    monitors on Windows via Pillow when available), matching image-target
    behavior. With *region*, crops via PyAutoGUI.
    """
    if region is not None:
        return pyautogui.screenshot(region=region)
    if sys.platform == "win32":
        try:
            from PIL import ImageGrab

            return ImageGrab.grab(all_screens=True)
        except Exception:
            pass
    return pyautogui.screenshot()


# ---------------------------------------------------------------------------
# OCR matching internals (ported from pytesseract-coordinate-finder)
# ---------------------------------------------------------------------------

Region = tuple[int, int, int, int]
MatchStrategy = Literal["best", "first"]


@dataclass(frozen=True)
class OcrMatch:
    coords: tuple[float, float]
    bbox: tuple[int, int, int, int]
    confidence: float
    matched_text: str
    source_region: Region


def _tesseract_config(*, psm: int | None) -> str:
    if psm is None:
        return ""
    return f"--psm {int(psm)}"


def _iter_word_rows(data: dict) -> Iterable[dict]:
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        conf_raw = data.get("conf", ["-1"] * n)[i]
        try:
            conf = float(conf_raw)
        except Exception:
            conf = -1.0
        yield {
            "i": i,
            "text": text,
            "conf": conf,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
            "block_num": int(data.get("block_num", [0] * n)[i]),
            "par_num": int(data.get("par_num", [0] * n)[i]),
            "line_num": int(data.get("line_num", [0] * n)[i]),
            "word_num": int(data.get("word_num", [0] * n)[i]),
        }


def _union_bbox(rows: list[dict]) -> tuple[int, int, int, int]:
    left = min(int(r["left"]) for r in rows)
    top = min(int(r["top"]) for r in rows)
    right = max(int(r["left"]) + int(r["width"]) for r in rows)
    bottom = max(int(r["top"]) + int(r["height"]) for r in rows)
    return (left, top, int(right - left), int(bottom - top))


def _select_match(
    *,
    rows: list[dict],
    word: str,
    min_conf: float,
    match_strategy: str,
    match_index: int,
    case_sensitive: bool,
    allow_contains: bool,
) -> dict | None:
    if not case_sensitive:
        needle = word.casefold()

        def norm(s: str) -> str:
            return s.casefold()
    else:
        needle = word

        def norm(s: str) -> str:
            return s

    exact: list[dict] = []
    contains: list[dict] = []

    for r in rows:
        if r["conf"] < min_conf:
            continue
        hay = norm(r["text"])
        if hay == needle:
            exact.append(r)
        elif allow_contains and needle in hay:
            contains.append(r)

    candidates = exact or contains
    if not candidates:
        return None

    if match_index < 0:
        raise ValueError("match_index must be >= 0.")

    if match_strategy == "first":
        return candidates[match_index] if match_index < len(candidates) else None

    ranked = sorted(candidates, key=lambda r: (r["conf"], -r["i"]), reverse=True)
    return ranked[match_index] if match_index < len(ranked) else None


def _select_phrase_match(
    *,
    rows: list[dict],
    phrase: str,
    min_conf: float,
    match_strategy: str,
    match_index: int,
    case_sensitive: bool,
) -> dict | None:
    if not phrase or not phrase.strip():
        return None

    phrase = " ".join(phrase.strip().split())
    if not case_sensitive:
        def norm(s: str) -> str:
            return s.casefold()
    else:
        def norm(s: str) -> str:
            return s

    tokens = [t for t in phrase.split(" ") if t]
    if not tokens:
        return None

    line_groups: dict[tuple[int, int, int], list[dict]] = {}
    for r in rows:
        if r["conf"] < min_conf:
            continue
        key = (int(r.get("block_num", 0)), int(r.get("par_num", 0)), int(r.get("line_num", 0)))
        line_groups.setdefault(key, []).append(r)

    candidates: list[dict] = []
    for _, line_rows in line_groups.items():
        line_rows_sorted = sorted(
            line_rows, key=lambda r: (int(r.get("word_num", 0)), int(r["left"]), int(r["top"]))
        )
        line_tokens = [norm(r["text"]) for r in line_rows_sorted]
        tokens_norm = [norm(t) for t in tokens]
        if len(line_tokens) < len(tokens_norm):
            continue
        for start in range(0, len(line_tokens) - len(tokens_norm) + 1):
            window = line_tokens[start : start + len(tokens_norm)]
            if window != tokens_norm:
                continue

            matched_rows = line_rows_sorted[start : start + len(tokens_norm)]
            left, top, width, height = _union_bbox(matched_rows)
            conf = sum(float(r["conf"]) for r in matched_rows) / max(1, len(matched_rows))
            candidates.append({
                "i": min(int(r["i"]) for r in matched_rows),
                "text": phrase,
                "conf": float(conf),
                "left": int(left),
                "top": int(top),
                "width": int(width),
                "height": int(height),
            })

    if not candidates:
        return None

    if match_index < 0:
        raise ValueError("match_index must be >= 0.")

    if match_strategy == "first":
        ordered = sorted(candidates, key=lambda r: int(r["i"]))
        return ordered[match_index] if match_index < len(ordered) else None

    ranked = sorted(candidates, key=lambda r: (float(r["conf"]), -int(r["i"])), reverse=True)
    return ranked[match_index] if match_index < len(ranked) else None


def _tokenize_punct_phrase(s: str) -> list[str]:
    s = s.strip()
    if not s:
        return []
    s = "".join(ch for ch in s if not ch.isspace())
    if not s:
        return []
    return re.findall(r"\w+|[^\w]", s, flags=re.UNICODE)


def _select_punct_phrase_match(
    *,
    rows: list[dict],
    phrase: str,
    min_conf: float,
    match_strategy: str,
    match_index: int,
    case_sensitive: bool,
    optional_punct: set[str],
) -> dict | None:
    if not phrase or not phrase.strip():
        return None

    if not case_sensitive:
        def norm(s: str) -> str:
            return s.casefold()
    else:
        def norm(s: str) -> str:
            return s

    query_tokens = _tokenize_punct_phrase(phrase)
    if not query_tokens:
        return None

    variants: list[list[str]] = [query_tokens]
    for p in list(query_tokens):
        if p in optional_punct:
            variants.append([t for t in query_tokens if t != p])

    seen: set[tuple[str, ...]] = set()
    uniq_variants: list[list[str]] = []
    for v in variants:
        key = tuple(v)
        if key in seen:
            continue
        if not v:
            continue
        seen.add(key)
        uniq_variants.append(v)

    line_groups: dict[tuple[int, int, int], list[dict]] = {}
    for r in rows:
        if r["conf"] < min_conf:
            continue
        key = (int(r.get("block_num", 0)), int(r.get("par_num", 0)), int(r.get("line_num", 0)))
        line_groups.setdefault(key, []).append(r)

    candidates: list[dict] = []
    for _, line_rows in line_groups.items():
        line_rows_sorted = sorted(
            line_rows, key=lambda r: (int(r.get("word_num", 0)), int(r["left"]), int(r["top"]))
        )
        line_tokens = [norm(r["text"]) for r in line_rows_sorted]

        for variant in uniq_variants:
            tokens_norm = [norm(t) for t in variant]
            if len(line_tokens) < len(tokens_norm):
                continue
            for start in range(0, len(line_tokens) - len(tokens_norm) + 1):
                window = line_tokens[start : start + len(tokens_norm)]
                if window != tokens_norm:
                    continue

                matched_rows = line_rows_sorted[start : start + len(tokens_norm)]
                left, top, width, height = _union_bbox(matched_rows)
                conf = sum(float(r["conf"]) for r in matched_rows) / max(1, len(matched_rows))
                candidates.append({
                    "i": min(int(r["i"]) for r in matched_rows),
                    "text": phrase,
                    "conf": float(conf),
                    "left": int(left),
                    "top": int(top),
                    "width": int(width),
                    "height": int(height),
                })

    if not candidates:
        return None

    if match_index < 0:
        raise ValueError("match_index must be >= 0.")

    if match_strategy == "first":
        ordered = sorted(candidates, key=lambda r: int(r["i"]))
        return ordered[match_index] if match_index < len(ordered) else None

    ranked = sorted(candidates, key=lambda r: (float(r["conf"]), -int(r["i"])), reverse=True)
    return ranked[match_index] if match_index < len(ranked) else None


def _center_of_bbox(left: int, top: int, width: int, height: int) -> tuple[float, float]:
    return (left + width / 2.0, top + height / 2.0)


def _approx_letter_coords_within_word_bbox(
    *,
    word: str,
    letter: str,
    letter_index: int,
    bbox_left: int,
    bbox_top: int,
    bbox_width: int,
    bbox_height: int,
) -> tuple[float, float]:
    indices = [i for i, ch in enumerate(word) if ch == letter]
    if not indices:
        raise ValueError(f"Letter {letter!r} is not in word {word!r}.")
    if letter_index < 0 or letter_index >= len(indices):
        raise ValueError(
            f"letter_index {letter_index} out of range for {letter!r} in {word!r} (found {len(indices)} occurrences)."
        )
    i = indices[letter_index]
    x = bbox_left + ((i + 0.5) / max(1, len(word))) * bbox_width
    y = bbox_top + bbox_height / 2.0
    return (x, y)


def _precise_letter_coords_from_cropped_boxes(
    *,
    pytesseract,
    cropped_img,
    bbox_left: int,
    bbox_top: int,
    letter: str,
    letter_index: int,
    case_sensitive: bool,
) -> tuple[float, float] | None:
    try:
        boxes_str = pytesseract.image_to_boxes(cropped_img)
    except Exception:
        return None

    lines = [ln.strip() for ln in boxes_str.splitlines() if ln.strip()]
    if not lines:
        return None

    occurrences: list[tuple[float, float]] = []
    img_h = int(getattr(cropped_img, "height", 0) or 0)
    if img_h <= 0:
        return None

    letter_norm = letter if case_sensitive else letter.casefold()

    for ln in lines:
        parts = ln.split()
        if len(parts) < 5:
            continue
        ch = parts[0]
        ch_norm = ch if case_sensitive else ch.casefold()
        if ch_norm != letter_norm:
            continue
        try:
            left = int(parts[1])
            bottom = int(parts[2])
            right = int(parts[3])
            top = int(parts[4])
        except Exception:
            continue
        x_local = (left + right) / 2.0
        y_local_from_bottom = (bottom + top) / 2.0
        y_local = img_h - y_local_from_bottom
        occurrences.append((bbox_left + x_local, bbox_top + y_local))

    if letter_index < 0 or letter_index >= len(occurrences):
        return None
    return occurrences[letter_index]


def locate_text_match(
    word: str,
    *,
    letter: str | None = None,
    letter_index: int = 0,
    window_title: str | None = None,
    region: Region | None = None,
    min_conf: float = 60.0,
    match_strategy: MatchStrategy = "best",
    match_index: int = 0,
    case_sensitive: bool = True,
    allow_contains: bool = False,
    lang: str = "eng",
    psm: int | None = None,
    precise_letter: bool = False,
) -> OcrMatch | None:
    if not word or not word.strip():
        raise ValueError("word must be a non-empty string.")
    word = word.strip()

    if letter is not None and len(letter) != 1:
        raise ValueError("letter must be a single character (or None).")

    pytesseract = _ensure_pytesseract()

    source_region = region
    if window_title:
        source_region = get_window_region(window_title)

    img = ocr_screenshot(source_region)
    config = _tesseract_config(psm=psm)
    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT, lang=lang, config=config
    )

    rows = list(_iter_word_rows(data))
    if any(ch.isspace() for ch in word):
        chosen = _select_phrase_match(
            rows=rows,
            phrase=word,
            min_conf=min_conf,
            match_strategy=match_strategy,
            match_index=match_index,
            case_sensitive=case_sensitive,
        )
    else:
        chosen = _select_match(
            rows=rows,
            word=word,
            min_conf=min_conf,
            match_strategy=match_strategy,
            match_index=match_index,
            case_sensitive=case_sensitive,
            allow_contains=allow_contains,
        )
        if chosen is None and any((not ch.isalnum()) for ch in word) and not allow_contains:
            chosen = _select_punct_phrase_match(
                rows=rows,
                phrase=word,
                min_conf=min_conf,
                match_strategy=match_strategy,
                match_index=match_index,
                case_sensitive=case_sensitive,
                optional_punct={"."},
            )

    if chosen is None:
        return None

    region_left = source_region[0] if source_region else 0
    region_top = source_region[1] if source_region else 0

    bbox_left = region_left + chosen["left"]
    bbox_top = region_top + chosen["top"]
    bbox_width = chosen["width"]
    bbox_height = chosen["height"]

    if letter is None:
        x, y = _center_of_bbox(bbox_left, bbox_top, bbox_width, bbox_height)
    else:
        word_for_letter = word if case_sensitive else word.casefold()
        letter_for_letter = letter if case_sensitive else letter.casefold()

        if precise_letter:
            crop_left = int(chosen["left"])
            crop_top = int(chosen["top"])
            crop_right = crop_left + int(bbox_width)
            crop_bottom = crop_top + int(bbox_height)
            cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))
            precise = _precise_letter_coords_from_cropped_boxes(
                pytesseract=pytesseract,
                cropped_img=cropped,
                bbox_left=bbox_left,
                bbox_top=bbox_top,
                letter=letter_for_letter,
                letter_index=letter_index,
                case_sensitive=case_sensitive,
            )
        else:
            precise = None

        if precise is not None:
            x, y = precise
        else:
            x, y = _approx_letter_coords_within_word_bbox(
                word=word_for_letter,
                letter=letter_for_letter,
                letter_index=letter_index,
                bbox_left=bbox_left,
                bbox_top=bbox_top,
                bbox_width=bbox_width,
                bbox_height=bbox_height,
            )

    return OcrMatch(
        coords=(x, y),
        bbox=(bbox_left, bbox_top, bbox_width, bbox_height),
        confidence=float(chosen["conf"]),
        matched_text=str(chosen["text"]),
        source_region=source_region if source_region else (0, 0, int(img.width), int(img.height)),
    )


def locate_text(
    word: str,
    letter: str | None = None,
    *,
    window_title: str | None = None,
    region: Region | None = None,
) -> tuple[float, float] | None:
    match = locate_text_match(
        word,
        letter=letter,
        window_title=window_title,
        region=region,
    )
    return None if match is None else match.coords


# ---------------------------------------------------------------------------
# Public OCR API (signatures unchanged for caller compatibility)
# ---------------------------------------------------------------------------

def find_text_on_screen(
    query: str,
    region: tuple[int, int, int, int] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
) -> tuple[int, int] | None:
    """Single non-blocking OCR pass. Returns screen-space (x, y) center of
    the best-matching word/phrase, or *None*.
    """
    match = locate_text_match(
        query,
        region=region,
        min_conf=60.0,
        match_strategy="best",
        match_index=0,
        case_sensitive=case_sensitive,
        allow_contains=(match_mode != "exact"),
    )
    if match is None:
        return None
    return (int(round(match.coords[0])), int(round(match.coords[1])))


def find_text_box_on_screen(
    query: str,
    region: tuple[int, int, int, int] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
) -> tuple[int, int, int, int] | None:
    """Single non-blocking OCR pass. Returns screen-space (left, top, w, h)
    of the best-matching word/phrase, or *None*.
    """
    match = locate_text_match(
        query,
        region=region,
        min_conf=60.0,
        match_strategy="best",
        match_index=0,
        case_sensitive=case_sensitive,
        allow_contains=(match_mode != "exact"),
    )
    if match is None:
        return None
    left, top, w, h = match.bbox
    return (int(left), int(top), int(w), int(h))


def find_text_box_and_point_on_screen(
    query: str,
    region: tuple[int, int, int, int] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int] | None]:
    """Single non-blocking OCR pass. Returns ``(box, point)``.

    *box*: screen-space ``(left, top, w, h)`` for the best match.
    *point*: screen-space ``(x, y)``.  For single-word queries this aims at
    the center of the word's middle letter (using ``image_to_boxes``).
    Falls back to the box center when letter-level inference isn't possible.
    """
    match = locate_text_match(
        query,
        region=region,
        min_conf=60.0,
        match_strategy="best",
        match_index=0,
        case_sensitive=case_sensitive,
        allow_contains=(match_mode != "exact"),
        precise_letter=True,
    )
    if match is None:
        return (None, None)
    left, top, w, h = match.bbox
    px, py = match.coords
    return ((int(left), int(top), int(w), int(h)), (int(round(px)), int(round(py))))


# After each failed OCR, sleep grows (capped) so long timeouts spend fewer full
# passes than a fixed delay every time, while the first wait matches *poll_interval*.
_SEARCH_TEXT_BACKOFF_FACTOR = 1.5
_SEARCH_TEXT_MAX_SLEEP_CAP = 3.0


def search_text(
    query: str,
    window_title: str | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
    timeout: float = 10.0,
    poll_interval: float = 0.8,
    move_duration: float = 0,
    on_search_begin: Callable[[], None] | None = None,
    on_found: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Poll-search for *query* via OCR, then move the mouse to the found text.

    If *window_title* is non-empty, OCR is limited to that window's region;
    otherwise the full virtual desktop is scanned (all monitors when supported).

    Between failed attempts, wait time starts at *poll_interval* and increases with
    each miss (capped), so a long *timeout* runs fewer full OCR passes than a
    fixed *poll_interval* on every retry.

    If *poll_interval* is <= 0, retries use ``time.sleep(0)`` only (same as before).

    Returns the (x, y) screen coordinates the mouse was moved to.
    Raises ``TextNotFoundError`` if not found within *timeout*.
    """
    region = None
    wt = (window_title or "").strip()
    if wt:
        region = get_window_region(wt)

    if on_search_begin:
        on_search_begin()

    deadline = time.monotonic() + timeout
    if poll_interval <= 0:
        next_sleep = 0.0
    else:
        next_sleep = poll_interval
        max_sleep_between = min(
            _SEARCH_TEXT_MAX_SLEEP_CAP,
            max(poll_interval * 3.0, poll_interval),
        )

    while time.monotonic() < deadline:
        coords = find_text_on_screen(
            query, region=region, match_mode=match_mode, case_sensitive=case_sensitive
        )
        if coords is not None:
            if on_found:
                on_found()
            move_to(coords[0], coords[1], duration=move_duration)
            return coords

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if poll_interval <= 0:
            time.sleep(0)
        else:
            time.sleep(min(next_sleep, remaining))
            next_sleep = min(
                next_sleep * _SEARCH_TEXT_BACKOFF_FACTOR,
                max_sleep_between,
            )

    raise TextNotFoundError(query, timeout)
