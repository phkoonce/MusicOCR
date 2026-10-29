#!/usr/bin/env python3
"""Split the GB horn books into one PDF per piece: .venv/bin/python scripts/gb_split.py [--pieces 1-3]"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from musicocr.ensemble_book import build_piece_pdfs, consensus, find_pieces, toc_entries

DRIVE = Path("~/Library/CloudStorage/GoogleDrive-philip.koonce@gmail.com/My Drive/GB").expanduser()
OUT = Path(__file__).resolve().parent.parent / "input" / "gb"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--toc-pages", default="5-11")
    ap.add_argument("--first-page", type=int, default=12)
    ap.add_argument("--horns", default="1,2,3,4")
    args = ap.parse_args()
    a, b = map(int, args.toc_pages.split("-"))
    horns = [int(x) for x in args.horns.split(",")]
    pdfs = {h: DRIVE / f"GB H{h} - unlocked.pdf" for h in horns}
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        tables = [toc_entries(p, range(a, b + 1), tmp) for p in pdfs.values()]
        for h, tab in zip(horns, tables):
            print(f"H{h}: {len(tab)} TOC entries")
        entries = consensus(tables)
        print(f"{len(entries)} pieces in the consensus table")
        table = {"entries": [asdict(e) for e in entries], "found": {}, "pdfs": {}}
        for h, pdf in pdfs.items():
            import subprocess
            n = int(next(l.split()[1] for l in subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout.splitlines() if l.startswith("Pages")))
            found = find_pieces(pdf, entries, args.first_page, n, tmp, log=lambda s, h=h: print(f"H{h}{s}"))
            print(f"H{h}: found {len(found)}/{len(entries)}")
            table["found"][str(h)] = [asdict(f) for f in found]
            pieces = build_piece_pdfs(pdf, found, n, OUT / f"h{h}", f"GB_H{h}", tmp)
            table["pdfs"][str(h)] = {str(k): str(v) for k, v in pieces.items()}
        (OUT / "pieces.json").parent.mkdir(parents=True, exist_ok=True)
        (OUT / "pieces.json").write_text(json.dumps(table, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
