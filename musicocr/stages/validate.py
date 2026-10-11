"""Stage 6: sanity-check the MusicXML with music21. Reports only, no fixes.

Findings are stashed on ``ctx.artifacts.validation`` for the final report.
Severities: ``error`` (almost certainly wrong), ``warn`` (suspicious / worth a
look), ``info`` (context).
"""
from __future__ import annotations

from musicocr.engines import has_check
from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext
from musicocr.stages._common import load_score, require_musicxml

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


def suspicious_tuplet_measures(part) -> list[tuple[int, str]]:
    """Measures whose tuplets look like a misread rather than real notation: more
    than one tuplet ratio in one bar, or a note shorter than a 32nd inside a tuplet.
    Engines (notably Copisteria, which infers tuplets from spacing) can invent
    these while still summing to the right bar length, so the duration check
    misses them."""
    out: list[tuple[int, str]] = []
    for m in part.getElementsByClass("Measure"):
        ratios, tiny = set(), False
        for n in m.recurse().notesAndRests:
            for t in n.duration.tuplets:
                ratios.add((t.numberNotesActual, t.numberNotesNormal))
                if n.duration.quarterLength < 0.125:
                    tiny = True
        if len(ratios) > 1:
            out.append((m.number, "mixed tuplet ratios "
                        + ", ".join(f"{a}:{b}" for a, b in sorted(ratios)) + " in one bar"))
        elif tiny:
            out.append((m.number, "tuplet containing a note shorter than a 32nd"))
    return out


def run(ctx: PipelineContext) -> StageResult:
    xml = require_musicxml(ctx)
    score = load_score(
        xml, parse_hint="The OMR output is likely malformed; inspect the per-page files.")
    from music21 import stream
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
    for m_no, why in suspicious_tuplet_measures(parts[0] if parts else score):
        findings.append(_finding("warn", "odd-tuplets", f"m.{m_no}: {why}", measure=m_no))
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

    if has_check(ctx.config.omr.engine, "dropped_multirest"):
        # homr-specific: a dropped multi-rest (the model reads a printed "3" as
        # a plain bar) has no structural signature of its own -- see
        # musicocr.homr_fixes and omr-eval/README.md. Weak heuristic, expect
        # false positives from legitimate single-bar rests.
        from musicocr.homr_fixes import suspicious_whole_rests
        try:
            for note in suspicious_whole_rests(xml):
                findings.append(_finding("info", "untagged-whole-rest", note))
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  suspicious_whole_rests check failed: {type(exc).__name__}: {exc}")

    counts = {s: sum(1 for f in findings if f["severity"] == s)
              for s in ("error", "warn", "info")}
    ctx.artifacts.validation = {"findings": findings, "stats": stats, "counts": counts}

    detail = f"{counts['error']} error, {counts['warn']} warn, {counts['info']} info"
    status = "ok"
    return StageResult("validate", status, detail, data=stats)
