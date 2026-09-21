"""Stage 2: transcribe the source PDF into MusicXML, via the configured OMR engine.

Dispatches on ``[omr] engine`` in config.toml:
  * ``homr``      (default) - musicocr.stages.omr_homr — an end-to-end OMR
    model that consistently outperformed Audiveris on monophonic wind/brass
    parts in local evaluation (see omr-eval/README.md).
  * ``audiveris``            - musicocr.stages.omr_audiveris — the classical
    segment-classify-reconstruct engine, tunable via ``[omr.profiles.*]``
    constants and the only engine with a GUI hand-correction round-trip.

Downstream stages (correct, extract) only see the shared artifacts both
engines write (``mxl_files``, ``mxl_by_page``, ``omr_failed_pages``,
``omr_log``, ``omr_project``) and don't need to know which engine ran.
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageError, StageResult
from musicocr.stages import omr_audiveris, omr_homr

_ENGINES = {"homr": omr_homr, "audiveris": omr_audiveris}


def run(ctx: PipelineContext) -> StageResult:
    engine = ctx.config.omr_engine
    mod = _ENGINES.get(engine)
    if mod is None:
        raise StageError(f"unknown [omr] engine {engine!r}; known: {sorted(_ENGINES)}")
    return mod.run(ctx)
