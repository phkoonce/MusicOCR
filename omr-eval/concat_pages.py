"""Concatenate homr's per-page MusicXML into one file per part.

Reuses the same measure-append logic the main pipeline uses to stitch
Audiveris's per-page output (musicocr.stages.extract._concat): the first
page's score -- metadata, part-list, attributes -- is kept as-is, and each
subsequent page's measures are appended onto the same part(s) and
renumbered 1..N. Only the first page's non-measure data survives, which is
the deduplication this pipeline already relies on.

Usage:
    .venv/bin/python omr-eval/concat_pages.py \
        horn1=omr-eval/pages/bach_horn1_p001.musicxml,omr-eval/pages/bach_horn1_p002.musicxml \
        horn2=omr-eval/pages/bach_horn2_p003.musicxml,omr-eval/pages/bach_horn2_p004.musicxml

Writes omr-eval/pages/<name>.musicxml for each `name=page1,page2,...` arg.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musicocr.stages.extract import _concat  # noqa: E402


def main(argv: list[str]) -> int:
    ok = True
    for arg in argv:
        name, _, pages = arg.partition("=")
        if not pages:
            print(f"skipping malformed arg: {arg!r}")
            ok = False
            continue
        page_files = [Path(p) for p in pages.split(",")]
        dest = page_files[0].parent / f"{name}.musicxml"
        merged, skipped = _concat(page_files, dest, log=print)
        if merged is None:
            print(f"{name}: merge failed")
            ok = False
        elif skipped:
            print(f"{name}: merged with {len(skipped)} page(s) skipped: "
                  f"{[p.name for p in skipped]}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
