"""Run inside the Copisteria venv: re-detect a page at a larger input size and
union the extra note/rest detections into a copy of the page's cached detections.

    python second_pass_detect.py <page.png> <base_dets.json> <out_dets.json> <scale>

The base detections were read at the size encoded in their file name
(``<stem>.dets_<tag>_<imgsz>.json``). Copisteria's detector misses some notes at
that size (e.g. the first eighth after a dotted quarter) but sees them, at
confidence ~1, one size up -- while detecting bars/multi-rests worse -- so only
note and rest detections that do not already overlap a base detection of the
same family are added. Prints ``{"added": N, "imgsz": S}``.

Standalone on purpose (no musicocr imports): it is executed by the Copisteria
venv's interpreter with cwd at the Copisteria repo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FAMILIES = ("note", "rest")
IOU_SKIP = 0.3


def _iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def union_dets(base: list, big: list) -> list:
    """``base`` plus the note/rest detections of ``big`` not overlapping a same-family one."""
    fam = lambda c: c["cls"].split("_")[0]  # noqa: E731
    extra = [r for r in big if fam(r) in FAMILIES and not any(
        fam(b) == fam(r) and _iou(b["xyxy"], r["xyxy"]) > IOU_SKIP for b in base)]
    return base + extra


def main(png: str, base: str, out: str, scale: str) -> None:
    from PIL import Image

    from copisteria.detect import Detector

    base_path = Path(base)
    imgsz = int(base_path.stem.rsplit("_", 1)[1])
    dets = json.loads(base_path.read_text())
    det = Detector()
    img = Image.open(png)
    merged, sizes = dets, []
    # ``scale`` may be a comma list: the detector's blind spots depend on the input size
    # (a symbol missed at 1600 and 2048 can show at 1824 or 2400+), so union several.
    for sc in str(scale).split(","):
        big_sz = int(round(imgsz * float(sc) / 32.0)) * 32
        if big_sz in sizes:
            continue
        sizes.append(big_sz)
        arr, s = det.prepare(img, big_sz)
        merged = union_dets(merged, det.run_array(arr, s))
    Path(out).write_text(json.dumps(merged))
    print(json.dumps({"added": len(merged) - len(dets), "imgsz": sizes[-1], "sizes": sizes}))


if __name__ == "__main__":
    main(*sys.argv[1:5])
