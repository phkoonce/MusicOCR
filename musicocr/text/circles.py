"""Circled rehearsal numbers (the "(1)", "(2)" of the GB horn books) found by shape.

Page OCR never reads them (the circle breaks the digit) and the symbol detector has no class for them,
but a thin ring about three staff spaces across is a very distinctive mark. Staff lines are cut out of
the page first (a ring that sits on a line would otherwise merge with it), then ring-shaped templates of a
few sizes are slid over the ink: a hit is a place where nearly the whole ring is ink, the hole inside holds
some ink (the digits) but not all, and the rings of two-digit numbers are a little wider and taller.
Which number it is does not matter downstream: the marks are numbered in reading order
(``musicocr.assemble.rehearsal``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

DEFAULT_SP = 36.0               # staff space in px when the page's staves were not found (350 dpi scan of a part)
HEIGHTS_SP = (2.65, 3.7)        # ring height range, in staff spaces (single digit ~2.9, two digits ~3.5)
WIDE = (0.0, 0.15)              # ring width = height * (1 + x): a two-digit ring is a bit wider
COVER = 0.88                    # share of the ring that must be ink
HOLE = (0.04, 0.75)             # share of the hole that is ink (the digits)
PAD = 8


@dataclass
class Circle:
    box: Tuple[float, float, float, float]
    score: float


def _ring(cv2, np, w: int, h: int, thick: int = 3):
    k = np.zeros((h + 2 * PAD, w + 2 * PAD), np.float32)
    cv2.ellipse(k, ((w + 2 * PAD) // 2, (h + 2 * PAD) // 2), (w // 2, h // 2), 0, 0, 360, 1, thick)
    return k


def find_circles(img, sp: float = DEFAULT_SP) -> List[Circle]:
    """Rings on a grayscale page image (PIL image or array), ``sp`` = staff space in pixels."""
    import numpy as np
    from PIL import Image
    import cv2
    arr = np.asarray(img.convert("L") if isinstance(img, Image.Image) else img)
    _, bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(4.5 * sp), 1)))
    lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    ink = (cv2.bitwise_and(bw, cv2.bitwise_not(lines)) > 0).astype(np.float32)
    near = cv2.dilate(ink, np.ones((5, 5), np.float32))          # a ring a pixel or two off still counts
    lo, hi = int(HEIGHTS_SP[0] * sp), int(HEIGHTS_SP[1] * sp)
    hits = []
    for h in range(lo, hi + 1, 4):
        for w in sorted({h, int(h * (1 + WIDE[1] / 2)), int(h * (1 + WIDE[1]))}):
            ring = _ring(cv2, np, w, h)
            cover = cv2.filter2D(near, -1, ring / ring.sum(), borderType=cv2.BORDER_CONSTANT)
            hole = np.zeros_like(ring)
            cv2.ellipse(hole, ((w + 2 * PAD) // 2, (h + 2 * PAD) // 2), (w // 2 - 8, h // 2 - 8), 0, 0, 360, 1, -1)
            filled = cv2.filter2D(ink, -1, hole / hole.sum(), borderType=cv2.BORDER_CONSTANT)
            ys, xs = np.where((cover >= COVER) & (filled > HOLE[0]) & (filled < HOLE[1]))
            hits += [(float(cover[y, x]), int(x), int(y), w, h) for y, x in zip(ys, xs)]
    hits.sort(reverse=True)
    out: List[Circle] = []
    for score, x, y, w, h in hits:
        if all(abs(x - (c.box[0] + c.box[2]) / 2) > 0.8 * sp or abs(y - (c.box[1] + c.box[3]) / 2) > 0.8 * sp for c in out):
            out.append(Circle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), score))
    return sorted(out, key=lambda c: (c.box[1], c.box[0]))
