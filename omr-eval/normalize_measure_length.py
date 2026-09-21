"""Force each measure's actual duration to match its time signature.

homr's rhythm misreads (see check_measure_duration.py) leave some measures'
note content summing to something other than what the time signature says --
e.g. a half note misread as a whole note gives "6.0 beats, expected 4.0".
MusicXML has no field for this; music21/MuseScore just accept whatever
duration the notes add up to, which MuseScore then treats as that measure's
own irregular/custom "actual duration" distinct from the nominal one. On
import that means the barlines don't line up with neighboring measures under
the same time signature, which makes hand-correction in MuseScore (dragging
notes between measures, using the same beat grid across a passage, etc.)
much more annoying than editing wrong-but-regularly-shaped measures.

This is a lossy fix -- the note that's actually wrong can't be identified
without the scan, so it just makes the measure conform mechanically:

  - overlong measure: notes/rests are truncated at the barline (an element
    straddling it is shortened to fit; music21 re-notates the truncated
    value as tied notes if needed); anything entirely past the barline is
    dropped.
  - underlong measure (not the piece's pickup or its true final measure --
    both stay untouched, same tolerance check_measure_duration.py already
    applies): padded with a rest for the remaining duration.

Content is not preserved across the truncation point -- this trades note
accuracy (already wrong) for a uniform measure grid to correct against. Only
handles measures music21 can read as plain notes/rests/chords; multi-voice
measures are normalized per voice.

Run this on the *merged* per-part file (after concat_pages.py), not on
individual page files -- on a single page, its first/last measure would
wrongly get the pickup/final-measure tolerance meant for the piece's actual
first/last measure, and any page-boundary anacrusis would be normalized away.

Usage:
    .venv/bin/python omr-eval/normalize_measure_length.py PART.musicxml [PART2.musicxml ...]

Rewrites each file in place by default; pass --suffix to write
PART.normalized.musicxml instead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TOL = 1e-6


def _voices_of(measure):
    from music21 import stream

    voices = list(measure.getElementsByClass(stream.Voice))
    return voices if voices else [measure]


def _rebuild(container, expected, actual):
    """Return a fresh container (same class) truncated/padded to `expected`.

    A Measure/Voice parsed from MusicXML apparently caches its highestTime
    (and hence its written-out span) from the source file: removing or
    resizing its notes in place doesn't change what gets written, and
    music21's writer silently re-pads the gap with an invisible rest to
    match the stale cached length. Building a brand-new container with only
    the elements we want avoids that stale state entirely.
    """
    from music21 import note

    new_container = container.__class__()
    if hasattr(container, "number"):
        new_container.number = container.number

    notes = list(container.notesAndRests)
    others = [el for el in container.elements if el not in notes]

    if actual > expected:
        for el in notes:
            start = el.offset
            if start >= expected - _TOL:
                continue  # drop -- entirely past the barline
            if start + el.duration.quarterLength > expected + _TOL:
                el.duration.quarterLength = expected - start
                if getattr(el, "tie", None) is not None:
                    el.tie = None
            new_container.insert(start, el)
        for el in others:
            new_container.insert(min(el.offset, expected), el)
    else:
        for el in notes:
            new_container.insert(el.offset, el)
        for el in others:
            new_container.insert(el.offset, el)
        r = note.Rest()
        r.duration.quarterLength = expected - actual
        new_container.insert(actual, r)
    return new_container


def normalize_part(part, label: str, log) -> int:
    from music21 import stream

    measures = list(part.getElementsByClass(stream.Measure))
    last = len(measures) - 1
    bar_lengths = [m.barDuration.quarterLength for m in measures]
    fixed = 0
    for i, m in enumerate(measures):
        expected = bar_lengths[i]
        try:
            actual = m.duration.quarterLength
        except Exception:  # noqa: BLE001
            continue
        if abs(actual - expected) <= _TOL:
            continue
        if i == 0 and actual < expected:
            continue  # legitimate pickup measure
        if i == last and actual < expected:
            continue  # legitimate incomplete final measure

        voices = list(m.getElementsByClass(stream.Voice))
        if voices:
            new_m = m.__class__()
            new_m.number = m.number
            for el in m.elements:
                if el not in voices:
                    new_m.insert(el.offset, el)
            for voice in voices:
                v_actual = voice.duration.quarterLength
                new_voice = (
                    _rebuild(voice, expected, v_actual)
                    if abs(v_actual - expected) > _TOL
                    else voice
                )
                new_m.insert(0, new_voice)
        else:
            new_m = _rebuild(m, expected, actual)
        measures[i] = new_m
        part.replace(m, new_m)

        fixed += 1
        log(f"  {label} m.{new_m.number}: {actual} -> {expected} beats")

    if fixed:
        # part.replace() swaps the object at the same offset but doesn't
        # shift anything after it -- every later measure is still sitting at
        # the offset implied by the *old* (unfixed) lengths. Re-lay the part
        # out end-to-end using each measure's now-correct bar length.
        for m in measures:
            part.remove(m)
        offset = 0
        for m, length in zip(measures, bar_lengths):
            part.insert(offset, m)
            offset += length
    return fixed


def normalize_file(path: Path, suffix: bool, log) -> None:
    from music21 import converter, stream

    log(f"{path}")
    score = converter.parse(str(path))
    parts = list(score.getElementsByClass(stream.Part)) or [score]

    total_fixed = 0
    for i, part in enumerate(parts):
        label = part.partName or f"part {i + 1}"
        total_fixed += normalize_part(part, label, log)

    if total_fixed == 0:
        log("  nothing to normalize")
        return

    out_path = path.with_suffix(".normalized.musicxml") if suffix else path
    score.write("musicxml", fp=str(out_path))
    log(f"  wrote {out_path} ({total_fixed} measure(s) normalized)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--suffix",
        action="store_true",
        help="write to PART.normalized.musicxml instead of overwriting the input",
    )
    args = parser.parse_args()

    try:
        import music21  # noqa: F401
    except ImportError:
        print(
            "music21 not importable -- run this with the main project's venv:\n"
            "  .venv/bin/python omr-eval/normalize_measure_length.py ...",
            file=sys.stderr,
        )
        sys.exit(2)

    for path in args.files:
        normalize_file(path, args.suffix, print)


if __name__ == "__main__":
    main()
