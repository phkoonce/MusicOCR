"""Post-processing fixes for homr's known MusicXML generator quirks.

homr (https://github.com/liebharc/homr, as of 0.7.0 / current ``main``) has a
bug in its rhythm-duration parser: ``kern_to_symbol_duration()`` builds a
``SymbolDuration`` for a multirest token (e.g. ``"8m"``) and never returns it,
so it falls through to the generic parser and the exported measure ends up
with a ``<measure-style><multiple-rest>N</multiple-rest>`` tag but NO
note/rest content at all -- no encoded duration for that measure. Filed
upstream as liebharc/homr#140 (open, no fix as of 2026-09).

Rather than patch homr's duration engine (whose type/duration derivation for
this branch is clearly unfinished even once the missing ``return`` is added),
``fix_multirest_file`` operates on the exported MusicXML directly: for any
``<measure>`` that has a ``<multiple-rest>`` style tag and no note children,
it injects a single whole-measure rest whose ``<duration>`` is computed from
the divisions and time signature already declared earlier in the same part --
both of which persist across measures per the MusicXML spec, so this needs no
knowledge of homr's internals.

This does NOT fix a mis-detected multi-rest count (e.g. a "3" misread as "6")
or a multi-rest silently dropped and written as an ordinary 1-measure rest --
those are transformer misclassifications, already wrong before this file is
generated. ``suspicious_whole_rests`` flags the latter as a weak,
false-positive-prone heuristic (see musicocr/stages/validate.py).

Background and full failure-mode writeup: omr-eval/README.md.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET


def _part_staff_numbers(part: ET.Element) -> set[str]:
    return {staff.text for staff in part.findall(".//note/staff") if staff.text}


def fix_multirest_part(part: ET.Element, log: Callable[[str], None] = print) -> int:
    """Inject a whole-measure rest into any homr-empty multirest measure. Returns count fixed."""
    staves = _part_staff_numbers(part)
    if len(staves) > 1:
        log(
            f"  WARNING part {part.get('id')}: {len(staves)} staves "
            f"({sorted(staves)}) -- multi-staff parts aren't handled, skipping"
        )
        return 0

    divisions: int | None = None
    beats: int | None = None
    beat_type: int | None = None
    fixed = 0

    for measure in part.findall("measure"):
        for div in measure.findall("./attributes/divisions"):
            if div.text:
                divisions = int(div.text)
        for time in measure.findall("./attributes/time"):
            b, bt = time.find("beats"), time.find("beat-type")
            if b is not None and b.text:
                beats = int(b.text)
            if bt is not None and bt.text:
                beat_type = int(bt.text)

        multi_rest = measure.find("./attributes/measure-style/multiple-rest")
        if multi_rest is None:
            continue
        if measure.find("note") is not None:
            continue  # already has content -- nothing to fix

        if divisions is None or beats is None or beat_type is None:
            log(
                f"  WARNING measure {measure.get('number')}: multi-rest but no "
                "divisions/time signature seen yet -- can't compute duration, skipping"
            )
            continue

        duration = Fraction(divisions) * beats * 4 / beat_type
        if duration.denominator != 1:
            log(
                f"  WARNING measure {measure.get('number')}: computed duration "
                f"{duration} isn't a whole number of divisions -- skipping"
            )
            continue

        note = ET.Element("note")
        ET.SubElement(note, "rest", {"measure": "yes"})
        ET.SubElement(note, "duration").text = str(int(duration))
        ET.SubElement(note, "voice").text = "1"
        if staves:
            ET.SubElement(note, "staff").text = next(iter(staves))
        measure.append(note)
        fixed += 1
        log(
            f"  measure {measure.get('number')}: injected whole-measure rest "
            f"(multiple-rest={multi_rest.text}, duration={int(duration)})"
        )

    return fixed


def fix_multirest_file(path: Path, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place, fixing any empty multirest measures. Returns count fixed."""
    tree = ET.parse(path)
    root = tree.getroot()
    total = sum(fix_multirest_part(part, log) for part in root.findall("part"))
    if total:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return total


def suspicious_whole_rests(path: Path) -> list[str]:
    """Plain whole-measure rests *not* tagged ``<multiple-rest>`` -- possibly a
    multi-rest homr dropped entirely rather than mis-tagged (see module
    docstring). Weak, single-file heuristic: a lone whole-measure rest is
    completely normal notation, so expect false positives.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    notes: list[str] = []

    for part in root.findall("part"):
        divisions: int | None = None
        beats: int | None = None
        beat_type: int | None = None

        for measure in part.findall("measure"):
            for div in measure.findall("./attributes/divisions"):
                if div.text:
                    divisions = int(div.text)
            for time in measure.findall("./attributes/time"):
                b, bt = time.find("beats"), time.find("beat-type")
                if b is not None and b.text:
                    beats = int(b.text)
                if bt is not None and bt.text:
                    beat_type = int(bt.text)

            multi_rest = measure.find("./attributes/measure-style/multiple-rest")
            note_children = measure.findall("note")
            if multi_rest is not None or len(note_children) != 1:
                continue

            note = note_children[0]
            rest, dur = note.find("rest"), note.find("duration")
            if not (rest is not None and dur is not None and dur.text
                    and divisions and beats and beat_type):
                continue
            full = Fraction(divisions) * beats * 4 / beat_type
            if full.denominator == 1 and int(dur.text) == int(full):
                notes.append(
                    f"measure {measure.get('number')}: plain whole-measure rest, not "
                    "tagged as a multi-rest -- could be a dropped multi-rest, worth a "
                    "glance against the scan"
                )
    return notes
