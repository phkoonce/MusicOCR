#!/usr/bin/env python3
"""Align per-part homr output to the printed bar numbers and combine into one score.

Automatic variant: the bar structure is derived from the part PDFs' text layer
(system-start bar numbers, multi-rest counts) and homr's per-system measure
counts; repairs are applied and listed in <out>.report.txt.  (See
align_combine.py for the variant driven by hand-written multi-rest lists.)

The spec is a local, git-ignored Python file (omr-eval/specs/, e.g.
zerberus.py).  It must define:

  TITLE, COMPOSER, TOTAL_BARS, meter(bar), key_fifths(bar)
  PARTS   {part_id: dict(name, abbr, key, program, clef, pdf, dir)}
          clef is "treble" or "bass"; pdf = part PDF; dir = homr per-page output

and optionally FIRST_BAR_BEATS, MARKS, PAGE_XML, IGNORE_NUMBERS.  The full
interface is documented in musicocr/alignment/systems.py and
musicocr/alignment/spec.py.  Requires poppler (pdftotext, pdfinfo).

Example:
    .venv/bin/python omr-eval/align_systems.py omr-eval/specs/zerberus.py out.musicxml
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from musicocr.assemble.alignment import AlignmentError
from musicocr.assemble.alignment.build import write_score
from musicocr.assemble.alignment.spec import load_spec
from musicocr.assemble.alignment.systems import combine


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path, help="spec module (.py)")
    ap.add_argument("out", type=Path, help="combined MusicXML to write (report goes next to it)")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        spec = load_spec(args.spec)
        score, report = combine(spec)
    except AlignmentError as e:
        raise SystemExit(str(e))
    write_score(score, args.out)
    rp = args.out.with_suffix(".report.txt")
    rp.write_text("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\nwrote {args.out} ({len(spec.PARTS)} parts x {spec.TOTAL_BARS} bars); report {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
