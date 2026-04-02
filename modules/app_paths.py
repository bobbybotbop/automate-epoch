"""Application root directory for dev vs PyInstaller-frozen runs."""

from __future__ import annotations

import sys
from pathlib import Path


def application_base_dir() -> Path:
    """Project root in development; folder containing the .exe when frozen.

    PyInstaller one-file extracts code to a temp ``_MEIPASS`` tree. Using
    ``Path(__file__)`` for user data would read/write empty dirs under that
    temp folder instead of next to the executable.

    Some bundled runs set ``sys._MEIPASS`` without ``sys.frozen``; user data
    (``targets/``, ``automations/``, etc.) must still resolve next to
    ``sys.executable``, not under ``_MEIPASS``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if getattr(sys, "_MEIPASS", None) is not None:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
