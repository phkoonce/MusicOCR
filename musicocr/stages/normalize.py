"""Stage 5 (between extract and validate): force each measure's duration to
match its time signature.

Mitigates the dominant homr rhythm-misread pattern documented in
omr-eval/README.md (a note read a step too long, e.g. "6.0 beats, expected
4.0") which otherwise leaves MuseScore's barlines out of alignment with
neighboring measures -- annoying for hand-correction. See
``musicocr.fixes.measure_normalize`` for the mechanics and its trade-offs (lossy:
trades note accuracy, already wrong, for a uniform grid to correct against).

``[stages.normalize] enabled`` (default true) controls this stage; it's a
mechanical, engine-agnostic pass (only measures that already disagree with
their time signature are touched), so it's safe to leave on for Audiveris
output too.
"""
from __future__ import annotations

from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext
from musicocr.stages._common import require_musicxml


def run(ctx: PipelineContext) -> StageResult:
    from musicocr.fixes import SCORE_FIXES, FixContext, fix_enabled, run_fixes

    fix = SCORE_FIXES[0]
    if not fix_enabled(fix, ctx.config):
        return StageResult("normalize", "skipped",
                           "disabled ([fixes] measure_lengths = false, or [stages.normalize] enabled = false)")

    xml = require_musicxml(ctx)
    fixed = run_fixes(SCORE_FIXES, FixContext([xml], ctx.config, ctx.log, xml.name)).get(fix.name, 0)
    if not fixed:
        return StageResult("normalize", "ok", "no irregular measures")
    return StageResult("normalize", "ok", f"{fixed} measure(s) normalized to their time signature")
