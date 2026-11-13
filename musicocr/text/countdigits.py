"""Reader for the bold count digits above multi-measure rests.

macOS Vision (and rapidocr) often miss a bold lone digit over a multi-rest H-bar, or read it
wrongly ("4", "6", "9" all come out as "6"); the copisteria reader then writes the rest as one bar.
The H-bars themselves are found reliably by the detector, so this module reads the digit(s) over each
detected ``multiRest`` directly from the page image:

1. ``find_count_blobs``: ink components in the strip above the bar, grouped into a number centred over it;
2. ``DigitBank``: normalised glyph bitmaps per class, built *self-supervised* from the digits the OCR did
   read over multi-rests in the same book (``harvest``), topped up by synthetic bold-font renderings for
   classes the book never showed; nearest-template classification with a margin test;
3. ``read_counts``: adds (or corrects) the digit box in ``<page>.texts.json`` for every multi-rest whose count
   is missing or contradicts a confident template read, so the existing count rules take over unchanged.

A read is accepted only when the glyph matches a template well and clearly better than any other class
(and, for a count the OCR already gave, only when that disagrees confidently): a wrong digit is worse than none.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

SIZE = 28                    # glyph bitmap side (px), height-normalised, centred
MIN_H_SP, MAX_H_SP = 1.6, 6.0       # a count glyph is this tall (staff spaces)
GROUP_GAP = 0.9              # components closer than this x glyph height belong to one number
MAX_COUNT = 3                # digits in a count
CENTRE_FRAC = 0.30           # number centre vs bar centre: this share of the bar's width ...
ACCEPT_SIM = 0.84            # a template must correlate at least this well
ACCEPT_MARGIN = 0.05         # ... and beat the best other class by this much
JUNK_CHARS = "pfmrsczaetwvbdhkngxyPFMRSCTLEBDKNAVWXYZ.-=<>"
FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _norm_glyph(binary: np.ndarray) -> np.ndarray:
    """Binary glyph (ink = 1, tight box) -> float SIZE x SIZE bitmap: height-normalised, centred, blurred, unit norm."""
    import cv2
    h, w = binary.shape
    s = (SIZE - 4) / max(h, 1)
    nw = max(1, min(SIZE - 4, int(round(w * s))))
    g = cv2.resize(binary.astype(np.float32), (nw, SIZE - 4), interpolation=cv2.INTER_AREA)
    out = np.zeros((SIZE, SIZE), np.float32)
    x0, y0 = (SIZE - nw) // 2, 2
    out[y0:y0 + SIZE - 4, x0:x0 + nw] = g
    out = cv2.GaussianBlur(out, (3, 3), 0.8)
    out -= out.mean()
    n = float(np.linalg.norm(out))
    return out / n if n else out


def _binarise(gray: np.ndarray) -> np.ndarray:
    import cv2
    g = gray if gray.dtype == np.uint8 else gray.astype(np.uint8)
    t, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    thr = min(max(t, 90), 170)
    return (g < thr).astype(np.uint8)


def components(binary: np.ndarray, min_h: float, max_h: float, min_w: float = 2.0):
    """Ink components of a binary strip as (x0, y0, x1, y1, mask) within the height bounds."""
    import cv2
    n, lab, st, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if min_h <= h <= max_h and w >= min_w and w <= 1.3 * max_h and a >= 0.2 * w * h:
            out.append((int(x), int(y), int(x + w), int(y + h), (lab[y:y + h, x:x + w] == i).astype(np.uint8)))
    return sorted(out, key=lambda c: c[0])


def split_glued(comp, max_ratio: float = 1.02):
    """Cut a component wider than ``max_ratio`` x its height (two glued digits: "12", "11") at the thinnest column of
    its middle third, recursively; returns component tuples (x0, y0, x1, y1, mask)."""
    x0, y0, x1, y1, m = comp
    h, w = m.shape
    if w <= max_ratio * h or w < 12:
        return [comp]
    cols = m.sum(axis=0).astype(float)
    lo, hi = int(0.25 * w), int(0.75 * w)
    cut = lo + int(np.argmin(cols[lo:hi + 1]))
    out = []
    for a, b in ((0, cut), (cut + 1, w)):
        part = m[:, a:b]
        ys, xs = np.nonzero(part)
        if len(xs) < 0.2 * h:
            continue
        px0, px1, py0, py1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        sub = (x0 + a + int(px0), y0 + int(py0), x0 + a + int(px1), y0 + int(py1), part[py0:py1, px0:px1])
        out.extend(split_glued(sub, max_ratio))
    return out


def _staff_space(dets: list[dict]) -> float | None:
    hs = sorted(d["xyxy"][3] - d["xyxy"][1] for d in dets if d.get("cls") == "measure")
    return hs[len(hs) // 2] / 4 if hs else None


def find_count_blobs(gray: np.ndarray, bar: list[float], sp: float, staff_top: float | None = None,
                     ) -> list[tuple[int, int, int, int, np.ndarray]]:
    """Glyphs (page-pixel box + the component's own ink mask, left to right) of the number centred above the multi-rest ``bar``.

    The strip runs from 8 staff spaces above the H-bar's box to just over the staff's top line; only
    components of count-glyph size, grouped by proximity, and the group nearest the bar's centre that is itself
    centred over the bar (within ``CENTRE_FRAC`` of its width + a space) count.
    """
    x0, y0, x1, y1 = bar
    top = staff_top if staff_top is not None else y0 + sp
    ya, yb = int(max(0, top - 9 * sp)), int(max(0, top - 0.35 * sp))
    xa, xb = int(max(0, x0 - 4 * sp)), int(min(gray.shape[1], x1 + 4 * sp))
    if yb - ya < 2 * sp or xb - xa < 2 * sp:
        return []
    strip = _binarise(gray[ya:yb, xa:xb])
    comps = [piece for c in components(strip, MIN_H_SP * sp, MAX_H_SP * sp) for piece in split_glued(c)]
    comps.sort(key=lambda c: c[0])
    if not comps:
        return []
    bands: list[list] = []            # glyphs of one number share a baseline and a height: first band by baseline
    for c in sorted(comps, key=lambda c: c[3]):
        for band in bands:
            if abs(c[3] - band[0][3]) <= 0.2 * (c[3] - c[1]) and 0.74 <= (c[3] - c[1]) / (band[0][3] - band[0][1]) <= 1.35:
                band.append(c)
                break
        else:
            bands.append([c])
    groups = []
    for band in bands:
        band.sort(key=lambda c: c[0])
        cur = [band[0]]
        for c in band[1:]:
            if c[0] - cur[-1][2] <= GROUP_GAP * max(c[3] - c[1], cur[-1][3] - cur[-1][1]):
                cur.append(c)
            else:
                groups.append(cur)
                cur = [c]
        groups.append(cur)
    bc = (x0 + x1) / 2
    best = None
    for g in groups:
        if len(g) > MAX_COUNT:
            continue
        gx0, gx1 = g[0][0] + xa, g[-1][2] + xa
        gh = np.mean([c[3] - c[1] for c in g])
        if max(c[3] - c[1] for c in g) > 1.35 * min(c[3] - c[1] for c in g) and len(g) > 1:
            continue        # a number's glyphs share a height (not a word's letters)
        if not (-0.2 * sp <= (yb - ya) - max(c[3] for c in g) <= 5.5 * sp):
            continue        # a count sits just above the staff, not up in the text line above it
        if g[0][0] == 0 or g[-1][2] >= strip.shape[1]:
            continue        # a glyph cut by the strip's edge
        d = abs((gx0 + gx1) / 2 - bc)
        if d <= CENTRE_FRAC * (x1 - x0) + sp and (best is None or d < best[0]):
            best = (d, g, gh)
    if best is None:
        return []
    return [(c[0] + xa, c[1] + ya, c[2] + xa, c[3] + ya, c[4]) for c in best[1]]


class DigitBank:
    """Normalised glyph templates per digit class, from book samples (weight 1) and fonts (fallback)."""

    def __init__(self):
        self.book: dict[int, list[np.ndarray]] = {k: [] for k in range(10)}
        self.font: dict[int, list[np.ndarray]] = {k: [] for k in range(10)}
        self.junk: list[np.ndarray] = []      # letters / dynamics that a bold-digit match must beat (class "not a digit")

    def add(self, digit: int, glyph_bin: np.ndarray, book: bool = True) -> None:
        (self.book if book else self.font)[digit].append(_norm_glyph(glyph_bin))

    def n_book(self) -> dict[int, int]:
        return {k: len(v) for k, v in self.book.items() if v}

    def add_fonts(self) -> None:
        from PIL import Image, ImageDraw, ImageFont
        for fp in FONT_PATHS:
            if not Path(fp).exists():
                continue
            try:
                font = ImageFont.truetype(fp, 96)
            except OSError:
                continue
            for ch in JUNK_CHARS:
                im = Image.new("L", (160, 160), 255)
                ImageDraw.Draw(im).text((20, 10), ch, font=font, fill=0)
                b = (np.asarray(im) < 128).astype(np.uint8)
                ys, xs = np.nonzero(b)
                if len(ys):
                    self.junk.append(_norm_glyph(b[ys.min():ys.max() + 1, xs.min():xs.max() + 1]))
            for d in range(10):
                im = Image.new("L", (160, 160), 255)
                ImageDraw.Draw(im).text((20, 10), str(d), font=font, fill=0)
                b = (np.asarray(im) < 128).astype(np.uint8)
                ys, xs = np.nonzero(b)
                if len(ys):
                    self.add(d, b[ys.min():ys.max() + 1, xs.min():xs.max() + 1], book=False)

    def scores(self, glyph_bin: np.ndarray) -> dict[int, float]:
        """Per class: mean of the three best book-template correlations (one stray, mislabelled template cannot carry a class);
        font templates stand in, discounted by 0.04, for a class with fewer than two book templates."""
        g = _norm_glyph(glyph_bin)
        out = {}
        for k in range(10):
            sims = sorted((float((g * t).sum()) for t in self.book[k]), reverse=True)[:3]
            fonts = [float((g * t).sum()) - 0.04 for t in self.font[k]]
            if len(sims) >= 2:
                out[k] = sum(sims) / len(sims)
            else:
                out[k] = max(sims + fonts, default=-1.0)
        return out

    def drop_contradicted(self) -> int:
        """Self-supervision clean-up: a book template whose class is clearly outvoted by another class's templates
        (the OCR read a 9 as 6) is dropped. Returns how many were dropped."""
        dropped = 0
        for k in range(10):
            keep = []
            for i, t in enumerate(self.book[k]):
                own = sorted((float((t * u).sum()) for j, u in enumerate(self.book[k]) if j != i), reverse=True)[:3]
                other = {c: sorted((float((t * u).sum()) for u in self.book[c]), reverse=True)[:3]
                         for c in range(10) if c != k and self.book[c]}
                mine = sum(own) / len(own) if own else 0.0
                worse = [c for c, v in other.items() if len(v) >= 3 and sum(v) / len(v) > mine + 0.03]
                if worse and len(own) >= 2:
                    dropped += 1
                else:
                    keep.append(t)
            self.book[k] = keep
        return dropped

    def classify(self, glyph_bin: np.ndarray) -> tuple[int | None, float, float]:
        """(digit or None, similarity, margin to the runner-up class)."""
        sc = self.scores(glyph_bin)
        order = sorted(sc, key=sc.get, reverse=True)
        best, second = order[0], order[1]
        sim, margin = sc[best], sc[best] - sc[second]
        if self.junk:
            g = _norm_glyph(glyph_bin)
            if max(float((g * t).sum()) for t in self.junk) >= sim - 0.01:
                return None, sim, 0.0          # looks at least as much like a letter/dynamic mark
        if sim >= ACCEPT_SIM and margin >= ACCEPT_MARGIN:
            return best, sim, margin
        return None, sim, margin

    def save(self, path: Path) -> None:
        data = {}
        for k in range(10):
            if self.book[k]:
                data[f"b{k}"] = np.stack(self.book[k])
        np.savez_compressed(path, **data)

    @classmethod
    def load(cls, path: Path | None, fonts: bool = True) -> "DigitBank":
        bank = cls()
        if path and Path(path).exists():
            with np.load(path) as z:
                for k in range(10):
                    if f"b{k}" in z:
                        bank.book[k] = list(z[f"b{k}"])
        if fonts:
            bank.add_fonts()
        return bank


def read_number(bank: DigitBank, glyphs) -> tuple[int | None, float]:
    """Classify each glyph; the number, or None when any glyph is rejected. Second value: lowest similarity."""
    digits, sims = [], []
    for g in glyphs:
        d, sim, _ = bank.classify(g[4])
        if d is None:
            return None, sim
        digits.append(d)
        sims.append(sim)
    if not digits:
        return None, 0.0
    return int("".join(str(d) for d in digits)), min(sims)


_DIGITS = re.compile(r"\d{1,3}")


def count_text_for(texts: list[dict], bar: list[float], sp: float):
    """The OCR digit box that serves as this H-bar's count (same geometry as the engine's ``_is_count``), or None."""
    bc = (bar[0] + bar[2]) / 2
    best = None
    for t in texts:
        if not _DIGITS.fullmatch(str(t.get("text", "")).strip()) or "xyxy" not in t:
            continue
        x0, y0, x1, y1 = t["xyxy"]
        if not (x1 > bar[0] and x0 < bar[2] and bar[1] - 8 * sp < (y0 + y1) / 2 < bar[1] + (bar[3] - bar[1]) / 2):
            continue
        if (y1 - y0) < 1.5 * sp or abs((x0 + x1) / 2 - bc) > 0.35 * (bar[2] - bar[0]) + sp:
            continue
        d = abs((x0 + x1) / 2 - bc)
        if best is None or d < best[0]:
            best = (d, t)
    return best[1] if best else None


def engine_counts(texts: list[dict], bar: list[float], sp: float) -> list[dict]:
    """Every OCR digit box the engine would take as this H-bar's count (copisteria ``_is_count``: 2.5+ spaces tall, or
    1.5+ and centred over the bar), within its horizontal extent and the six spaces above it."""
    out = []
    for t in texts:
        if not _DIGITS.fullmatch(str(t.get("text", "")).strip()) or "xyxy" not in t:
            continue
        x0, y0, x1, y1 = t["xyxy"]
        if not (x1 > bar[0] and x0 < bar[2] and bar[1] - 6 * sp < (y0 + y1) / 2 < bar[1] + (bar[3] - bar[1]) / 2):
            continue
        h = y1 - y0
        if h >= 2.5 * sp or (h >= 1.5 * sp and abs((x0 + x1) / 2 - (bar[0] + bar[2]) / 2) <= 0.35 * (bar[2] - bar[0]) + sp):
            out.append(t)
    return out


def staff_top_for(dets: list[dict], bar: list[float], sp: float) -> float | None:
    cx = (bar[0] + bar[2]) / 2
    cand = [d["xyxy"] for d in dets if d.get("cls") == "measure" and d["xyxy"][0] - sp < cx < d["xyxy"][2] + sp
            and abs(d["xyxy"][1] - bar[1]) < 3 * sp]
    return min((c[1] for c in cand), default=None)


def multirest_bars(dets: list[dict], min_conf: float = 0.3) -> list[list[float]]:
    return [d["xyxy"] for d in dets if d.get("cls") == "multiRest" and d.get("conf", 1.0) >= min_conf]


MIN_COUNT, MAX_COUNT_VALUE = 2, 199
OVERRIDE_SIM = 0.93          # replacing a number the OCR did read needs a firmer template match than filling a gap
OVERRIDE_MARGIN = 0.08
SYSTEM_START_SP = 6.0        # a number this close to the system's left edge is a bar number
BANK_FILE = Path(__file__).resolve().parent / "data" / "countdigits_bank.npz"
HARVEST_MIN_H_SP = 2.2       # book samples are bold counts, not the small thin digits of camera scans


def _system_left(dets: list[dict], bar: list[float], sp: float) -> float:
    """Left edge of the system the bar is in (leftmost measure box or clef of that staff row)."""
    y0 = bar[1]
    rows = [m["xyxy"] for m in dets if m.get("cls") == "measure" and abs(m["xyxy"][1] - y0) < 3 * sp]
    left = [m[0] for m in rows] + [c["xyxy"][0] for c in dets if str(c.get("cls", "")).startswith("clef")
                                   and c.get("conf", 1.0) >= 0.5 and rows
                                   and rows[0][1] - sp < (c["xyxy"][1] + c["xyxy"][3]) / 2 < rows[0][3] + sp]
    return min(left, default=bar[0])


def read_page(bank: DigitBank, gray: np.ndarray, dets: list[dict], texts: list[dict], rings=()) -> list[dict]:
    """One entry per multi-rest H-bar whose count the OCR did not give or gave differently, with a firm template read.

    ``{"bar", "box", "n", "old", "drop", "add"}``: ``box`` the glyph group in page pixels, ``n`` the count read, ``old`` the OCR
    text it contradicts (or None), ``drop`` the OCR boxes to remove, ``add`` whether to write the box.
    """
    sp = _staff_space(dets)
    if not sp:
        return []
    out = []
    for bar in multirest_bars(dets, 0.5):
        glyphs = find_count_blobs(gray, bar, sp, staff_top_for(dets, bar, sp))
        if not glyphs or not (1 <= len(glyphs) <= MAX_COUNT):
            continue
        box = [min(g[0] for g in glyphs), min(g[1] for g in glyphs), max(g[2] for g in glyphs), max(g[3] for g in glyphs)]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if cx - _system_left(dets, bar, sp) < SYSTEM_START_SP * sp:
            continue
        if any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rings):
            continue
        old = count_text_for(texts, bar, sp)
        scored = [bank.classify(g[4]) for g in glyphs]
        if any(d is None for d, _, _ in scored):
            continue
        n = int("".join(str(d) for d, _, _ in scored))
        if not (MIN_COUNT <= n <= MAX_COUNT_VALUE):
            continue
        firm = all(s >= OVERRIDE_SIM and m >= OVERRIDE_MARGIN for _, s, m in scored)
        rivals = [t for t in engine_counts(texts, bar, sp) if str(t["text"]).strip() != str(n)]
        if old is None and not rivals:
            out.append({"bar": bar, "box": box, "n": n, "old": None, "drop": [], "add": True})
        elif rivals and firm:
            # the OCR gave a different number the engine would take as the count (a misread, or a tempo figure
            # like "= 128" standing over the bar): a firm read of the bold digit wins and the rivals are dropped
            have = any(str(t["text"]).strip() == str(n) for t in engine_counts(texts, bar, sp))
            out.append({"bar": bar, "box": box, "n": n, "old": str(rivals[0]["text"]).strip(), "drop": rivals, "add": not have})
    return out


def read_counts(png: Path, dets_path: Path, texts_path: Path, bank: DigitBank, log=None, rings_fn=None) -> int:
    """Write the counts read from the page image into ``texts_path`` (created if absent); returns how many were added or
    corrected. ``rings_fn(dets)`` lists circled rehearsal numbers (only asked for when something would change)."""
    import cv2
    try:
        dets = json.loads(Path(dets_path).read_text())
        texts = json.loads(Path(texts_path).read_text()) if Path(texts_path).exists() else []
        gray = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    except (OSError, ValueError):
        return 0
    if gray is None:
        return 0
    found = read_page(bank, gray, dets, texts)
    if found and rings_fn:
        found = read_page(bank, gray, dets, texts, rings=rings_fn(dets))
    for f in found:
        if f["drop"]:
            texts = [t for t in texts if not any(t is r or t == r for r in f["drop"])]
        if f["add"]:
            texts.append({"text": str(f["n"]), "xyxy": [float(v) for v in f["box"]], "role": "words", "source": "countdigits"})
        if log:
            log(f"  count digits: {f['n']} over the multi-rest at ({f['bar'][0]:.0f},{f['bar'][1]:.0f})"
                + (f" (OCR read {f['old']})" if f["old"] is not None else " (OCR read nothing)"))
    if found:
        Path(texts_path).write_text(json.dumps(texts))
    return len(found)


def harvest_page(bank: DigitBank, gray: np.ndarray, dets: list[dict], texts: list[dict]) -> int:
    """Add the glyphs of OCR-read counts over this page's multi-rests (bold ones only) to ``bank``."""
    sp = _staff_space(dets)
    if not sp:
        return 0
    n = 0
    for bar in multirest_bars(dets, 0.5):
        t = count_text_for(texts, bar, sp)
        if not t or not (t["xyxy"][3] - t["xyxy"][1]) >= HARVEST_MIN_H_SP * sp:
            continue
        glyphs = find_count_blobs(gray, bar, sp, staff_top_for(dets, bar, sp))
        text = str(t["text"]).strip()
        if len(glyphs) != len(text):
            continue
        for ch, g in zip(text, glyphs):
            bank.add(int(ch), g[4])
            n += 1
    return n


def harvest_dir(bank: DigitBank, pages_dir: Path, limit: int = 400) -> int:
    """Harvest every page directory under ``pages_dir`` (``pNNN/pNNN.{png,texts.json,dets_*.json}``)."""
    import cv2
    n = 0
    for pdir in sorted(Path(pages_dir).glob("p*"))[:limit]:
        stem = pdir.name
        dets = next(iter(pdir.glob(f"{stem}.dets_*.json")), None)
        tj, png = pdir / f"{stem}.texts.json", pdir / f"{stem}.png"
        if not (dets and tj.exists() and png.exists()):
            continue
        try:
            d, t = json.loads(dets.read_text()), json.loads(tj.read_text())
        except (OSError, ValueError):
            continue
        if not multirest_bars(d, 0.5) or not any(_DIGITS.fullmatch(str(x.get("text", "")).strip()) for x in t):
            continue
        gray = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            n += harvest_page(bank, gray, d, [x for x in t if x.get("source") != "countdigits"])
    return n


def default_bank(book_pages: Path | None = None) -> DigitBank:
    """The shipped bank (digits harvested from several books) + synthetic fonts, plus this book's own OCR-read
    counts from ``book_pages`` when given, with mislabelled templates removed."""
    bank = DigitBank.load(BANK_FILE)
    if book_pages is not None:
        harvest_dir(bank, book_pages)
    bank.drop_contradicted()
    return bank


def drop_tempo_digits(texts_path: Path, layer_path: Path, log=None) -> int:
    """Remove digit boxes that are part of a tempo phrase ("Andante sostenuto ♩ = 80") of the page's text layer from
    ``texts_path``: a tall tempo figure over a bar would otherwise be taken as that bar's multi-rest count (80 bars of rest).
    Returns how many were removed."""
    try:
        texts = json.loads(Path(texts_path).read_text())
        layer = json.loads(Path(layer_path).read_text())
    except (OSError, ValueError):
        return 0
    tempi = [p["xyxy"] for p in layer if isinstance(p, dict) and p.get("kind") == "tempo" and "xyxy" in p]
    if not tempi:
        return 0
    keep, dropped = [], 0
    for t in texts:
        if "xyxy" in t and _DIGITS.fullmatch(str(t.get("text", "")).strip()):
            cx, cy = (t["xyxy"][0] + t["xyxy"][2]) / 2, (t["xyxy"][1] + t["xyxy"][3]) / 2
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in tempi):
                dropped += 1
                if log:
                    log(f"  tempo figure {t['text']} is not a multi-rest count")
                continue
        keep.append(t)
    if dropped:
        Path(texts_path).write_text(json.dumps(keep))
    return dropped
