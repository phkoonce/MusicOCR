#!/usr/bin/env python
"""Which cached pages would the count-digit reader change? (no OMR, nothing written)

    .venv/bin/python scripts/countdigits_offline_check.py output/szeged-alpen [> list.txt]

For every part dir of the output, builds the part's digit bank (shipped templates + that part's OCR-read counts) and runs
`countdigits.read_page` on every page's cached detections / texts / image. Prints `<part>/pages/<page> <n>: <count>(<ocr>) ...`
for the pages where it would add a missing count or correct a confident misread: the page-parts to re-run (delete the page's
`pNNN.musicxml`, keep its detections, run the batch for the part with `--redo`), then the number of pages / changes.
"""
from __future__ import annotations

import json
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from musicocr.text import countdigits as cd  # noqa: E402
from musicocr.text.circles import find_circles  # noqa: E402


def check_part(pages: str):
    pages = Path(pages)
    bank = cd.default_bank(pages)
    out = []
    for pdir in sorted(pages.glob("p*")):
        stem = pdir.name
        dets = next(iter(pdir.glob(f"{stem}.dets_*.json")), None)
        png = pdir / f"{stem}.png"
        if not (dets and png.exists()):
            continue
        d = json.loads(dets.read_text())
        tj = pdir / f"{stem}.texts.json"
        t = [x for x in (json.loads(tj.read_text()) if tj.exists() else []) if x.get("source") != "countdigits"]
        gray = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        found = cd.read_page(bank, gray, d, t)
        if not found:
            continue
        sp = cd._staff_space(d)
        rings = [c.box for c in find_circles(gray, sp or 36.0)]
        found = cd.read_page(bank, gray, d, t, rings=rings)
        if found:
            part = pages.parent.parent.name if pages.parent.name == "copisteria" else pages.parent.name
            out.append(f"{part}/pages/{stem} {len(found)}: "
                       + " ".join(f"{f['n']}({f['old'] if f['old'] is not None else '-'})" for f in found))
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1])
    parts = sorted(root.glob("*/pages")) + sorted(root.glob("*/engines/copisteria/pages"))
    with Pool(6) as pool:
        res = sorted(r for rr in pool.imap_unordered(check_part, [str(p) for p in parts]) for r in rr)
    print(f"{len(res)} page-parts of {len(parts)} parts")
    print("\n".join(res))
