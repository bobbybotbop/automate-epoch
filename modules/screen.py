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

import logging
import traceback

import numpy as np
import pyautogui
import pygetwindow as gw
import pyscreeze
from PIL import Image

_log = logging.getLogger("flowdesk.detection")

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


def _patch_pyscreeze_screenshot_all_screens() -> None:
    """Windows: default PyScreeze capture is primary monitor only; match full desktop."""
    if sys.platform != "win32":
        return
    orig = pyscreeze.screenshot

    def screenshot(*args, **kwargs):
        if kwargs.get("region") is None:
            kwargs.setdefault("allScreens", True)
        return orig(*args, **kwargs)

    pyscreeze.screenshot = screenshot


_patch_pyscreeze_screenshot_all_screens()


def _is_pyinstaller_bundle() -> bool:
    """True when running under PyInstaller (onefile or onedir)."""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


# Throttle foregrounding the same window during OCR/image polling (get_window_region).
_WINDOW_ACTIVATE_COOLDOWN_SEC = 2.0
_window_activate_last: dict[str, float] = {}


def _full_screen_capture():
    """Bitmap for full-screen OCR and image matching.

    On Windows, prefer Pillow ``ImageGrab.grab(all_screens=True)`` so capture
    covers the virtual desktop (all monitors) and matches frozen PyInstaller
    builds more reliably than PyAutoGUI/PyScreeze defaults (``allScreens=False``).
    """
    if sys.platform == "win32":
        try:
            from PIL import ImageGrab

            return ImageGrab.grab(all_screens=True)
        except Exception:
            pass
        try:
            return pyautogui.screenshot(allScreens=True)
        except Exception:
            pass
    return pyautogui.screenshot()


def _region_screenshot(region: tuple[int, int, int, int]):
    """Screenshot of *(left, top, width, height)*, preferring Pillow on Windows."""
    left, top, w, h = region
    if sys.platform == "win32":
        try:
            from PIL import ImageGrab

            bbox = (left, top, left + w, top + h)
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception:
            pass
    return pyautogui.screenshot(region=region)


def _needle_for_locate(path: Path) -> str | np.ndarray:
    """Needle image the same way PyScreeze uses for ``locateOnScreen``: ``cv2.imread``
    when possible; otherwise BGR ndarray via Pillow (Unicode / OneDrive paths).
    """
    import cv2

    s = str(path)
    if cv2.imread(s, cv2.IMREAD_COLOR) is not None:
        return s
    pil = Image.open(path).convert("RGB")
    arr = np.array(pil)
    return arr[:, :, ::-1].copy()


# locateAll raises pyscreezes exception inside the generator; PyAutoGUIs wrapper
# does not always convert that to ImageNotFoundException.
_IMAGE_MATCH_EXCEPTIONS = (pyautogui.ImageNotFoundException, pyscreeze.ImageNotFoundException)


def _confidence_tiers(base: float) -> list[float]:
    """OpenCV match scores often fall well below 0.85 under DPI scaling or theme
    changes. Retry with lower thresholds after color/grayscale attempts at *base*.
    """
    tiers: list[float] = [base]
    if base > 0.5:
        tiers.append(max(0.35, min(base * 0.55, 0.55)))
    if base > 0.35:
        tiers.append(0.28)
    if base > 0.23:
        tiers.append(0.22)
    out: list[float] = []
    seen: set[float] = set()
    for t in tiers:
        if t >= 0.2 and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _first_non_excluded_box(
    needle: str | np.ndarray,
    haystack,
    *,
    confidence: float,
    grayscale: bool,
) -> tuple[int, int, int, int] | None:
    try:
        for location in pyautogui.locateAll(
            needle, haystack, confidence=confidence, grayscale=grayscale
        ):
            box = (
                int(location.left),
                int(location.top),
                int(location.width),
                int(location.height),
            )
            if not _is_excluded(box):
                return box
    except _IMAGE_MATCH_EXCEPTIONS:
        pass
    except Exception:
        _log.warning("locateAll unexpected error:\n%s", traceback.format_exc())
    return None


def _find_template_box(path: Path, confidence: float) -> tuple[int, int, int, int] | None:
    """First FlowDesk-excluding template hit across confidence tiers and grayscale."""
    needle = _needle_for_locate(path)
    needle_kind = "path" if isinstance(needle, str) else f"ndarray{needle.shape}"
    haystack = _full_screen_capture()
    _log.debug(
        "template_find  path=%s  needle=%s  haystack=%sx%s  tiers=%s",
        path, needle_kind, haystack.width, haystack.height,
        _confidence_tiers(confidence),
    )
    for conf in _confidence_tiers(confidence):
        for grayscale in (False, True):
            box = _first_non_excluded_box(
                needle, haystack, confidence=conf, grayscale=grayscale
            )
            if box is not None:
                _log.debug("template_hit  conf=%.2f  gs=%s  box=%s", conf, grayscale, box)
                return box
    _log.debug("template_miss  path=%s", path.name)
    return None


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
    """Single non-blocking screen check. Returns (x, y) center or None.

    Uses OpenCV template matching (PyScreeze) on ``_full_screen_capture()`` and
    tiered confidence plus grayscale fallback when strict thresholds miss (common
    with display scaling). Matches inside FlowDesk or toast UI are skipped.
    """
    path = Path(target_path)
    if not path.is_file():
        _log.warning("find_image: file missing  path=%s", path)
        return None
    box = _find_template_box(path, confidence)
    if box is None:
        return None
    x = int(box[0] + box[2] / 2)
    y = int(box[1] + box[3] / 2)
    return (x, y)


def find_image_box(
    target_path: str | Path, confidence: float = 0.85
) -> tuple[int, int, int, int] | None:
    """Single non-blocking screen check. Returns (left, top, width, height) or None.

    Matches that overlap FlowDesk's own window or toast area are ignored.
    See ``find_image``.
    """
    path = Path(target_path)
    if not path.is_file():
        _log.warning("find_image_box: file missing  path=%s", path)
        return None
    return _find_template_box(path, confidence)


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
    """Take a screenshot, optionally of a specific region (x, y, w, h).

    Full-screen uses the same capture path as OCR and image matching (see
    ``_full_screen_capture`` / ``_region_screenshot`` on Windows).
    """
    if region is None:
        return _full_screen_capture()
    return _region_screenshot(region)


def simple_click(button: str = "left", clicks: int = 1) -> None:
    """Click at the current cursor position without moving."""
    pyautogui.click(button=button, clicks=clicks)


# ---------------------------------------------------------------------------
# Self-exclusion: ignore matches inside FlowDesk windows and toast areas
# ---------------------------------------------------------------------------

_EXCLUSION_CACHE: tuple[float, list[tuple[int, int, int, int]]] = (0.0, [])
_EXCLUSION_TTL = 0.5  # seconds

_TOAST_W = 320
_TOAST_H = 56
_TOAST_MARGIN = 12
_TOAST_MAX_SLOTS = 3


def _rects_intersect(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> bool:
    """True when rectangles (left, top, width, height) overlap."""
    a_l, a_t, a_w, a_h = a
    b_l, b_t, b_w, b_h = b
    return (
        a_l < b_l + b_w
        and a_l + a_w > b_l
        and a_t < b_t + b_h
        and a_t + a_h > b_t
    )


def _point_in_any_rect(
    x: float, y: float, rects: list[tuple[int, int, int, int]]
) -> bool:
    for l, t, w, h in rects:
        if l <= x < l + w and t <= y < t + h:
            return True
    return False


def _get_flowdesk_window_rects() -> list[tuple[int, int, int, int]]:
    """Return screen rects for all visible FlowDesk windows."""
    rects: list[tuple[int, int, int, int]] = []
    try:
        for w in gw.getAllWindows():
            title = w.title or ""
            if "FlowDesk" not in title or not title.strip():
                continue
            if w.isMinimized or w.width <= 0 or w.height <= 0:
                continue
            rects.append((max(w.left, 0), max(w.top, 0), w.width, w.height))
    except Exception:
        pass
    return rects


def _get_toast_rects() -> list[tuple[int, int, int, int]]:
    """Return screen rects covering the bottom-right toast stack area."""
    try:
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            return []
        geo = screen.availableGeometry()
        right, bottom = geo.right(), geo.bottom()
    except Exception:
        sw, sh = pyautogui.size()
        right, bottom = sw - 1, sh - 1

    rects: list[tuple[int, int, int, int]] = []
    for i in range(_TOAST_MAX_SLOTS):
        x = right - _TOAST_W - _TOAST_MARGIN
        y = bottom - (i + 1) * (_TOAST_H + _TOAST_MARGIN)
        rects.append((x, y, _TOAST_W, _TOAST_H))
    return rects


def _get_exclusion_rects() -> list[tuple[int, int, int, int]]:
    """Cached list of screen regions to ignore during matching."""
    global _EXCLUSION_CACHE
    now = time.monotonic()
    if now - _EXCLUSION_CACHE[0] < _EXCLUSION_TTL:
        return _EXCLUSION_CACHE[1]
    rects = _get_flowdesk_window_rects() + _get_toast_rects()
    _EXCLUSION_CACHE = (now, rects)
    return rects


def _is_excluded(bbox: tuple[int, int, int, int]) -> bool:
    """True when *bbox* (left, top, w, h) overlaps any exclusion region."""
    for er in _get_exclusion_rects():
        if _rects_intersect(bbox, er):
            return True
    return False


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

    # Frozen EXE: foreground the target window so Pillow/GDI capture matches dev runs
    # when another app was last focused (throttled to avoid fighting the user).
    if _is_pyinstaller_bundle() and sys.platform == "win32":
        now = time.monotonic()
        last = _window_activate_last.get(title_substring, 0.0)
        if now - last >= _WINDOW_ACTIVATE_COOLDOWN_SEC:
            try:
                if getattr(win, "isActive", True) is False:
                    win.activate()
                    time.sleep(0.12)
            except Exception:
                pass
            _window_activate_last[title_substring] = now

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
    bundle_ok = exe_path.is_file()
    traineddata_ok = bundle_ok and (exe_path.parent / "tessdata" / "eng.traineddata").is_file()

    if bundle_ok and traineddata_ok:
        pytesseract.pytesseract.tesseract_cmd = str(exe_path)
        # Windows portable Tesseract (and pytesseract's invocation) expects
        # TESSDATA_PREFIX to point at the tessdata *folder* itself — it loads
        # PREFIX/eng.traineddata, not PREFIX/tessdata/eng.traineddata.
        tessdata = exe_path.parent / "tessdata"
        if tessdata.is_dir():
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
        _log.info(
            "Tesseract: bundled  cmd=%s  TESSDATA_PREFIX=%s",
            exe_path, os.environ.get("TESSDATA_PREFIX"),
        )
        return

    import shutil

    system_tess = shutil.which("tesseract")
    if system_tess:
        pytesseract.pytesseract.tesseract_cmd = system_tess
        _log.info("Tesseract: system PATH  cmd=%s", system_tess)
        return

    if not bundle_ok:
        raise TesseractMissingError(
            f"Bundled Tesseract-OCR not found.\n"
            f"Expected: {exe_path}"
        )
    raise TesseractMissingError(
        f"Language data missing — OCR cannot run without it.\n"
        f"Expected: {exe_path.parent / 'tessdata' / 'eng.traineddata'}"
    )


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
    """Image for OCR. Same capture path as ``screenshot`` / ``find_image``."""
    return screenshot(region)


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
    _log.debug(
        "OCR  query=%r  img=%sx%s  region=%s  word_rows=%d  sample=%s",
        word, img.width, img.height, source_region, len(rows),
        [r["text"] for r in rows[:8]],
    )
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
        _log.debug("OCR  no match for %r  (%d rows)", word, len(rows))
        return None

    region_left = source_region[0] if source_region else 0
    region_top = source_region[1] if source_region else 0

    bbox_left = region_left + chosen["left"]
    bbox_top = region_top + chosen["top"]
    bbox_width = chosen["width"]
    bbox_height = chosen["height"]

    if _is_excluded((bbox_left, bbox_top, bbox_width, bbox_height)):
        _log.debug(
            "OCR  match excluded  text=%r  bbox=(%d,%d,%d,%d)",
            chosen["text"], bbox_left, bbox_top, bbox_width, bbox_height,
        )
        return None

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
    offset_x: int = 0,
    offset_y: int = 0,
    move_duration: float = 0,
    on_search_begin: Callable[[], None] | None = None,
    on_found: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Poll-search for *query* via OCR, then move the mouse to the found text.

    If *window_title* is non-empty, OCR is limited to that window's region;
    otherwise the same full-screen capture as image matching is used.

    The cursor moves to the OCR match center, shifted by *offset_x* / *offset_y*
    (same convention as :func:`click_image`).

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
            dest_x = coords[0] + offset_x
            dest_y = coords[1] + offset_y
            move_to(dest_x, dest_y, duration=move_duration)
            return (dest_x, dest_y)

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
