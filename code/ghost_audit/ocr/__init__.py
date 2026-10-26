"""OCR for screenshot evidence.

Order of engines: cached transcript (keyed by file sha256) -> Apple Vision via the
bundled Swift helper (macOS) -> pytesseract (if installed). The cache is written
back so later runs (including on machines without an OCR engine) are reproducible.
Failures are reported to the caller; nothing is invented.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE.parent.parent / "cache" / "ocr_cache.json"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(c, indent=1, sort_keys=True, ensure_ascii=False))


def _vision(path: Path) -> str:
    if sys.platform != "darwin" or not shutil.which("swift"):
        raise RuntimeError("Apple Vision unavailable")
    r = subprocess.run(["swift", str(HERE / "vision_ocr.swift"), str(path)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"vision ocr failed: {r.stderr[:200]}")
    # drop the y/x position columns
    return "\n".join(line.split("\t", 2)[-1] for line in r.stdout.splitlines())


def _tesseract(path: Path) -> str:
    import pytesseract  # type: ignore
    from PIL import Image
    return pytesseract.image_to_string(Image.open(path))


def ocr_image(path: Path) -> tuple[str, str]:
    """Return (text, engine). Raises RuntimeError if no engine/cached result exists."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = _load_cache()
    if sha in cache:
        return cache[sha]["text"], cache[sha]["engine"] + "+cache"
    errors = []
    for name, fn in (("apple-vision", _vision), ("tesseract", _tesseract)):
        try:
            text = fn(path)
            cache[sha] = {"engine": name, "file": path.name, "text": text}
            _save_cache(cache)
            return text, name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    raise RuntimeError("; ".join(errors))
