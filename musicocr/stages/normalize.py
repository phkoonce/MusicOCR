"""Stage 5 (between extract and validate): force each measure's duration to
match its time signature.

Mitigates the dominant homr rhythm-misread pattern documented in
omr-eval/README.md (a note read a step too long, e.g. "6.0 beats, expected
4.0") which otherwise leaves MuseScore's barlines out of alignment with
neighboring measures -- annoying for hand-correction. See
``musicocr.measure_normalize`` for the mechanics and its trade-offs (lossy:
trades note accuracy, already wrong, for a uniform grid to correct against).

``[stages.normalize] enabled`` (default true) controls this stage; it's a
mechanical, engine-agnostic pass (only measures that already disagree with
their time signature are touched), so it's safe to leave on for Audiveris
output too.
"""
from __future__ import annotations

from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext
from musicocr.stages._common import disabled, load_score, require_musicxml


def run(ctx: PipelineContext) -> StageResult:
    if not ctx.config.stages.normalize_enabled:
        return disabled("normalize", "normalize")

    xml = require_musicxml(ctx)
    score = load_score(xml)

    from musicocr.measure_normalize import normalize_score

    fixed = normalize_score(score, log=ctx.log)
    if not fixed:
        return StageResult("normalize", "ok", "no irregular measures")

    score.write("musicxml", fp=str(xml))
    return StageResult("normalize", "ok", f"{fixed} measure(s) normalized to their time signature")
