"""Circled rehearsal numbers (the "(1)", "(2)" of the GB horn books) found by shape.

Page OCR never reads them (the circle breaks the digit) and the symbol detector has no class for them,
but a thin ring about three staff spaces across is a very distinctive mark. Staff lines are cut out of
the page first (a ring that sits on a line would otherwise merge with it), then ring-shaped templates of a
few sizes are slid over the ink: a hit is a place where nearly the whole ring is ink, the hole inside holds
some ink (the digits) but not all, and the rings of two-digit numbers are a little wider and taller.
What is printed inside (a number or letters) is read afterwards by ``read_mark_labels``; where it cannot be, the
marks are numbered in reading order (``musicocr.assemble.rehearsal``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

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


# ---- boxed rehearsal numbers --------------------------------------------------------------------------------
# Many editions frame the number in a square instead of a ring ("[12]"). A box is a closed thin rectangle with a
# hole, a little taller than a staff space is wide... found as a contour (staff lines cut out first); its number is
# read by OCR from a contact sheet of every box on the page (a lone digit is not read by Vision, a row of them is).

BOX_H_SP = (2.0, 4.2)
BOX_W_SP = (1.8, 5.5)


def find_boxes(img, sp: float = DEFAULT_SP) -> List[Circle]:
    """Rectangles with a hole (a number in a frame) on a grayscale page image, in reading order."""
    import numpy as np
    from PIL import Image
    import cv2
    arr = np.asarray(img.convert("L") if isinstance(img, Image.Image) else img)
    _, bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(4.5 * sp), 1)))
    lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(lines))
    cnts, hier = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    out: List[Circle] = []
    for i, c in enumerate(cnts):
        if hier[0][i][3] != -1 or hier[0][i][2] == -1:          # an outer contour that has a hole
            continue
        x, y, w, h = cv2.boundingRect(c)
        if not (BOX_H_SP[0] * sp <= h <= BOX_H_SP[1] * sp and BOX_W_SP[0] * sp <= w <= BOX_W_SP[1] * sp):
            continue
        approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
        fill = cv2.contourArea(c) / float(w * h)
        if len(approx) == 4 and fill > 0.85:
            out.append(Circle((float(x), float(y), float(x + w), float(y + h)), fill))
    return sorted(out, key=lambda b: (b.box[1], b.box[0]))


def _valid_label(text: str) -> bool:
    """A printed rehearsal label: a number (1-3 digits, not 0) or one or two capital letters."""
    if text.isdigit():
        return len(text) <= 3 and int(text) > 0
    return text.isalpha() and text.isupper() and text.isascii() and len(text) <= 2


ATTEMPTS = ((2, 0.18), (3, 0.26), (2, 0.1))          # (enlargement, border trimmed as a share of the frame's height)


def read_mark_labels(img, shapes: List[Circle], ocr=None) -> List[Optional[str]]:
    """Labels of ``shapes`` (see ``_read_once``); what one reading leaves unread is tried again at another scale and
    trim, the frame's edge or a bold digit often being what defeats it."""
    out: List[Optional[str]] = [None] * len(shapes)
    for scale, trim in ATTEMPTS:
        todo = [i for i, v in enumerate(out) if v is None]
        if not todo:
            break
        for i, v in zip(todo, _read_once(img, [shapes[i] for i in todo], ocr, scale, trim)):
            out[i] = v
    return out


def _read_once(img, shapes: List[Circle], ocr, scale: int, trim: float) -> List[Optional[str]]:
    """The label inside each box or ring (a number or capital letters; None where it could not be read): the
    interiors are laid out in one row with wide gaps and read together, then each recognised word is given to the
    tile it sits over. Vision reads neither a lone digit nor a lone letter, but a row of them it does."""
    import os
    import tempfile
    from pathlib import Path
    from PIL import Image
    if not shapes:
        return []
    if ocr is None:
        from musicocr.text.ocr_backends import ocr_words
        ocr = lambda p: ocr_words(Path(p), "vision")
    gap, pad = 120, 40
    tiles = []
    for b in shapes:
        x0, y0, x1, y1 = b.box
        m = trim * (y1 - y0)
        c = img.convert("L").crop((int(x0 + m), int(y0 + m), int(x1 - m), int(y1 - m)))
        tiles.append(c.resize((c.width * scale, c.height * scale), Image.LANCZOS))
    sheet = Image.new("L", (sum(t.width + gap for t in tiles) + 2 * pad, max(t.height for t in tiles) + 2 * pad), 255)
    x, spans = pad, []
    for t in tiles:
        sheet.paste(t, (x, pad))
        spans.append((x, x + t.width))
        x += t.width + gap
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        sheet.save(name)
        words = ocr(name) or []
    finally:
        os.unlink(name)
    out: List[Optional[str]] = [None] * len(shapes)
    for wd in words:
        text = wd["text"].strip().replace(" ", "")
        if not text or not (text.isdigit() or (text.isalpha() and text.isupper())):
            continue
        wx0, _, wx1, _ = wd["xyxy"]
        over = [i for i, (a, b) in enumerate(spans) if min(wx1, b) - max(wx0, a) > 0.4 * (b - a)]
        if len(over) == 1:
            if out[over[0]] is None and _valid_label(text):
                out[over[0]] = text
            continue
        # several boxes read as one word ("123456", "8910", "ABC"): one character each when the count matches,
        # else (digits) a tile one digit wide holds one digit, a wider one two
        if len(text) == len(over):
            counts = [1] * len(over)
        elif text.isdigit():
            widths = [spans[i][1] - spans[i][0] for i in over]
            narrow = min(widths)
            counts = [1 if w < 1.5 * narrow else 2 for w in widths]
        else:
            continue
        if sum(counts) == len(text):
            k = 0
            for i, c in zip(over, counts):
                if out[i] is None and _valid_label(text[k:k + c]):
                    out[i] = text[k:k + c]
                k += c
    return out
