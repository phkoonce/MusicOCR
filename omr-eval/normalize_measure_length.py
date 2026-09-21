"""CLI wrapper around ``musicocr.measure_normalize.normalize_score``.

The fix itself now also runs automatically inside the main pipeline as the
``normalize`` stage (``[stages.normalize] enabled``, default on) -- see
``musicocr/stages/normalize.py``. Kept here as a standalone entry point for
ad-hoc eval work (e.g. re-normalizing a concat_pages.py output outside a
pipeline run). See musicocr/measure_normalize.py for the full explanation and
omr-eval/README.md's 2026-09-13 entry for the writeup.

Run this on a *merged* per-part file (after concat_pages.py), not on
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


def normalize_file(path: Path, suffix: bool, log) -> None:
    from music21 import converter

    from musicocr.measure_normalize import normalize_score

    log(f"{path}")
    score = converter.parse(str(path))
    fixed = normalize_score(score, log)

    if fixed == 0:
        log("  nothing to normalize")
        return

    out_path = path.with_suffix(".normalized.musicxml") if suffix else path
    score.write("musicxml", fp=str(out_path))
    log(f"  wrote {out_path} ({fixed} measure(s) normalized)")


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
