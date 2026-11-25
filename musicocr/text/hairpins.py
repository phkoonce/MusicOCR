"""Long hairpins the symbol detector cannot see, found from the page pixels.

The detector reads a page at a fixed input size; a thin hairpin running over several bars is far longer
than anything it was trained on and comes out as nothing (GHS Xmas score p11: three five-bar crescendos,
no detection above 0.05). Their shape is unambiguous: two straight lines between the staves that meet in a
point, so they are picked out of the connected ink between the staves and added to the cached detections
for the reader, which writes them as it does any other hairpin.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

MIN_LENGTH_SP = 7.0            # shorter ones the detector reads itself
MAX_HEIGHT_SP = 4.0            # opening height, plus the tilt of a skewed scan
OPEN_SP, APEX_SP = 0.6, 0.4    # the two arms are this far apart at the open end / at the point
MAX_BEND_SP = 0.2              # each arm is a straight line (a slur is not)
INK = 200


def _arms(np, comp, sp: float) -> Optional[str]:
    """"cres" / "dim" when the component ``comp`` (bool array) is two straight lines meeting at one end."""
    h, w = comp.shape
    margin = max(2, w // 50)
    xs = np.linspace(margin, w - 1 - margin, 60).astype(int)
    tops, bots, used = [], [], []
    for x in xs:
        rows = np.flatnonzero(comp[:, x])
        if len(rows):
            used.append(x)
            tops.append(rows[0])
            bots.append(rows[-1])
    if len(used) < 0.9 * len(xs):
        return None
    x, top, bot = (np.array(v, dtype=float) for v in (used, tops, bots))
    fits = []
    for y in (top, bot):
        slope, base = np.polyfit(x, y, 1)
        if np.abs(y - (slope * x + base)).mean() > MAX_BEND_SP * sp:
            return None
        fits.append((slope, base))
    sep = [(fits[1][0] - fits[0][0]) * e + fits[1][1] - fits[0][1] for e in (0, w - 1)]
    if min(sep) > APEX_SP * sp or max(sep) < OPEN_SP * sp:
        return None
    # hollow between the arms (a beam or a wedge of ink is not a hairpin)
    mid = x[(bot - top) > OPEN_SP * sp * 0.7]
    if len(mid) and np.mean([comp[:, int(c)].sum() > 0.6 * (np.ptp(np.flatnonzero(comp[:, int(c)])) + 1)
                             for c in mid]) > 0.2:
        return None
    return "cres" if sep[1] > sep[0] else "dim"


def find_hairpins(gray, staves: Sequence[Tuple[float, float]], sp: float) -> List[dict]:
    """``[{"cls": "hairpin_cres"|"hairpin_dim", "xyxy": [...]}]`` of the long hairpins on a page.
    ``gray``: the page as a 2-D uint8 array; ``staves``: (top, bottom) rows of every staff."""
    import cv2
    import numpy as np
    ink = (gray < INK).astype(np.uint8)
    for top, bottom in staves:
        ink[max(0, int(top - 0.3 * sp)):int(bottom + 0.3 * sp) + 1] = 0
    # barlines run through the gaps of a score's system and would chain everything together
    upright = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((int(2.5 * sp), 1), np.uint8))
    ink = ink & (1 - cv2.dilate(upright, np.ones((1, 5), np.uint8)))
    # bridge the cuts they leave, and scan breaks in a thin line
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, int(0.6 * sp) | 1), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < MIN_LENGTH_SP * sp or not 0.5 * sp <= h <= MAX_HEIGHT_SP * sp or area > 0.35 * w * h:
            continue
        kind = _arms(np, (labels[y:y + h, x:x + w] == i) & (closed[y:y + h, x:x + w] == 1), sp)
        if kind:
            out.append({"cls": f"hairpin_{kind}", "xyxy": [float(x), float(y), float(x + w), float(y + h)]})
    return out


def _covered(box, others: Sequence[Sequence[float]]) -> bool:
    for b in others:
        iw = min(box[2], b[2]) - max(box[0], b[0])
        ih = min(box[3], b[3]) - max(box[1], b[1])
        if ih > 0 and iw > 0.5 * (box[2] - box[0]):
            return True
    return False


def add_long_hairpins(png: Path, dets_path: Path, log=None) -> int:
    """Add the page's undetected long hairpins to its cached detections; returns the number added."""
    import numpy as np
    from PIL import Image
    from musicocr.text.words import find_staves
    dets = json.loads(dets_path.read_text())
    with Image.open(png) as im:
        img = im.convert("L")
    try:
        staves = find_staves(img)
    except ValueError:
        return 0
    if not staves:
        return 0
    sp = sorted(s.sp for s in staves)[len(staves) // 2]
    known = [d["xyxy"] for d in dets if str(d.get("cls", "")).startswith("hairpin") and d.get("conf", 1.0) >= 0.5]
    new = [h for h in find_hairpins(np.asarray(img), [(s.top, s.bottom) for s in staves], sp)
           if not _covered(h["xyxy"], known)]
    for h in new:
        x0, y0, x1, y1 = h["xyxy"]
        dets.append({"cls": h["cls"], "conf": 0.95, "xyxy": h["xyxy"], "bbox": [x0, y0, x1 - x0, y1 - y0]})
        if log:
            log(f"  {png.stem}: long {h['cls'].split('_')[1]} hairpin at ({x0:.0f},{y0:.0f}), {(x1 - x0) / sp:.0f} spaces")
    if new:
        dets_path.write_text(json.dumps(dets))
    return len(new)
