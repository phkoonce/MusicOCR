"""Flag multi-bar-rest problems in homr MusicXML output that no code fix can catch.

fix_multirest_xml.py repairs measures homr tagged as a multi-rest but left
empty. It can't help with the two failure modes that are already wrong in
homr's own decoded token sequence, before any XML is written:

  * a multi-rest's printed count misread (e.g. a "3" coming out as
    <multiple-rest>6</multiple-rest>)
  * a multi-rest dropped entirely, written as an ordinary 1-measure rest
    with no <multiple-rest> tag at all

Neither has a structural signature reliable enough to catch alone -- a lone
whole-measure rest is completely normal notation. The one ground-truth-free
signal available is that every part of the same multi-part piece must add up
to the same total duration: if horn 1's transcription is missing 2 measures
because a "3"-bar rest silently became a 1-bar rest, its grand total will be
short relative to the other 7 horns'. So this script's main job is comparing
total durations across a piece's parts; it also does a much weaker one-file
heuristic (listing plain whole-measure rests as worth a manual glance) since
that costs nothing extra once the file is already open.

Usage:
    # one part, single page or several pages of the same part concatenated
    python3 omr-eval/check_multirest.py horn1=p001.musicxml,p002.musicxml

    # compare across parts of the same piece once more have been run
    python3 omr-eval/check_multirest.py \\
        horn1=bach_horn1_p001.musicxml,bach_horn1_p002.musicxml \\
        horn2=bach_horn2_p003.musicxml,bach_horn2_p004.musicxml \\
        horn3=bach_horn3_p005.musicxml,bach_horn3_p006.musicxml

Each token is NAME=file1,file2,... (comma-separated pages of one part, in
order) or just a bare file path (the part name is taken from the filename).
Run fix_multirest_xml.py first so a part's total isn't shorted purely by the
known empty-multi-rest-measure bug rather than a genuine misdetection.
"""
from __future__ import annotations

import argparse
import sys
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET


def measure_full_duration(divisions: int, beats: int, beat_type: int) -> Fraction:
    return Fraction(divisions) * beats * 4 / beat_type


def scan_file(path: Path) -> tuple[Fraction, list[str]]:
    """Returns (total duration across all parts, in quarter notes; suspicious-measure notes)."""
    tree = ET.parse(path)
    root = tree.getroot()
    total = Fraction(0)
    notes: list[str] = []

    for part in root.findall("part"):
        divisions: int | None = None
        beats: int | None = None
        beat_type: int | None = None
        part_total_divisions = 0

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

            for note in note_children:
                dur = note.find("duration")
                if dur is not None and dur.text:
                    part_total_divisions += int(dur.text)

            if multi_rest is None and len(note_children) == 1:
                note = note_children[0]
                rest = note.find("rest")
                dur = note.find("duration")
                if (
                    rest is not None
                    and dur is not None
                    and dur.text
                    and divisions is not None
                    and beats is not None
                    and beat_type is not None
                ):
                    full = measure_full_duration(divisions, beats, beat_type)
                    if full.denominator == 1 and int(dur.text) == int(full):
                        notes.append(
                            f"{path.name} measure {measure.get('number')}: "
                            "plain whole-measure rest, not tagged as a multi-rest -- "
                            "worth a glance against the scan (could be a dropped multi-rest)"
                        )

        if divisions:
            total += Fraction(part_total_divisions, divisions)

    return total, notes


def parse_part_arg(arg: str) -> tuple[str, list[Path]]:
    if "=" in arg:
        name, files = arg.split("=", 1)
        return name, [Path(f) for f in files.split(",")]
    path = Path(arg)
    return path.stem, [path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "parts",
        nargs="+",
        help="NAME=file1,file2,... (pages of one part, in order) or a bare file path",
    )
    args = parser.parse_args()

    totals: dict[str, Fraction] = {}
    all_notes: list[str] = []

    for arg in args.parts:
        name, files = parse_part_arg(arg)
        part_total = Fraction(0)
        for f in files:
            file_total, notes = scan_file(f)
            part_total += file_total
            all_notes.extend(notes)
        totals[name] = part_total

    if all_notes:
        print("Plain whole-measure rests not tagged as multi-rests (may be undetected):")
        for note in all_notes:
            print(f"  {note}")
        print()

    print("Per-part total duration (quarter notes):")
    for name, total in totals.items():
        print(f"  {name}: {total}")

    if len(totals) > 1:
        counts: dict[Fraction, int] = {}
        for total in totals.values():
            counts[total] = counts.get(total, 0) + 1
        majority_total = max(counts, key=lambda t: counts[t])
        outliers = {name: total for name, total in totals.items() if total != majority_total}
        print()
        if outliers:
            print(
                f"MISMATCH: {len(outliers)} part(s) disagree with the majority total "
                f"({majority_total} quarter notes) -- likely a misdetected or dropped multi-rest:"
            )
            for name, total in outliers.items():
                diff = total - majority_total
                sign = "+" if diff >= 0 else ""
                print(f"  {name}: {total} (diff {sign}{diff})")
            sys.exit(1)
        else:
            print(f"All {len(totals)} part(s) agree: {majority_total} quarter notes total.")


if __name__ == "__main__":
    main()
