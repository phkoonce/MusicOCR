"""Stage 2 (LEGATO 2 engine): placeholder -- no released model yet.

LEGATO 2 (Yang et al., arXiv:2607.05769) is a system-by-system neural OMR model
that emits symbolic (ABC) output. As of 2026-10 only the paper is public: no
code or weights. This module reserves the engine name so config/CLI/registry are
ready; wire it up when a release exists:

1. Add ``omr-eval/legato2/`` venv + weights to ``omr-eval/setup.sh`` and a
   ``[tools] legato2_python`` entry; flip ``available`` in ``musicocr.engines``.
2. Implement ``run``: segment a page into systems (the model works one system
   at a time -- possibly via the shared ``musicocr.alignment.systems`` code),
   transcribe each, and convert ABC -> MusicXML (music21's ABC parser first,
   ``abc2xml`` as fallback) into ``pages/pNNN/pNNN.musicxml``.
3. Reuse ``musicocr.stages._omr_common.run_raster_pages`` if the model's CLI
   is image-in/file-out, otherwise write the per-system loop here and publish
   the same artifacts (``mxl_files``, ``mxl_by_page``, ``omr_failed_pages``,
   ``omr_log``, ``omr_project``).
"""
from __future__ import annotations

from musicocr.engines import ENGINES
from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext


def run(ctx: PipelineContext) -> StageResult:
    raise StageError(f"engine 'legato2' is not usable yet: {ENGINES['legato2'].note}")
