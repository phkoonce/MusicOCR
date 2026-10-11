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
Scanned PDFs have no text layer; for those ``write_ocr_texts_json`` runs OCR
(``musicocr.ocr_backends``, macOS Vision by default) directly on the prepared page
image, so its boxes need no geometry mapping.
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


def pdf_has_text(pdftotext: str, pdf: Path, page: int, log=None) -> bool:
    """True if ``page`` (1-based) has any text layer at all (scans have none)."""
    res = run_cmd_lenient([pdftotext, "-f", str(page), "-l", str(page), str(pdf), "-"],
                          timeout=60, log=log)
    return bool(res and res.returncode == 0 and res.output.strip())


def digit_boxes_from_words(words: list[dict]) -> list[dict]:
    """Keep bare 1-3 digit OCR words, dropping implausibly wide boxes (OCR often
    reads clefs/braces/staff fragments as digits inside a huge box)."""
    out = []
    for w in words:
        text = str(w.get("text", "")).strip()
        if not _DIGITS.fullmatch(text):
            continue
        x0, y0, x1, y1 = (float(v) for v in w["xyxy"])
        if (x1 - x0) > 1.0 * (y1 - y0) * len(text):
            continue
        out.append({"text": text, "xyxy": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                    "role": "words"})
    return out


def write_ocr_texts_json(png: Path, backend: str = "auto", log=None, **opts) -> int:
    """OCR the prepared page image and write ``<png stem>.texts.json`` (digits only).

    Returns the box count; 0 (and removes any stale file) when nothing was found.
    """
    from musicocr.ocr_backends import ocr_words
    dst = png.with_name(f"{png.stem}.texts.json")
    boxes = digit_boxes_from_words(ocr_words(png, backend, log=log, **opts))
    if not boxes:
        dst.unlink(missing_ok=True)
        return 0
    dst.write_text(json.dumps(boxes))
    return len(boxes)


def _image_size(png: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(png) as im:
        return im.size


# --- multi-rest detection prior ------------------------------------------------------
#
# Copisteria's evidence model re-judges every detected symbol and drops those it scores
# below 0.5 "real". A wide multiRest H-bar detected at modest confidence (Zerberus Horn 1,
# last bar: det 0.65 -> real 0.49) is discarded, so the bar becomes empty and the count
# the text boxes carry is never looked up. The model's judgement leans on the detector's
# own confidence, so where the PDF text layer independently shows a count digit over the
# symbol we raise that confidence in the cached detections file before the model reads it.

BOOSTED_CONF = 0.99


def _staff_space(dets: list[dict]) -> float | None:
    """Staff space from the median ``measure`` box height (a bar is four spaces tall)."""
    hs = sorted(d["xyxy"][3] - d["xyxy"][1] for d in dets if d.get("cls") == "measure")
    return hs[len(hs) // 2] / 4 if hs else None


def boost_multirest_dets(dets_path: Path, texts_path: Path, log=None) -> int:
    """Raise the confidence of ``multiRest`` detections that have a digit box over them.

    "Over" mirrors Copisteria's own count lookup: horizontal overlap and the digit's
    vertical centre between six staff spaces above the symbol and its mid-height.
    Rewrites ``dets_path`` in place; returns the number of detections raised.
    """
    try:
        dets = json.loads(dets_path.read_text())
        texts = json.loads(texts_path.read_text())
    except (OSError, ValueError):
        return 0
    sp = _staff_space(dets)
    raised = 0
    for d in dets:
        if d.get("cls") != "multiRest" or d.get("conf", 1.0) >= BOOSTED_CONF:
            continue
        x0, y0, x1, y1 = d["xyxy"]
        s = sp or (y1 - y0) * 0.3
        for t in texts:
            tx0, ty0, tx1, ty1 = t["xyxy"]
            if (_DIGITS.fullmatch(str(t.get("text", "")).strip()) and tx1 > x0 and tx0 < x1
                    and y0 - 6 * s < (ty0 + ty1) / 2 < y0 + (y1 - y0) / 2):
                if log:
                    log(f"  multiRest at ({x0:.0f},{y0:.0f}) conf {d['conf']:.2f} -> "
                        f"{BOOSTED_CONF} (count digit {t['text']} over it)")
                d["conf"] = BOOSTED_CONF
                raised += 1
                break
    if raised:
        dets_path.write_text(json.dumps(dets))
    return raised


MIN_TALL_SP = 5.0     # a multi-rest count is set big (~10 staff spaces tall); tuplet digits, bar numbers
                      # and fingerings are 2-3 spaces


def add_missing_multirest_dets(dets_path: Path, texts_path: Path, log=None) -> int:
    """Add a ``multiRest`` detection to an *empty* bar that has a big count digit over it.

    Copisteria's detector sometimes misses a multi-rest H-bar altogether at its default input
    size (Zerberus Euphonium 1, last bar: the 8-bar rest was found at 0.72 only one size up and
    at 0.93 two sizes down); nothing then carries the count and the bar vanishes from the bar
    total. A tall digit box (not a time signature: its bar holds notes/rests) over a bar with no
    note/rest/multiRest detection of its own is an independent witness, so a box shaped like the
    detector's own (the bar's width, from one space above its top to 0.7 of its height) is added
    with a confident score.
    Rewrites ``dets_path`` in place; returns the number of detections added.
    """
    try:
        dets = json.loads(dets_path.read_text())
        texts = json.loads(texts_path.read_text())
    except (OSError, ValueError):
        return 0
    sp = _staff_space(dets)
    if not sp:
        return 0
    measures = [d for d in dets if d.get("cls") == "measure"]
    content = [d for d in dets if d.get("cls", "").split("_")[0] in ("note", "rest", "mRest", "multiRest")
               and d.get("conf", 1.0) >= 0.5]
    added = 0
    for t in texts:
        if not _DIGITS.fullmatch(str(t.get("text", "")).strip()):
            continue
        tx0, ty0, tx1, ty1 = t["xyxy"]
        if (ty1 - ty0) < MIN_TALL_SP * sp:
            continue
        cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
        for m in measures:
            x0, y0, x1, y1 = m["xyxy"]
            if not (x0 < cx < x1 and y0 - 8 * sp < cy < y0 + (y1 - y0) / 2):
                continue
            if any(x0 <= (c["xyxy"][0] + c["xyxy"][2]) / 2 <= x1
                   and y0 - 3 * sp <= (c["xyxy"][1] + c["xyxy"][3]) / 2 <= y1 + 3 * sp for c in content):
                break
            box = [x0 + 0.02 * (x1 - x0), y0 - sp, x1 - 0.02 * (x1 - x0), y0 + 0.7 * (y1 - y0)]
            dets.append({"cls": "multiRest", "conf": 0.99, "xyxy": box,
                         "bbox": [box[0], box[1], box[2] - box[0], box[3] - box[1]]})
            content.append(dets[-1])
            added += 1
            if log:
                log(f"  multiRest added at ({box[0]:.0f},{box[1]:.0f}): count digit {t['text']} "
                    f"over an empty bar the detector read nothing in")
            break
    if added:
        dets_path.write_text(json.dumps(dets))
    return added
