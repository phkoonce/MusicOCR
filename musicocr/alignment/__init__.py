"""Spec-driven alignment of per-part homr MusicXML to a score's printed bars.

Two front-ends decide which homr measure becomes which printed bar:

* :mod:`musicocr.alignment.units` -- from a hand-written list of multi-rest
  ranges and rest bars (``omr-eval/align_combine.py``);
* :mod:`musicocr.alignment.systems` -- automatically, from the part PDFs' text
  layer (``omr-eval/align_systems.py``).

Both share the spec loading and MusicXML building blocks in
:mod:`musicocr.alignment.spec` and :mod:`musicocr.alignment.build`.
"""
from __future__ import annotations


class AlignmentError(RuntimeError):
    """Raised when a part cannot be aligned to the spec's printed structure."""
