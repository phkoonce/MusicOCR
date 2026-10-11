#!/usr/bin/env python3
"""Combine single-part homr MusicXMLs into one multi-part score, aligned to printed bars.

Hand-specified variant: the spec lists, per part, which printed bar ranges are
multi-rests and which bars are single-bar rests; homr's k-th measure is mapped
to the k-th printed unit.  (See align_systems.py for the automatic variant that
reads the structure from the part PDFs.)

The spec is a local, git-ignored Python file (omr-eval/specs/, e.g.
last_airbender.py).  It must define:

  TITLE, COMPOSER, TOTAL_BARS, meter(bar) -> (beats, beat_type)
  PARTS          {part_id: (display_name, path_to_homr_musicxml)}
  MULTIREST(p)   [(first_bar, last_bar), ...] printed as multi-rests
  REST_BARS(p)   set of bars printed as single-bar rests

and optionally: fix_bar(part, bar, homr_measure, spec, ctx), MARKS, CLEF,
TRANSPOSE, PART_ABBR, MIDI_PROGRAM.  The full interface is documented in
musicocr/alignment/units.py and musicocr/alignment/spec.py.

Example:
    .venv/bin/python omr-eval/align_combine.py omr-eval/specs/last_airbender.py out.musicxml
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from musicocr.alignment import AlignmentError
from musicocr.alignment.build import write_score
from musicocr.alignment.spec import load_spec
from musicocr.alignment.units import combine


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=__doc__.split("\n\n", 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path, help="spec module (.py)")
    ap.add_argument("out", type=Path, help="combined MusicXML to write")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        spec = load_spec(args.spec)
        score, report = combine(spec)
    except AlignmentError as e:
        raise SystemExit(str(e))
    write_score(score, args.out)
    print("\n".join(report))
    print(f"\nwrote {args.out} ({len(spec.PARTS)} parts x {spec.TOTAL_BARS} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
