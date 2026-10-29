"""Stage 2: transcribe the source PDF into MusicXML, via the configured OMR engine.

Dispatches on ``[omr] engine`` in config.toml, using the registry in
``musicocr.engines``:
  * ``homr``       (default) - musicocr.engines.homr.stage — an end-to-end OMR
    model that consistently outperformed Audiveris on monophonic wind/brass
    parts in local evaluation (see omr-eval/README.md).
  * ``audiveris``  - musicocr.engines.audiveris — the classical
    segment-classify-reconstruct engine, tunable via ``[omr.profiles.*]``
    constants and the only engine with a GUI hand-correction round-trip.
  * ``copisteria`` - musicocr.engines.copisteria.stage — page-image reader with a
    learned evidence model.
  * ``legato2``    - musicocr.engines.legato2.stage — LEGATO 2 (system finder + ABC
    transcription, converted to MusicXML with music21).

Downstream stages (correct, extract) only see the shared artifacts every
engine writes (``mxl_files``, ``mxl_by_page``, ``omr_failed_pages``,
``omr_log``, ``omr_project``) and don't need to know which engine ran.
"""
from __future__ import annotations

import importlib

from musicocr.engines import ENGINES
from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext

# name -> module (loaded lazily); tests may monkeypatch entries.
_ENGINES: dict = {}


def _module(name: str):
    if name not in _ENGINES:
        _ENGINES[name] = importlib.import_module(ENGINES[name].module)
    return _ENGINES[name]


def run(ctx: PipelineContext) -> StageResult:
    engine = ctx.config.omr.engine
    if engine not in ENGINES:
        raise StageError(f"unknown [omr] engine {engine!r}; known: {sorted(ENGINES)}")
    return _module(engine).run(ctx)
