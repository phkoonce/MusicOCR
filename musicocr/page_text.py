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


MIN_TALL_SP = 4.0     # a multi-rest count is set big (4.5-10 staff spaces tall); tuplet digits, bar numbers
                      # and fingerings are 2-3 spaces
BREVE_MAX_CONF = 0.5  # a breve detected below this under a count digit is really the multi-rest H-bar


def _breves_to_multirests(dets: list[dict], tall: list[list[float]], sp: float, log=None) -> int:
    """Re-label low-confidence ``note_breve`` detections that sit under a big count digit.

    The thick H-bar of a multi-rest looks like a breve head on the middle line; on 150-ppi scans the
    detector then finds a weak breve instead of the multiRest, the bar is read as an 8-beat breve and
    swallows its neighbour (HTTYD2 Trumpet 2, "6" over bars 1-6: a 12-beat first bar). A real breve is
    rare and has no giant numeral over it, so the detection becomes a confident multiRest spanning from
    the clef/key/time signature (or the previous barline) to the next barline.
    """
    fixed = 0
    for d in list(dets):
        if d.get("cls") != "note_breve" or d.get("conf", 1.0) >= BREVE_MAX_CONF:
            continue
        x0, y0, x1, y1 = d["xyxy"]
        cx = (x0 + x1) / 2
        t = next((b for b in tall if b[0] < cx < b[2] and 0 < y0 - (b[1] + b[3]) / 2 < 10 * sp), None)
        if t is None:
            continue
        left = [e["xyxy"][2] for e in dets
                if e is not d and e.get("conf", 1.0) >= 0.5 and e["xyxy"][2] < x0
                and (e.get("cls", "").split("_")[0] in ("clef", "keySig", "meterSig")
                     or e.get("cls", "").startswith("barLine")) and abs(e["xyxy"][1] - y0) < 8 * sp]
        right = [(e["xyxy"][0] + e["xyxy"][2]) / 2 for e in dets
                 if e.get("conf", 1.0) >= 0.5 and e.get("cls", "").startswith("barLine")
                 and (e["xyxy"][0] + e["xyxy"][2]) / 2 > x1 and abs(e["xyxy"][1] - y0) < 8 * sp]
        bx0 = max(left, default=x0 - 4 * sp) + 0.5 * sp
        bx1 = min(right, default=x1 + 4 * sp) - 0.5 * sp
        box = [bx0, y0 - 0.3 * sp, bx1, y1 + 0.3 * sp]
        if log:
            log(f"  breve at ({x0:.0f},{y0:.0f}) conf {d.get('conf', 0):.2f} under a big digit: re-labelled multiRest")
        d.update({"cls": "multiRest", "conf": 0.99, "xyxy": box, "bbox": [box[0], box[1], box[2] - box[0], box[3] - box[1]]})
        d.pop("attrs", None), d.pop("attr_p", None), d.pop("attr_dist", None)
        fixed += 1
    return fixed


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
    tall = [t["xyxy"] for t in texts if _DIGITS.fullmatch(str(t.get("text", "")).strip())
            and t["xyxy"][3] - t["xyxy"][1] >= MIN_TALL_SP * sp]
    added = _breves_to_multirests(dets, tall, sp, log)
    content = [d for d in dets if d.get("cls", "").split("_")[0] in ("note", "rest", "mRest", "multiRest")
               and d.get("conf", 1.0) >= 0.5]

    def stacked(b) -> bool:
        """Another tall digit right above/below it: the two numerals of a time signature."""
        w, h = b[2] - b[0], b[3] - b[1]
        return any(o is not b and abs((o[0] + o[2]) / 2 - (b[0] + b[2]) / 2) <= 0.6 * max(w, 1)
                   and 0 < abs(o[1] - b[1]) <= 1.8 * h for o in tall)

    for t in texts:
        if not _DIGITS.fullmatch(str(t.get("text", "")).strip()):
            continue
        tx0, ty0, tx1, ty1 = t["xyxy"]
        if (ty1 - ty0) < MIN_TALL_SP * sp or stacked(t["xyxy"]):
            continue
        cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
        for m in measures:
            x0, y0, x1, y1 = m["xyxy"]
            if not (x0 < cx < x1 and y0 - 8 * sp < cy < y0 + (y1 - y0) / 2):
                continue
            if abs(cx - (x0 + x1) / 2) > 0.2 * (x1 - x0):
                break          # a count is centred over its bar; a time signature sits at its left edge
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


# --- small noteheads are grace notes -------------------------------------------------
#
# The detector classifies a beamed pair of small notes before a normal note (a ruff /
# flam, very common in timpani and drum parts) as grace notes through its ``grace``
# attribute, but for a few of them it says "none" with a low "acc" probability although
# the notehead is plainly ~60% of the size of the page's ordinary ones. The evidence model
# follows the detector and then writes them as real notes (the bar overflows, the next
# note is dropped, a gap appears). Where a note detection is much smaller than the page's
# ordinary notes we therefore set its grace attribute in the cached detections file before
# the model reads it.

GRACE_ACC_P = 0.97
GRACE_W_RATIO = 0.8         # width below this x the page's median ordinary note ...
GRACE_H_RATIO = 0.9         # ... and height below this (graces are ~0.62 x 0.62, ordinary notes >= 0.95)
GRACE_MIN_REFERENCE = 6     # ordinary notes needed to trust the median


def mark_small_graces(dets_path: Path, log=None) -> int:
    """Set ``grace = acc`` on note detections clearly smaller than the page's ordinary notes.

    Rewrites ``dets_path`` in place; returns the number of detections changed.
    """
    try:
        dets = json.loads(dets_path.read_text())
    except (OSError, ValueError):
        return 0
    notes = [d for d in dets if str(d.get("cls", "")).startswith("note_") and d.get("conf", 1.0) >= 0.5]
    ordinary = [d for d in notes if d.get("attrs", {}).get("grace", "<na>") in ("<na>", "none")
                and d.get("attr_dist", {}).get("grace", {}).get("acc", 0.0) < 0.5]
    if len(ordinary) < GRACE_MIN_REFERENCE:
        return 0
    ws = sorted(d["xyxy"][2] - d["xyxy"][0] for d in ordinary)
    hs = sorted(d["xyxy"][3] - d["xyxy"][1] for d in ordinary)
    # the median ordinary note: the small ones are a minority on any page
    mw, mh = ws[len(ws) // 2], hs[len(hs) // 2]
    changed = 0
    for d in ordinary:
        w, h = d["xyxy"][2] - d["xyxy"][0], d["xyxy"][3] - d["xyxy"][1]
        if w < GRACE_W_RATIO * mw and h < GRACE_H_RATIO * mh:
            d.setdefault("attrs", {})["grace"] = "acc"
            d.setdefault("attr_p", {})["grace"] = GRACE_ACC_P
            d.setdefault("attr_dist", {})["grace"] = {"<na>": round((1 - GRACE_ACC_P) * 0.7, 4),
                                                       "acc": GRACE_ACC_P,
                                                       "unacc": round((1 - GRACE_ACC_P) * 0.3, 4)}
            changed += 1
            if log:
                log(f"  small note at ({d['xyxy'][0]:.0f},{d['xyxy'][1]:.0f}) {w:.0f}x{h:.0f} "
                    f"vs median {mw:.0f}x{mh:.0f}: grace")
    if changed:
        dets_path.write_text(json.dumps(dets))
    return changed
