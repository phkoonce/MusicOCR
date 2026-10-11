"""Run inside the Copisteria venv: re-detect a page at a larger input size and
union the extra note/rest detections into a copy of the page's cached detections.

    python second_pass_detect.py <page.png> <base_dets.json> <out_dets.json> <scale> [mode [cache.json]]

``mode`` is ``union`` (default: add the note/rest detections that overlap nothing) or
``empty-bars`` (add notes/rests/whole-bar rests only inside measure boxes that hold nothing:
a lost-bar repair, see ``fill_empty_bars``; no detection is run when no box is empty).
``cache.json`` stores the larger-size detections so a later call on the same page reuses them.

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


# Symbols that make a bar non-empty (front.py's ``music``), plus the whole-bar rest.
MUSIC = ("note", "rest", "mRest", "multiRest", "mRpt", "multiRpt")
# What a bar the detector read as empty may get from the bigger size: notes, rests, whole-bar rests.
EMPTY_BAR_FAMILIES = ("note", "rest", "mRest")
EMPTY_BAR_MIN_CONF = 0.9


def empty_bars(dets: list) -> list:
    """Boxes of the confident ``measure`` detections that hold no note, rest or multi-rest at all."""
    fam = lambda c: c["cls"].split("_")[0]  # noqa: E731
    music = [d["xyxy"] for d in dets if fam(d) in MUSIC and d.get("conf", 1.0) >= 0.3]
    out = []
    for m in dets:
        if m["cls"] != "measure" or m.get("conf", 0.0) < 0.5:
            continue
        x0, y0, x1, y1 = m["xyxy"]
        if not any(x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1 for b in music):
            out.append(m["xyxy"])
    return out


def fill_empty_bars(base: list, big: list) -> list:
    """``base`` plus the confident note / rest / whole-bar-rest detections of ``big`` that sit in a
    measure box the base detections left empty and overlap no base detection of the same family.

    An empty box makes Copisteria merge the bar into its neighbour (a last bar holding only a whole
    rest is dropped; a first bar whose only note went undetected fuses with the next one), which is
    a lost bar, not a missing note, so it cannot be repaired by splicing a later reading.
    """
    fam = lambda c: c["cls"].split("_")[0]  # noqa: E731
    boxes = empty_bars(base)
    if not boxes:
        return base
    extra: list = []
    for r in big:
        if fam(r) not in EMPTY_BAR_FAMILIES or r.get("conf", 0.0) < EMPTY_BAR_MIN_CONF:
            continue
        cx, cy = (r["xyxy"][0] + r["xyxy"][2]) / 2, (r["xyxy"][1] + r["xyxy"][3]) / 2
        if not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
            continue
        if any(fam(b) == fam(r) and _iou(b["xyxy"], r["xyxy"]) > IOU_SKIP for b in base + extra):
            continue
        extra.append(r)
    return base + extra


def _big_dets(png: str, imgsz: int, scale: str, cache: str | None):
    """The detections of ``png`` at ``imgsz * scale`` (rounded to 32), read from / saved to ``cache``."""
    big_sz = int(round(imgsz * float(scale) / 32.0)) * 32
    if cache and Path(cache).exists():
        return json.loads(Path(cache).read_text()), big_sz
    from PIL import Image

    from copisteria.detect import Detector

    det = Detector()
    arr, s = det.prepare(Image.open(png), big_sz)
    big = det.run_array(arr, s)
    if cache:
        Path(cache).write_text(json.dumps(big))
    return big, big_sz


def main(png: str, base: str, out: str, scale: str, mode: str = "union", cache: str | None = None) -> None:
    base_path = Path(base)
    imgsz = int(base_path.stem.rsplit("_", 1)[1])
    dets = json.loads(base_path.read_text())
    if mode == "empty-bars" and not empty_bars(dets):      # nothing to repair: no detection run
        Path(out).write_text(json.dumps(dets))
        print(json.dumps({"added": 0, "imgsz": imgsz}))
        return
    big, big_sz = _big_dets(png, imgsz, scale, cache)
    merged = fill_empty_bars(dets, big) if mode == "empty-bars" else union_dets(dets, big)
    Path(out).write_text(json.dumps(merged))
    print(json.dumps({"added": len(merged) - len(dets), "imgsz": big_sz}))


if __name__ == "__main__":
    main(*sys.argv[1:7])
