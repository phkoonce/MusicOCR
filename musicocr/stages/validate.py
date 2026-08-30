"""Stage 5: sanity-check the MusicXML with music21. Reports only, no fixes.

Findings are stashed on ``ctx.artifacts['validation']`` for the final report.
Severities: ``error`` (almost certainly wrong), ``warn`` (suspicious / worth a
look), ``info`` (context).
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageError, StageResult

_TOL = 1e-6


def _finding(sev, code, msg, **loc):
    return {"severity": sev, "code": code, "message": msg, **loc}


def _check_part(part, p_index, findings):
    from music21 import clef, key, meter, stream

    measures = list(part.getElementsByClass(stream.Measure))
    pname = part.partName or f"part {p_index + 1}"

    if not measures:
        findings.append(_finding("warn", "empty-part", f"{pname}: no measures", part=pname))
        return 0

    flat = part.flatten()
    if not flat.getElementsByClass(clef.Clef):
        findings.append(_finding("warn", "no-clef", f"{pname}: no clef found", part=pname))
    if not flat.getElementsByClass(meter.TimeSignature):
        findings.append(_finding("warn", "no-time", f"{pname}: no time signature", part=pname))
    # A missing key signature only matters if the part actually uses accidentals
    # (an explicit "no sharps/flats" key is often just omitted by the OMR).
    if not flat.getElementsByClass(key.KeySignature):
        acc = sum(1 for n in flat.notes for p in getattr(n, "pitches", [])
                  if p.accidental is not None)
        if acc:
            findings.append(_finding(
                "warn", "no-key",
                f"{pname}: no key signature but {acc} accidental(s) — likely misread key",
                part=pname))

    last = len(measures) - 1
    for i, m in enumerate(measures):
        try:
            expected = m.barDuration.quarterLength
            actual = m.duration.quarterLength
        except Exception:  # noqa: BLE001
            continue
        if abs(actual - expected) <= _TOL:
            continue
        # tolerate a pickup at the very start and an incomplete final measure
        if i == 0 and actual < expected:
            continue
        if i == last and actual < expected:
            findings.append(_finding(
                "info", "short-final",
                f"{pname} m.{m.number}: final measure {actual}/{expected} beats (often fine)",
                part=pname, measure=m.number))
            continue
        sev = "error" if actual > expected else "warn"
        findings.append(_finding(
            sev, "measure-duration",
            f"{pname} m.{m.number}: {actual} beats, expected {expected}",
            part=pname, measure=m.number))
    return len(measures)


def run(ctx: PipelineContext) -> StageResult:
    xml = ctx.artifacts.get("musicxml") or (ctx.workdir / f"{ctx.book_name}.musicxml")
    if not xml.exists():
        raise StageError(
            f"no .musicxml at {xml.name}; run the extract stage first "
            "(e.g. --from extract)"
        )
    ctx.artifacts.setdefault("musicxml", xml)

    try:
        from music21 import converter, stream
    except Exception as exc:  # noqa: BLE001
        raise StageError(
            f"music21 not importable ({exc}). Activate the venv / run scripts/setup.sh."
        ) from exc

    try:
        score = converter.parse(str(xml))
    except Exception as exc:  # noqa: BLE001
        raise StageError(
            f"music21 could not parse {xml.name} ({type(exc).__name__}: {exc}). "
            "The OMR output is likely malformed; inspect the per-page files."
        ) from exc
    parts = list(score.getElementsByClass(stream.Part)) or [score]

    findings: list[dict] = []
    measure_counts = [_check_part(p, i, findings) for i, p in enumerate(parts)]

    if len(set(measure_counts)) > 1:
        findings.append(_finding(
            "warn", "measure-count-mismatch",
            f"parts have differing measure counts: {measure_counts}"))

    flat = score.flatten()
    notes = len(flat.notes)
    rests = len(flat.getElementsByClass("Rest"))
    tuplets = sum(1 for n in flat.notesAndRests if n.duration.tuplets)
    accidentals = sum(
        1 for n in flat.notes
        for p in getattr(n, "pitches", [])
        if p.accidental is not None
    )
    stats = {
        "parts": len(parts),
        "measures_per_part": measure_counts,
        "notes": notes,
        "rests": rests,
        "tuplets": tuplets,
        "accidentals": accidentals,
    }
    if notes and tuplets / max(notes, 1) > 0.25:
        findings.append(_finding(
            "warn", "many-tuplets",
            f"{tuplets}/{notes} notes are tuplets — check for rhythm misreads"))
    if notes and accidentals / max(notes, 1) > 0.4:
        findings.append(_finding(
            "warn", "many-accidentals",
            f"{accidentals}/{notes} notes carry accidentals — check key signature"))
    if notes == 0:
        findings.append(_finding("error", "no-notes", "no notes recognised in the score"))

    counts = {s: sum(1 for f in findings if f["severity"] == s)
              for s in ("error", "warn", "info")}
    ctx.artifacts["validation"] = {"findings": findings, "stats": stats, "counts": counts}

    detail = f"{counts['error']} error, {counts['warn']} warn, {counts['info']} info"
    status = "ok"
    return StageResult("validate", status, detail, data=stats)
