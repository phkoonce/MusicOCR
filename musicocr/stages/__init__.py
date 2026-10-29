"""Pipeline stages. Each module exposes ``run(ctx) -> StageResult``.

This package also owns the stage registry: the canonical order and the loader.
"""
from __future__ import annotations

import importlib
from typing import Callable

# Canonical order. `correct` sits between OMR and extract so a corrected .omr
# project feeds the rest of the pipeline unchanged. `normalize` sits between
# extract and validate so it works on the merged score and its fixes show up
# in the same validate pass that would otherwise just flag them.
# `retouch` (optional, off by default) fixes edition-specific things (old bass clefs, title/composer)
# on the cleaned score before transpose declares the instrument.
# `transpose` (optional, off by default) follows normalize so it shifts the
# already-cleaned score, and precedes validate/convert so the deliverables carry
# the new pitch.
STAGE_ORDER = ["ingest", "omr", "correct", "extract", "normalize", "retouch", "transpose",
               "validate", "convert", "qa"]


def load_stage(name: str) -> Callable:
    """Import ``musicocr.stages.<name>`` and return its ``run`` callable."""
    return importlib.import_module(f"musicocr.stages.{name}").run
