"""PyAutoGUI wrapper for screen automation actions.

Provides high-level functions for image-based clicking, typing, hotkeys,
and waiting for screen elements with configurable confidence and timeout.
"""

from __future__ import annotations

import time
from pathlib import Path

import pyautogui

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
