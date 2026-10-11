"""Loading per-piece spec modules and the bar-geometry helpers they feed.

A *spec* is a plain Python file (kept locally under ``omr-eval/specs/``, one per
piece).  Attributes used by the shared code:

* ``TITLE`` (str), ``COMPOSER`` (str, may be empty), ``TOTAL_BARS`` (int)
* ``meter(bar) -> (beats, beat_type)``
* optional ``FIRST_BAR_BEATS`` -- length of bar 1 in quarter notes (pickup)
* optional ``MARKS`` -- ``{part_id | "all": {bar: [(kind, *args), ...]}}`` with
  kind ``"tempo"`` (text, bpm), ``"rehearsal"`` (letter) or ``"words"`` (text)

Front-end specific attributes are documented in ``units`` and ``systems``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Union

from . import AlignmentError

DIV = 10080  # <divisions> per quarter used for all generated output


def load_spec(path: Union[str, Path]) -> ModuleType:
    """Import a spec file by path (it need not be on ``sys.path``)."""
    p = Path(path)
    if not p.is_file():
        raise AlignmentError(f"spec file not found: {p}")
    s = importlib.util.spec_from_file_location(f"spec_{p.stem}", p)
    if s is None or s.loader is None:
        raise AlignmentError(f"cannot load spec: {p}")
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def bar_len(spec: Any, bar: int) -> int:
    """Length of printed ``bar`` in ``DIV`` units (honours ``FIRST_BAR_BEATS``)."""
    first = getattr(spec, "FIRST_BAR_BEATS", None)
    if bar == 1 and first:
        return first * DIV
    beats, beat_type = spec.meter(bar)
    return beats * DIV * 4 // beat_type
