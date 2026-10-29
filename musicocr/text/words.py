"""Words and phrases for the text layer (docs/text-layer-design.md sections 5.1-5.2).

``words_for_page`` returns word boxes (page-image pixels) from the PDF text layer when
the page has one, else from OCR of the bands above/below each staff. ``phrases`` joins
words on one baseline into phrases. Nothing here knows about music beyond staff geometry.

OCR path: macOS Vision is poor at small italic words on a whole 350-dpi page but reads
them from a band cut around the staves and upscaled; every band is read under two
resampling filters and the results unioned (Vision drops words under one filter and
finds them under the other). Results are cached beside the page image.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

TARGET_SP = 39.0          # staff space (px) after upscaling a band: ~1.5x on a 350-dpi page
CACHE_VERSION = "tw3"


@dataclass
class Word:
    text: str
    box: Box                    # x0, y0, x1, y1 in page-image pixels
    source: str = "ocr"         # "ocr" | "pdf"
    style: str = ""             # "", "italic", "bold", "bold italic" (PDF layer only)

    def to_json(self) -> dict:
        return {"text": self.text, "box": [round(v, 1) for v in self.box], "source": self.source,
                "style": self.style}

    @classmethod
    def from_json(cls, d: dict) -> "Word":
        return cls(d["text"], tuple(d["box"]), d.get("source", "ocr"), d.get("style", ""))


@dataclass
class Phrase:
    text: str
    box: Box
    words: List[Word] = field(default_factory=list)

    @property
    def style(self) -> str:
        s = {w.style for w in self.words if w.style}
        return s.pop() if len(s) == 1 else ""


@dataclass
class Staff:
    top: float
    bottom: float
    left: float

    @property
    def sp(self) -> float:
        return (self.bottom - self.top) / 4


# --- staves and bands ------------------------------------------------------------

def find_staves(img) -> List[Staff]:
    """Staves of a greyscale page image (PIL 'L'), found on a half-size copy and scaled back."""
    from musicocr.assemble.alignment.systems import _staff_systems
    half = img.resize((max(1, img.width // 2), max(1, img.height // 2)))
    return [Staff(top * 2, base * 2, last * 2) for top, base, last in _staff_systems(half)]


def bands(staves: Sequence[Staff], height: int, above_sp: float = 8.0, below_sp: float = 6.0,
          merge_sp: float = 18.0) -> List[Tuple[int, int]]:
    """Row ranges to OCR. Per staff a band over it (``above_sp``) and one under it (``below_sp``);
    where the gap to the next staff is at most ``merge_sp`` staff spaces, also the whole gap as one
    band (so a word between the two is not cut in two). Vision is fragile: a word it garbles in
    one crop it often reads in another, and the passes are unioned."""
    out: List[Tuple[int, int]] = []
    for i, s in enumerate(staves):
        sp = s.sp
        out.append((int(max(0, s.top - (above_sp + 1.5) * sp)), int(s.top - 0.5 * sp)))
        out.append((int(s.bottom + 0.5 * sp), int(min(height, s.bottom + (below_sp + 1.5) * sp))))
        if i + 1 < len(staves):
            n = staves[i + 1]
            if n.top - s.bottom <= merge_sp * (sp + n.sp) / 2:
                out.append((int(s.bottom + 0.5 * sp), int(n.top - 0.5 * n.sp)))
    return [(y0, y1) for y0, y1 in sorted(set(out)) if y1 - y0 > 4]


# --- OCR source ------------------------------------------------------------------

_FILTERS = {"lanczos": "LANCZOS", "bicubic": "BICUBIC"}


def _clean(text: str) -> str:
    return text.strip()


def _alnum_ratio(t: str) -> float:
    return sum(c.isalnum() for c in t) / max(1, len(t))


_METRO_TAIL = re.compile(r"=\s*\d{2,3}\)?$")


def _score(t: str) -> tuple:
    """Which of two readings of the same box to trust: one the lexicon understands (or a metronome
    mark) beats a cleaner-looking garble ("(EZI=P)" vs "(d=123)"), then the more alphanumeric, then
    the longer."""
    from musicocr.text.classify import classify
    known = classify(t).kind not in ("ignore", "unknown") or bool(_METRO_TAIL.search(t))
    return (known, _alnum_ratio(t), len(t))


def _same_box(a: Box, b: Box) -> bool:
    """The same word seen twice: centres each inside the other's box, or one box mostly inside the other."""
    def inside(p, q):
        return q[0] <= (p[0] + p[2]) / 2 <= q[2] and q[1] <= (p[1] + p[3]) / 2 <= q[3]
    if inside(a, b) and inside(b, a):
        return True
    iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return iw > 0 and ih > 0 and iw * ih >= 0.6 * max(small, 1e-6)


def union_words(passes: Iterable[List[Word]]) -> List[Word]:
    """Merge the words of several passes: boxes that coincide are one word (the better reading,
    see ``_score``, wins). Repeated until stable: a replacement changes the box it stands on."""
    merged: List[Word] = []
    for words in passes:
        merged += words
    while True:
        out: List[Word] = []
        changed = False
        for w in merged:
            for i, m in enumerate(out):
                if _same_box(w.box, m.box):
                    if _score(w.text) > _score(m.text):
                        out[i] = w
                    changed = True
                    break
            else:
                out.append(w)
        merged = out
        if not changed:
            return merged


def _dedupe_bands(words: List[Word]) -> List[Word]:
    """Bands overlap: a word seen in several is kept once (the best reading of it)."""
    return union_words([words])


def ocr_band_words(img, band: Tuple[int, int], scratch: Path, backend: str, passes: Sequence[str],
                   scale: float, log=None, **opts) -> List[Word]:
    """OCR rows ``band`` of ``img`` (PIL) under each resampling filter in ``passes``."""
    from PIL import Image
    from musicocr.text.ocr_backends import ocr_words
    y0, y1 = band
    crop = img.crop((0, y0, img.width, y1))
    results: List[List[Word]] = []
    for name in passes:
        rs = getattr(Image, _FILTERS.get(name, "LANCZOS"))
        big = crop.resize((int(crop.width * scale), int(crop.height * scale)), rs)
        big.save(scratch)
        ws = []
        for w in ocr_words(scratch, backend, log=log, **opts):
            t = _clean(w["text"])
            if not t:
                continue
            bx0, by0, bx1, by1 = (v / scale for v in w["xyxy"])
            ws.append(Word(t, (bx0, by0 + y0, bx1, by1 + y0), "ocr"))
        results.append(ws)
    return union_words(results)


def ocr_page_words(png: Path, staves: Optional[List[Staff]] = None, backend: str = "vision",
                   passes: Sequence[str] = ("lanczos", "bicubic"), above_sp: float = 8.0,
                   below_sp: float = 6.0, log=None, **opts) -> Tuple[List[Word], List[Staff]]:
    from PIL import Image
    img = Image.open(png).convert("L")
    staves = staves if staves is not None else find_staves(img)
    if not staves:
        return [], staves
    sp = sorted(s.sp for s in staves)[len(staves) // 2]
    scale = min(2.5, max(1.0, TARGET_SP / sp))
    scratch = png.with_name(f"{png.stem}.textband.png")
    words: List[Word] = []
    try:
        for band in bands(staves, img.height, above_sp, below_sp):
            words += ocr_band_words(img, band, scratch, backend, passes, scale, log=log, **opts)
    finally:
        scratch.unlink(missing_ok=True)
    return _dedupe_bands(words), staves


# --- PDF text-layer source -------------------------------------------------------

_WORD = re.compile(
    r'<word xMin="(-?[\d.]+)" yMin="(-?[\d.]+)" xMax="(-?[\d.]+)" yMax="(-?[\d.]+)">(.*?)</word>')


def pdf_page_words(pdftotext: str, pdf: Path, page: int, geom, size: Tuple[int, int],
                   log=None) -> List[Word]:
    """Every word of ``page`` (1-based) from the PDF text layer, boxes mapped onto the prepared
    image (``geom``: ``PageGeometry``); words outside the image (cropped headers) are dropped.
    Style (italic/bold) comes from the font names ``pdftohtml -xml`` reports, where available."""
    from musicocr.util import run_cmd_lenient
    res = run_cmd_lenient([pdftotext, "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          timeout=60, log=log)
    if res is None or res.returncode != 0 or geom is None:
        return []
    out: List[Word] = []
    w, h = size
    for x0, y0, x1, y1, text in _WORD.findall(res.output):
        text = html.unescape(text).strip()
        if not text:
            continue
        box = geom.pdf_box_to_image((float(x0), float(y0), float(x1), float(y1)))
        if box[2] > 0 and box[3] > 0 and box[0] < w and box[1] < h:
            out.append(Word(text, box, "pdf"))
    return out


# --- phrases ---------------------------------------------------------------------

def phrases(words: Sequence[Word], sp: float, gap_sp: float = 1.5, line_sp: float = 0.85) -> List[Phrase]:
    """Join words on one baseline into phrases: same line (centres within ``line_sp`` staff
    spaces) and a horizontal gap under ``gap_sp``. A word that ends in a hyphen is joined to the
    next without a space ("ri-" "tard." -> "ritard.")."""
    ws = sorted(words, key=lambda w: (w.box[1] + w.box[3]) / 2)
    lines: List[List[Word]] = []
    centres: List[float] = []
    for w in ws:
        cy = (w.box[1] + w.box[3]) / 2
        for i, c in enumerate(centres):
            if abs(cy - c) <= line_sp * sp:          # c = the line's latest (lowest) centre: words chain along it
                lines[i].append(w)
                centres[i] = cy
                break
        else:
            lines.append([w])
            centres.append(cy)
    out: List[Phrase] = []
    for line in lines:
        line.sort(key=lambda w: w.box[0])
        cur: List[Word] = []
        for w in line:
            if cur and (w.box[0] - cur[-1].box[2] > gap_sp * sp or _ends_metronome(cur)):
                out.append(_make(cur))
                cur = []
            cur.append(w)
        if cur:
            out.append(_make(cur))
    out.sort(key=lambda p: (p.box[1], p.box[0]))
    return out


_METRO_END = re.compile(r"=\s*(?:c\.?\s*)?\d{2,3}(?:\s*[-\u2013]\s*\d{2,3})?\)?$")
_NUM_END = re.compile(r"^\(?\d{2,3}\)?$")


def _ends_metronome(ws: List[Word]) -> bool:
    """A metronome mark ends with its number: words after it are another phrase ("(= 102)" "Choir, Cel")."""
    last = ws[-1].text
    text = " ".join(w.text for w in ws)
    return bool(_METRO_END.search(text)) and ("=" in last or bool(_NUM_END.match(last)))


def _make(ws: List[Word]) -> Phrase:
    text = ""
    for w in ws:
        if text.endswith("-") and len(text) > 1 and text[-2].isalpha() and w.text[:1].isalpha():
            text = text[:-1] + w.text
        else:
            text = f"{text} {w.text}" if text else w.text
    box = (min(w.box[0] for w in ws), min(w.box[1] for w in ws),
           max(w.box[2] for w in ws), max(w.box[3] for w in ws))
    return Phrase(text, box, list(ws))


_METRO_ONLY = re.compile(r"^[\s(]*[^\w\s=]{0,3}\s*[JjdD]?\s*\.?\s*=\s*(?:c\.?\s*)?\d")


def stack_tempo_lines(ps: List[Phrase], sp: float) -> List[Phrase]:
    """A tempo word with its metronome mark printed directly beneath it ("Sl. Slower" over
    "♩ = 122") is one phrase: join a metronome-only phrase to the phrase above it when that one
    ends within 1.5 staff spaces above and the two overlap horizontally."""
    out = list(ps)
    for b in [p for p in ps if _METRO_ONLY.match(p.text)]:
        cands = [a for a in out if a is not b and not _METRO_ONLY.match(a.text)
                 and 0 <= b.box[1] - a.box[3] <= 1.5 * sp
                 and min(a.box[2], b.box[2]) - max(a.box[0], b.box[0]) > 0.3 * min(a.box[2] - a.box[0], b.box[2] - b.box[0])]
        if cands:
            a = min(cands, key=lambda a: b.box[1] - a.box[3])
            merged = Phrase(f"{a.text} {b.text}", (min(a.box[0], b.box[0]), a.box[1],
                                                   max(a.box[2], b.box[2]), b.box[3]), a.words + b.words)
            out = [p for p in out if p is not a and p is not b] + [merged]
    out.sort(key=lambda p: (p.box[1], p.box[0]))
    return out


# --- cache -----------------------------------------------------------------------

def page_key(png: Path, params: dict) -> str:
    h = hashlib.sha1(png.read_bytes())
    h.update(json.dumps(params, sort_keys=True).encode())
    return f"{CACHE_VERSION}-{h.hexdigest()[:16]}"


def load_cached(path: Path, key: str) -> Optional[Tuple[List[Word], List[Staff]]]:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if d.get("key") != key:
        return None
    return [Word.from_json(w) for w in d["words"]], [Staff(*s) for s in d["staves"]]


def save_cache(path: Path, key: str, words: List[Word], staves: List[Staff]) -> None:
    path.write_text(json.dumps({"key": key, "words": [w.to_json() for w in words],
                                "staves": [[s.top, s.bottom, s.left] for s in staves]}))
