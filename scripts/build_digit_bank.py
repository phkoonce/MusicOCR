#!/usr/bin/env python
"""Build the shipped count-digit template bank (musicocr/text/data/countdigits_bank.npz).

    .venv/bin/python scripts/build_digit_bank.py output/szeged-alpen output/ghs-opera ... [--per-class 40]

Harvests the bold count digits that OCR read confidently over detected multi-rests (`countdigits.harvest_dir`) from every
part's pages in the given output dirs, drops templates the other classes outvote (OCR misreads), and keeps up to
``--per-class`` varied templates per class (farthest-point sampling) so the file stays small.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from musicocr.text import countdigits as cd  # noqa: E402


def thin(templates: list[np.ndarray], k: int) -> list[np.ndarray]:
    if len(templates) <= k:
        return templates
    chosen = [0]
    best = np.array([float((templates[0] * t).sum()) for t in templates])
    while len(chosen) < k:
        i = int(np.argmin(best))
        chosen.append(i)
        best = np.maximum(best, np.array([float((templates[i] * t).sum()) for t in templates]))
    return [templates[i] for i in chosen]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("outputs", nargs="+")
    ap.add_argument("--per-class", type=int, default=40)
    ap.add_argument("--out", default=str(cd.BANK_FILE))
    a = ap.parse_args()
    bank = cd.DigitBank()
    for o in a.outputs:
        for pages in sorted(Path(o).glob("*/pages")) + sorted(Path(o).glob("*/engines/copisteria/pages")):
            cd.harvest_dir(bank, pages)
    print("harvested", bank.n_book())
    print("dropped as mislabelled:", bank.drop_contradicted())
    for k in range(10):
        bank.book[k] = thin(bank.book[k], a.per_class)
    print("kept", bank.n_book())
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    bank.save(Path(a.out))
    print("wrote", a.out, Path(a.out).stat().st_size, "bytes")
