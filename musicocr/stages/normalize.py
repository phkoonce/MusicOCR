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

from musicocr.pipeline import PipelineContext, StageError, StageResult


def run(ctx: PipelineContext) -> StageResult:
    if not ctx.config.normalize_enabled:
        return StageResult("normalize", "skipped", "disabled ([stages.normalize] enabled = false)")

    xml = ctx.artifacts.get("musicxml") or (ctx.workdir / f"{ctx.book_name}.musicxml")
    if not xml.exists():
        raise StageError(
            f"no .musicxml at {xml.name}; run the extract stage first (e.g. --from extract)"
        )
    ctx.artifacts.setdefault("musicxml", xml)

    try:
        from music21 import converter
    except Exception as exc:  # noqa: BLE001
        raise StageError(
            f"music21 not importable ({exc}). Activate the venv / run scripts/setup.sh."
        ) from exc

    from musicocr.measure_normalize import normalize_score

    try:
        score = converter.parse(str(xml))
    except Exception as exc:  # noqa: BLE001
        raise StageError(
            f"music21 could not parse {xml.name} ({type(exc).__name__}: {exc})."
        ) from exc

    fixed = normalize_score(score, log=ctx.log)
    if not fixed:
        return StageResult("normalize", "ok", "no irregular measures")

    score.write("musicxml", fp=str(xml))
    return StageResult("normalize", "ok", f"{fixed} measure(s) normalized to their time signature")
