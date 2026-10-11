"""Page text for OMR engines that can use it, taken from the PDF's text layer.

Copisteria reads the count over a multi-measure rest from OCR text boxes in
``<page>.texts.json`` (``[{"text", "xyxy", "role"}]``, pixel coordinates of the
page image); without them it emits the rest as an ordinary bar and the count is
lost. Born-digital PDFs already carry that text, so we read it with poppler's
``pdftotext -bbox`` and map the boxes onto the *prepared* page image using the
geometry ``preprocess`` recorded (DPI scale, deskew rotation, crop offset).

Only bare 1-3 digit words are kept. Notation fonts also show up in the text
layer as junk glyph "words" (``œ``, ``˙``, ``&``, ``p``) and handing those to the
reader as directions would duplicate the dynamics it already detects; digits
are what the multi-rest logic needs and are otherwise ignored by the reader.
Scanned PDFs have no text layer and yield nothing (an OCR source is future work).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from musicocr.preprocess import PageGeometry
from musicocr.util import run_cmd_lenient

_WORD = re.compile(
    r'<word xMin="(-?[\d.]+)" yMin="(-?[\d.]+)" xMax="(-?[\d.]+)" yMax="(-?[\d.]+)">(.*?)</word>')
_DIGITS = re.compile(r"\d{1,3}")


def pdf_digit_boxes(pdftotext: str, pdf: Path, page: int, log=None) -> list[dict]:
    """Digit-only words on ``page`` (1-based), boxes in PDF points."""
    res = run_cmd_lenient([pdftotext, "-bbox", "-f", str(page), "-l", str(page),
                           str(pdf), "-"], timeout=60, log=log)
    if res is None or res.returncode != 0:
        return []
    out = []
    for x0, y0, x1, y1, text in _WORD.findall(res.output):
        text = html.unescape(text).strip()
        if _DIGITS.fullmatch(text):
            out.append({"text": text, "xyxy": [float(x0), float(y0), float(x1), float(y1)],
                        "role": "words"})
    return out


def write_texts_json(pdftotext: str, pdf: Path, page: int, png: Path, log=None) -> int:
    """Write ``<png stem>.texts.json`` next to the page image; return the box count.

    Does nothing (returns 0, removes any stale file) when the image has no
    recorded geometry or the page has no text layer.
    """
    dst = png.with_name(f"{png.stem}.texts.json")
    geom = PageGeometry.load(png)
    boxes = pdf_digit_boxes(pdftotext, pdf, page, log=log) if geom else []
    if not boxes:
        dst.unlink(missing_ok=True)
        return 0
    w, h = _image_size(png)
    kept = []
    for b in boxes:
        b["xyxy"] = [round(v, 1) for v in geom.pdf_box_to_image(b["xyxy"])]
        x0, y0, x1, y1 = b["xyxy"]
        if x1 > 0 and y1 > 0 and x0 < w and y0 < h:     # cropped-away headers fall outside
            kept.append(b)
    if not kept:
        dst.unlink(missing_ok=True)
        return 0
    dst.write_text(json.dumps(kept))
    return len(kept)


def _image_size(png: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(png) as im:
        return im.size
