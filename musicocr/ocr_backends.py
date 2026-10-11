"""Pluggable OCR backends for scanned pages: image -> ``[{"text", "xyxy"}]`` word boxes.

``ocr_words(png, backend)`` is the single seam; boxes are in pixels of ``png``.
A backend is a function ``(png, log) -> list[dict] | None`` registered in
``BACKENDS`` (``None``/empty = unavailable or nothing found).

* ``rapidocr``: PP-OCR via ONNX, run by the homr venv's interpreter
  (``rapidocr_run.py``; models ship inside the package, nothing is downloaded).
* ``vision``: macOS Vision text recognition via a tiny Swift program
  (``vision_ocr.swift``), compiled on first use with ``swiftc`` into the user
  cache dir. Needs the Xcode command-line tools; no packages or downloads.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from musicocr.util import run_cmd_lenient

_SWIFT_SRC = Path(__file__).with_name("vision_ocr.swift")
_CACHE = Path.home() / "Library" / "Caches" / "musicocr"


def _vision_binary(log=None) -> Path | None:
    """Path of the compiled Vision OCR helper, building it if needed."""
    if sys.platform != "darwin" or not _SWIFT_SRC.exists():
        return None
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    digest = hashlib.sha1(_SWIFT_SRC.read_bytes()).hexdigest()[:10]
    exe = _CACHE / f"vision_ocr-{digest}"
    if exe.exists():
        return exe
    _CACHE.mkdir(parents=True, exist_ok=True)
    if log:
        log("  compiling the Vision OCR helper (one-time)")
    try:
        res = subprocess.run([swiftc, "-O", str(_SWIFT_SRC), "-o", str(exe)],
                             capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if res.returncode == 0 and exe.exists() else None


def _vision(png: Path, log=None, **opts) -> list[dict] | None:
    exe = _vision_binary(log)
    if not exe:
        return None
    res = run_cmd_lenient([str(exe), str(png)], timeout=300, log=log)
    if res is None or res.returncode != 0:
        return None
    for line in reversed(res.output.splitlines()):    # skip any stderr noise
        if line.startswith("["):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def _rapidocr(png: Path, log=None, *, python: str | None = None, **opts) -> list[dict] | None:
    if not python or not Path(python).exists():
        return None
    res = run_cmd_lenient([python, "-I", str(Path(__file__).with_name("rapidocr_run.py")), str(png)],
                          timeout=300, log=log)
    if res is None or res.returncode != 0:
        return None
    for line in reversed(res.output.splitlines()):
        if line.startswith("["):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


BACKENDS: dict[str, Callable] = {"rapidocr": _rapidocr, "vision": _vision}


def _same(a: list[float], b: list[float]) -> bool:
    """Boxes are the same word if their centres each lie inside the other."""
    def inside(p, q):
        return q[0] <= (p[0] + p[2]) / 2 <= q[2] and q[1] <= (p[1] + p[3]) / 2 <= q[3]
    return inside(a, b) and inside(b, a)


def ocr_words(png: Path, backend: str = "auto", log=None, **opts) -> list[dict]:
    """Word boxes on ``png``. ``backend`` is a ``BACKENDS`` name, or "auto" = the union
    of every backend that works (they miss different isolated numerals), de-duplicated
    by box overlap. ``opts`` (e.g. ``python=``) are passed to each backend."""
    names = list(BACKENDS) if backend == "auto" else [backend]
    merged: list[dict] = []
    for name in names:
        fn = BACKENDS.get(name)
        if fn is None:
            continue
        try:
            words = fn(png, log, **opts)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"  OCR backend {name} failed ({type(exc).__name__}: {exc})")
            continue
        for w in words or []:
            if not any(w["text"] == m["text"] and _same(w["xyxy"], m["xyxy"]) for m in merged):
                merged.append(w)
    return merged
