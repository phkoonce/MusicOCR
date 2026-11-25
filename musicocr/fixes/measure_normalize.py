"""Force each measure's actual duration to match its time signature.

homr's rhythm misreads (see musicocr/stages/validate.py's measure-duration
check) leave some measures' note content summing to something other than what
the time signature says -- e.g. a half note misread as a whole note gives
"6.0 beats, expected 4.0". MusicXML has no field for "this measure is
irregular"; music21/MuseScore just accept whatever duration the notes add up
to, which MuseScore then treats as that measure's own irregular "actual
duration", distinct from the nominal one. On import that means the barlines
don't line up with neighboring measures under the same time signature, which
makes hand-correction in MuseScore (dragging notes between measures, working
a passage on the same beat grid) much more annoying than editing a
wrong-but-regularly-shaped measure.

This is a lossy fix -- the note that's actually wrong can't be identified
without the scan, so it just makes the measure conform mechanically:

  - overlong measure: notes/rests are truncated at the barline (an element
    straddling it is shortened to fit); anything entirely past the barline is
    dropped.
  - underlong measure (not the piece's pickup or its true final measure --
    both are left untouched, the same tolerance musicocr's validate stage
    already applies): padded with a rest for the remaining duration.

Content is not preserved across the truncation point -- this trades note
accuracy (already wrong) for a uniform measure grid to correct against. Only
handles measures music21 can read as plain notes/rests/chords; multi-voice
measures are normalized per voice.

Run this on a *merged* per-part score (after the extract stage's page
concatenation), not on individual page files -- on a single page, its
first/last measure would wrongly get the pickup/final-measure tolerance meant
for the piece's actual first/last measure, and any page-boundary anacrusis
would be normalized away.

Background, and the music21 stale-length gotcha this works around: see
omr-eval/README.md's 2026-09-13 entry.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from musicocr.musicxml.io import open_wedges

_TOL = 1e-6


def _rebuild(container, expected: float, actual: float):
    """Return a fresh container (same class) truncated/padded to `expected`.

    A Measure/Voice parsed from MusicXML apparently caches its highestTime
    (and hence its written-out span) from the source file: removing or
    resizing its notes in place doesn't change what gets written, and
    music21's writer silently re-pads the gap with an invisible rest to match
    the stale cached length. Building a brand-new container with only the
    elements we want avoids that stale state entirely.
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


def normalize_part(part, label: str, log: Callable[[str], None] = print, known_full=frozenset(),
                   incomplete=frozenset()) -> int:
    """Normalize every irregular measure in `part` in place. Returns count fixed.

    ``incomplete``: indices of bars that are short as printed (every part of the score, at a section edge).
    ``known_full``: indices of bars another part of the same score holds at full length; there a short first or
    last bar is no pickup or incomplete ending but a dropped rest."""
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
        if i in incomplete and actual < expected:
            continue
        if i == 0 and actual < expected and i not in known_full:
            continue  # legitimate pickup measure
        if i == last and actual < expected and i not in known_full:
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
        # part.replace() swaps the object at the same offset but doesn't shift
        # anything after it -- every later measure is still sitting at the
        # offset implied by the *old* (unfixed) lengths. Re-lay the part out
        # end-to-end using each measure's now-correct bar length.
        for m in measures:
            part.remove(m)
        offset = 0
        for m, length in zip(measures, bar_lengths):
            part.insert(offset, m)
            offset += length
    return fixed


def normalize_file(path, log: Callable[[str], None] = print) -> int:
    """Normalize the score in MusicXML file `path` in place (rewritten only if something changed)."""
    from music21 import converter

    score = converter.parse(str(path))
    fixed = normalize_score(score, log)
    if fixed:
        score.write("musicxml", fp=str(path))
        open_wedges(Path(path))
    return fixed


def normalize_score(score, log: Callable[[str], None] = print) -> int:
    """Normalize every part of `score` (a music21 Score) in place. Returns count fixed."""
    from music21 import stream

    parts = list(score.getElementsByClass(stream.Part)) or [score]
    total = 0
    full = _full_bars(parts) if len(parts) > 1 else frozenset()
    short = _incomplete_bars(parts) if len(parts) > 1 else frozenset()
    for i, part in enumerate(parts):
        label = part.partName or f"part {i + 1}"
        total += normalize_part(part, label, log, known_full=full, incomplete=short)
    return total


def _full_bars(parts) -> frozenset:
    """Indices of bars that some part of a score fills with notes and written rests to the bar's length (parts of
    one score share their bar lengths)."""
    from music21 import stream

    out = set()
    for part in parts:
        for i, m in enumerate(part.getElementsByClass(stream.Measure)):
            try:
                if m.duration.quarterLength >= m.barDuration.quarterLength - _TOL:
                    out.add(i)
            except Exception:  # noqa: BLE001
                continue
    return frozenset(out)


def _incomplete_bars(parts) -> frozenset:
    """Indices of bars every part leaves equally short next to a repeat or a double / final barline: a second
    ending or a section that completes an upbeat."""
    from music21 import bar, stream

    def edge(m) -> bool:
        return any(isinstance(b, bar.Repeat) or (b is not None and b.type in ("final", "double", "light-heavy",
                   "light-light")) for b in (m.leftBarline, m.rightBarline))

    columns = [list(p.getElementsByClass(stream.Measure)) for p in parts]
    out = set()
    for i in range(min(len(c) for c in columns)):
        try:
            lengths = {c[i].duration.quarterLength for c in columns}
            if len(lengths) != 1 or lengths.pop() >= columns[0][i].barDuration.quarterLength - _TOL:
                continue
        except Exception:  # noqa: BLE001
            continue
        if any(edge(m) for c in columns for m in c[max(0, i - 1):i + 1]):
            out.add(i)
    return frozenset(out)
