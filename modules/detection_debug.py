"""Detection debug logger — always on in frozen builds, opt-in via FLOWDESK_DETECTION_DEBUG=1 in dev."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = logging.getLogger("flowdesk.detection")
_logger.setLevel(logging.DEBUG)
_configured = False


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def setup_detection_logging(logs_dir: Path) -> None:
    """Call once from main.py after logs_dir exists."""
    global _configured
    if _configured:
        return
    _configured = True

    enabled = _is_frozen() or os.environ.get("FLOWDESK_DETECTION_DEBUG", "").strip() == "1"
    if not enabled:
        _logger.addHandler(logging.NullHandler())
        return

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S"
    )

    fh = RotatingFileHandler(
        logs_dir / "detection_debug.log",
        maxBytes=512 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    if not _is_frozen():
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(fmt)
        _logger.addHandler(ch)

    _logger.info(
        "Detection logging started  frozen=%s  _MEIPASS=%s",
        getattr(sys, "frozen", False),
        getattr(sys, "_MEIPASS", None),
    )


def get_logger() -> logging.Logger:
    return _logger
