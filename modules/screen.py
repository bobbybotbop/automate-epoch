"""PyAutoGUI wrapper for screen automation actions.

Provides high-level functions for image-based clicking, typing, hotkeys,
waiting for screen elements, OCR text search, and simple clicks.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> tuple[int, int]:
    """Poll the screen until the target image appears. Returns (x, y) center.

    Raises TargetNotFoundError if not found within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        coords = find_image(target_path, confidence)
        if coords is not None:
            return coords
        time.sleep(poll_interval)
    raise TargetNotFoundError(str(target_path), timeout)


def click_image(
    target_path: str | Path,
    confidence: float = 0.85,
    timeout: float = 10.0,
    clicks: int = 1,
    offset_x: int = 0,
    offset_y: int = 0,
) -> tuple[int, int]:
    """Locate a target image on screen and click with optional offset.

    *offset_x* / *offset_y* shift the click away from the image center
    (positive x = right, positive y = down).

    Returns the (x, y) coordinates that were clicked.
    Raises TargetNotFoundError if not found within timeout.
    """
    coords = wait_for_image(target_path, confidence, timeout)
    click_x = coords[0] + offset_x
    click_y = coords[1] + offset_y
    pyautogui.click(click_x, click_y, clicks=clicks)
    return (click_x, click_y)


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


def hotkey(*keys: str) -> None:
    """Send a keyboard shortcut (e.g. hotkey('ctrl', 'p'))."""
    pyautogui.hotkey(*keys)


def move_to(x: int, y: int, duration: float = 0.2) -> None:
    """Move the mouse to absolute screen coordinates."""
    pyautogui.moveTo(x, y, duration=duration)


def screenshot(region: tuple[int, int, int, int] | None = None):
    """Take a screenshot, optionally of a specific region (x, y, w, h)."""
    return pyautogui.screenshot(region=region)


def simple_click(button: str = "left", clicks: int = 1) -> None:
    """Click at the current cursor position without moving."""
    pyautogui.click(button=button, clicks=clicks)


# ---------------------------------------------------------------------------
# OCR text search
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
            "Option A — bundled with the app:\n"
            "  Place Tesseract-OCR/tesseract.exe (+ tessdata/) next to main.py\n"
            "  or inside the PyInstaller bundle.\n\n"
            "Option B — system install:\n"
            "  Install from https://github.com/UB-Mannheim/tesseract/wiki\n"
            "  and ensure tesseract.exe is on PATH."
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
    """Point pytesseract at a usable tesseract.exe and tessdata."""
    existing = str(
        getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    ).strip()
    if existing and existing != "tesseract" and Path(existing).is_file():
        return

    if sys.platform != "win32":
        return

    candidates: list[Path] = [
        Path(_resource_path("Tesseract-OCR/tesseract.exe")),
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]

    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        checked = "\n".join(f"  - {p}" for p in candidates)
        raise TesseractMissingError(f"Checked:\n{checked}")

    pytesseract.pytesseract.tesseract_cmd = str(found)

    tessdata = found.parent / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))


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


def find_text_on_screen(
    query: str,
    region: tuple[int, int, int, int] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
) -> tuple[int, int] | None:
    """Single non-blocking OCR pass. Returns screen-space (x, y) center of
    the best-matching word/phrase, or *None*.
    """
    box = find_text_box_on_screen(
        query, region=region, match_mode=match_mode, case_sensitive=case_sensitive
    )
    if box is None:
        return None
    left, top, w, h = box
    return (left + w // 2, top + h // 2)


def find_text_box_on_screen(
    query: str,
    region: tuple[int, int, int, int] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
) -> tuple[int, int, int, int] | None:
    """Single non-blocking OCR pass. Returns screen-space (left, top, w, h)
    of the best-matching word/phrase, or *None*.
    """
    pytesseract = _ensure_pytesseract()
    from pytesseract import Output

    img = ocr_screenshot(region)
    data = pytesseract.image_to_data(img, output_type=Output.DICT)

    n = len(data["text"])
    words: list[tuple[str, int, int, int, int]] = []
    for i in range(n):
        txt = data["text"][i]
        if not txt or not txt.strip():
            continue
        words.append((
            txt.strip(),
            int(data["left"][i]),
            int(data["top"][i]),
            int(data["width"][i]),
            int(data["height"][i]),
        ))

    if not words:
        return None

    q = query if case_sensitive else query.lower()

    full_text_parts: list[tuple[str, int, int, int, int]] = []
    for word, lx, ty, ww, hh in words:
        cmp = word if case_sensitive else word.lower()
        full_text_parts.append((cmp, lx, ty, ww, hh))

    if match_mode == "exact":
        for cmp, lx, ty, ww, hh in full_text_parts:
            if cmp == q:
                if region:
                    lx += region[0]
                    ty += region[1]
                return (lx, ty, ww, hh)
    else:
        q_words = q.split()
        if len(q_words) <= 1:
            for cmp, lx, ty, ww, hh in full_text_parts:
                if q in cmp:
                    if region:
                        lx += region[0]
                        ty += region[1]
                    return (lx, ty, ww, hh)
        else:
            for start in range(len(full_text_parts) - len(q_words) + 1):
                span = full_text_parts[start : start + len(q_words)]
                joined = " ".join(s[0] for s in span)
                if q in joined:
                    lx = span[0][1]
                    ty = min(s[2] for s in span)
                    rx = max(s[1] + s[3] for s in span)
                    by = max(s[2] + s[4] for s in span)
                    ww = rx - lx
                    hh = by - ty
                    if region:
                        lx += region[0]
                        ty += region[1]
                    return (lx, ty, ww, hh)

    return None


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
    pytesseract = _ensure_pytesseract()

    box = find_text_box_on_screen(
        query, region=region, match_mode=match_mode, case_sensitive=case_sensitive
    )
    if box is None:
        return (None, None)

    left, top, w, h = box
    fallback_point = (left + w // 2, top + h // 2)

    q = query.strip()
    if not q or len(q.split()) != 1:
        return (box, fallback_point)

    img = ocr_screenshot(region)
    img_h = int(getattr(img, "size", (0, 0))[1] or 0)
    if img_h <= 0:
        return (box, fallback_point)

    # Convert the matched word box to local screenshot coordinates.
    local_left, local_top = left, top
    if region:
        local_left -= region[0]
        local_top -= region[1]
    local_box = (local_left, local_top, w, h)

    try:
        boxes_text = pytesseract.image_to_boxes(img)
    except Exception:
        return (box, fallback_point)

    # Parse per-character boxes (Tesseract uses bottom-left origin).
    char_centers: list[tuple[float, float]] = []
    for line in (boxes_text or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            lx = int(parts[1])
            by = int(parts[2])
            rx = int(parts[3])
            ty = int(parts[4])
        except ValueError:
            continue

        top_y = img_h - ty
        bottom_y = img_h - by
        cx = (lx + rx) / 2.0
        cy = (top_y + bottom_y) / 2.0

        bl, bt, bw, bh = local_box
        if bl <= cx <= bl + bw and bt <= cy <= bt + bh:
            char_centers.append((cx, cy))

    if not char_centers:
        return (box, fallback_point)

    char_centers.sort(key=lambda p: p[0])
    mid = len(char_centers) // 2
    if len(char_centers) % 2 == 1:
        cx, cy = char_centers[mid]
    else:
        cx = (char_centers[mid - 1][0] + char_centers[mid][0]) / 2.0
        cy = (char_centers[mid - 1][1] + char_centers[mid][1]) / 2.0

    if region:
        cx += region[0]
        cy += region[1]

    return (box, (int(round(cx)), int(round(cy))))


def search_text(
    query: str,
    window_title: str | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
    timeout: float = 10.0,
    poll_interval: float = 0.8,
    move_duration: float = 0.2,
) -> tuple[int, int]:
    """Poll-search for *query* via OCR, then move the mouse to the found text.

    If *window_title* is non-empty, OCR is limited to that window's region;
    otherwise the full virtual desktop is scanned (all monitors when supported).

    Returns the (x, y) screen coordinates the mouse was moved to.
    Raises ``TextNotFoundError`` if not found within *timeout*.
    """
    region = None
    wt = (window_title or "").strip()
    if wt:
        region = get_window_region(wt)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        coords = find_text_on_screen(
            query, region=region, match_mode=match_mode, case_sensitive=case_sensitive
        )
        if coords is not None:
            move_to(coords[0], coords[1], duration=move_duration)
            return coords
        time.sleep(poll_interval)

    raise TextNotFoundError(query, timeout)
