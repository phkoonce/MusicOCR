#!/usr/bin/env python
"""Which cached pages would the current multi-rest rules change? (no OMR, nothing written)

    .venv/bin/python scripts/multirest_offline_check.py output/paxman-all [> list.txt]

Runs `add_missing_multirest_dets` on *copies* of every page's cached detections + texts and prints
`<part>/pages/<page> <n additions>` for the pages where it adds or re-labels a multi-rest: the page-parts to re-run
(delete the page's `pNNN.musicxml`, keep its detections, run the batch for the part with `--redo`). Circled
rehearsal numbers are looked up only for pages that would change (the ring search is slow).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from musicocr.text.circles import find_circles  # noqa: E402
from musicocr.text.page_text import _staff_space, add_missing_multirest_dets  # noqa: E402


def check(texts: str):
    texts = Path(texts)
    stem, d = texts.name[:-len(".texts.json")], texts.parent
    dets = next(iter(d.glob(f"{stem}.dets_*.json")), None)
    if not dets:
        return None
    tmp = Path(tempfile.mkdtemp())
    try:
        t, tt = tmp / dets.name, tmp / texts.name
        shutil.copy(dets, t)
        shutil.copy(texts, tt)
        if add_missing_multirest_dets(t, tt) == 0:
            return None
        sp = _staff_space(json.loads(dets.read_text()))
        with Image.open(d / f"{stem}.png") as im:
            rings = [c.box for c in find_circles(im.convert("L"), sp or 36.0)]
        shutil.copy(dets, t)
        n = add_missing_multirest_dets(t, tt, rings=rings)
        return f"{d.parent.parent.name}/pages/{stem} {n}" if n else None
    except Exception as exc:  # noqa: BLE001
        return f"ERR {d} {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    files = sorted(str(f) for f in out.glob("*/pages/p*/p*.texts.json"))
    with Pool(6) as pool:
        res = sorted(r for r in pool.imap_unordered(check, files, 4) if r)
    print(f"{len(res)} of {len(files)} pages")
    print("\n".join(res))
