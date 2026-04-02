"""Required files for packaging FlowDesk with PyInstaller (UI build + FlowDesk.spec)."""

from __future__ import annotations

import sys
from pathlib import Path


def missing_build_inputs(root: Path) -> list[str]:
    """Return human-readable relative paths for anything missing under *root*."""
    missing: list[str] = []
    root = root.resolve()
    if not (root / "main.py").is_file():
        missing.append("main.py")
    tess = root / "Tesseract-OCR"
    if not tess.is_dir():
        missing.append("Tesseract-OCR/ (directory)")
        return missing
    if sys.platform == "win32":
        if not (tess / "tesseract.exe").is_file():
            missing.append("Tesseract-OCR/tesseract.exe")
    else:
        if not (tess / "tesseract").is_file() and not (tess / "tesseract.exe").is_file():
            missing.append("Tesseract-OCR/tesseract")
    if not (tess / "tessdata" / "eng.traineddata").is_file():
        missing.append("Tesseract-OCR/tessdata/eng.traineddata")
    return missing


def ensure_build_inputs_or_exit(root: Path) -> None:
    """Abort the PyInstaller spec with a clear message if inputs are missing."""
    miss = missing_build_inputs(root)
    if not miss:
        return
    lines = "\n".join(f"  • {m}" for m in miss)
    sys.exit(
        "FlowDesk build aborted — missing required files:\n\n"
        f"{lines}\n\n"
        "Place a complete portable Tesseract-OCR tree next to main.py "
        "(see project docs)."
    )
