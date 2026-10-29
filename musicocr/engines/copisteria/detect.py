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


CONTENT = ("note", "rest", "mRest", "multiRest", "mRpt", "multiRpt")
CONTENT_MIN_CONF = 0.4          # Copisteria's own bar-content filter (p >= 0.4)
EMPTY_FAMILIES = ("note", "rest", "mRest", "multiRest")
EMPTY_MIN_CONF = 0.5


def empty_measures(dets: list) -> list:
    """``measure`` detections with nothing (note/rest/mRest/multiRest/repeat, conf >= 0.4) centred in them.

    Copisteria drops an empty first/last stretch of a system as "no bar" (the cautionary key or
    clef after the last bar line) and a bar with nothing in it reads as nothing, so a missed
    symbol that is a bar's only content loses the *whole bar* from the output -- not a gap that
    the gap-bar second pass would notice (and the bar count change blocks splicing a later reading)."""
    fam = lambda c: c["cls"].split("_")[0]  # noqa: E731
    content = [d for d in dets if fam(d) in CONTENT and d.get("conf", 1.0) >= CONTENT_MIN_CONF]
    out = []
    for m in dets:
        if fam(m) != "measure" or m.get("conf", 1.0) < 0.5:
            continue
        x0, y0, x1, y1 = m["xyxy"]
        h = y1 - y0
        if not any(x0 <= (c["xyxy"][0] + c["xyxy"][2]) / 2 <= x1
                   and y0 - 0.75 * h <= (c["xyxy"][1] + c["xyxy"][3]) / 2 <= y1 + 0.75 * h for c in content):
            out.append(m)
    return out


def union_into_empty(base: list, big: list) -> list:
    """``base`` plus the note/rest/mRest/multiRest detections (conf >= 0.5) of ``big`` that are centred in a
    bar ``base`` found empty and do not overlap a base detection of the same family."""
    fam = lambda c: c["cls"].split("_")[0]  # noqa: E731
    empties = empty_measures(base)
    if not empties:
        return base
    extra = []
    for r in big:
        if fam(r) not in EMPTY_FAMILIES or r.get("conf", 0.0) < EMPTY_MIN_CONF:
            continue
        cx, cy = (r["xyxy"][0] + r["xyxy"][2]) / 2, (r["xyxy"][1] + r["xyxy"][3]) / 2
        if not any(m["xyxy"][0] <= cx <= m["xyxy"][2] and m["xyxy"][1] - 0.75 * (m["xyxy"][3] - m["xyxy"][1]) <= cy
                   <= m["xyxy"][3] + 0.75 * (m["xyxy"][3] - m["xyxy"][1]) for m in empties):
            continue
        if any(fam(b) == fam(r) and _iou(b["xyxy"], r["xyxy"]) > IOU_SKIP for b in base + extra):
            continue
        extra.append(r)
    return base + extra


def main(png: str, base: str, out: str, scale: str, mode: str = "", cache: str | None = None) -> None:
    """``mode`` ``--empty-bars``: only fill detector-empty bars (one scale; the larger-scale detections are
    cached in ``cache`` for the gap pass to reuse). Otherwise union note/rest detections over the
    comma-separated ``scale`` list."""
    base_path = Path(base)
    imgsz = int(base_path.stem.rsplit("_", 1)[1])
    dets = json.loads(base_path.read_text())
    empty_mode = mode.lstrip("-") == "empty-bars"
    if empty_mode and not empty_measures(dets):          # nothing to repair: no detection run
        Path(out).write_text(json.dumps(dets))
        print(json.dumps({"added": 0, "imgsz": imgsz, "sizes": []}))
        return

    from PIL import Image

    from copisteria.detect import Detector

    det = Detector()
    img = Image.open(png)
    merged, sizes = dets, []
    # ``scale`` may be a comma list: the detector's blind spots depend on the input size
    # (a symbol missed at 1600 and 2048 can show at 1824 or 2400+), so union several.
    for k, sc in enumerate(str(scale).split(",")[:1] if empty_mode else str(scale).split(",")):
        big_sz = int(round(imgsz * float(sc) / 32.0)) * 32
        if big_sz in sizes:
            continue
        sizes.append(big_sz)
        use_cache = cache if (cache and k == 0) else None     # the cache holds the first scale only
        if use_cache and Path(use_cache).exists():
            big = json.loads(Path(use_cache).read_text())
        else:
            arr, s = det.prepare(img, big_sz)
            big = det.run_array(arr, s)
            if use_cache:
                Path(use_cache).write_text(json.dumps(big))
        merged = union_into_empty(merged, big) if empty_mode else union_dets(merged, big)
    Path(out).write_text(json.dumps(merged))
    print(json.dumps({"added": len(merged) - len(dets), "imgsz": sizes[-1], "sizes": sizes}))


if __name__ == "__main__":
    main(*sys.argv[1:7])
