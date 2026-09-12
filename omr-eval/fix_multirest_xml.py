"""Patch homr's multi-measure rests to carry a real duration.

homr (as of 0.7.0 / current `main`) has a bug in its rhythm-duration parser:
`kern_to_symbol_duration()` builds a `SymbolDuration` for a multirest token
(e.g. "8m") and never returns it, so it falls through to the generic parser
and the measure ends up with a `<measure-style><multiple-rest>N</multiple-rest>`
tag but NO note/rest content at all -- no encoded duration for that measure.
Filed upstream as https://github.com/liebharc/homr/issues/140 (open, no fix
yet as of 2026-09).

Rather than patch homr's duration engine (whose type/duration derivation for
this branch is clearly unfinished even once the missing `return` is added),
this operates on the exported MusicXML directly: for any <measure> that has
a <multiple-rest> style tag and no note children, it injects a single
whole-measure rest whose <duration> is computed from the divisions and time
signature already declared earlier in the same part -- both of which persist
across measures per the MusicXML spec, so this needs no knowledge of homr's
internals.

This does NOT fix a mis-detected multi-rest count (e.g. a "3" misread as "6")
or a multi-rest silently dropped and written as an ordinary 1-measure rest --
those are transformer misclassifications, already wrong before this file is
generated. See check_multirest.py for a validation pass aimed at catching
those instead.

Usage:
    python3 omr-eval/fix_multirest_xml.py PAGE.musicxml [PAGE2.musicxml ...]

Rewrites each file in place by default; pass --suffix to write PAGE.fixed.musicxml
instead. Only handles single-staff parts (this eval track is scoped to
monophonic brass/woodwind parts) -- prints a warning and skips any part with
more than one <staff> value rather than guessing.
"""
from __future__ import annotations

import argparse
import sys
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET


def part_staff_numbers(part: ET.Element) -> set[str]:
    return {staff.text for staff in part.findall(".//note/staff") if staff.text}


def fix_part(part: ET.Element, path: str) -> int:
    staves = part_staff_numbers(part)
    if len(staves) > 1:
        print(
            f"  WARNING part {part.get('id')}: {len(staves)} staves "
            f"({sorted(staves)}) -- multi-staff parts aren't handled, skipping",
            file=sys.stderr,
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
            print(
                f"  WARNING measure {measure.get('number')}: multi-rest but no "
                "divisions/time signature seen yet -- can't compute duration, skipping",
                file=sys.stderr,
            )
            continue

        duration = Fraction(divisions) * beats * 4 / beat_type
        if duration.denominator != 1:
            print(
                f"  WARNING measure {measure.get('number')}: computed duration "
                f"{duration} isn't a whole number of divisions -- skipping",
                file=sys.stderr,
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
        print(
            f"  measure {measure.get('number')}: injected whole-measure rest "
            f"(multiple-rest={multi_rest.text}, duration={int(duration)})"
        )

    return fixed


def fix_file(path: Path, suffix: bool) -> None:
    print(f"{path}")
    tree = ET.parse(path)
    root = tree.getroot()
    total_fixed = 0
    for part in root.findall("part"):
        total_fixed += fix_part(part, str(path))

    if total_fixed == 0:
        print("  nothing to fix")
        return

    ET.indent(tree, space="  ")
    out_path = path.with_suffix(".fixed.musicxml") if suffix else path
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    print(f"  wrote {out_path} ({total_fixed} measure(s) fixed)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--suffix",
        action="store_true",
        help="write to PAGE.fixed.musicxml instead of overwriting the input",
    )
    args = parser.parse_args()

    for path in args.files:
        fix_file(path, args.suffix)


if __name__ == "__main__":
    main()
