"""Automatic alignment: derive each part's bar structure from the part PDF itself.

Sources of truth, per part:

* the printed bar number at the start of every system (``pdftotext -bbox``,
  left margin),
* the printed multi-rest counts (large digits above a multi-rest bar),
* homr's per-page MusicXML, whose ``<print new-system>`` marks say how many
  measures it produced for each system.

For a system with B printed bars and multi-rests c_1..c_m, homr should have
produced ``B - sum(c_i - 1)`` measures.  When it did, the counts are assigned to
rest-only measures and every measure gets its printed bar number.  Otherwise the
common repairs are applied and *reported*: stray trailing measures dropped, a
lost multi-rest re-inserted, a multi-rest misread as notes replaced by rests.
Anything else is padded with rests and flagged.

Spec interface (in addition to the common attributes in
:mod:`musicocr.assemble.alignment.spec`):

* ``PARTS``: ``{part_id: dict(name, abbr, key, program, clef, pdf, dir)}`` where
  ``clef`` is ``"treble"`` or ``"bass"``, ``pdf`` is the part PDF and ``dir``
  holds homr's per-page output.
* ``key_fifths(bar) -> int`` key signature (fifths) in force at ``bar``.
* optional ``PAGE_XML``: per-page file pattern, default
  ``"{dir}/{key}.p{page:03d}.musicxml"`` (fields ``dir``, ``key``, ``page``).
* optional ``IGNORE_NUMBERS``: numbers in the PDF text layer to ignore when
  looking for multi-rest counts (e.g. tempo values), default none.
* optional ``FIRST_BAR_BEATS`` (pickup length) and ``MARKS``.
"""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from . import AlignmentError
from .build import (add_attributes, build_score, check_length, copy_notes,
                    is_rest_only, measure_total, rest_measure)
from .spec import DIV, bar_len

# Page geometry (PDF points) used to read the text layer.
_LEFT_MARGIN_X = 70
_TOP_Y = 80
_COUNT_MIN_HEIGHT = 25      # multi-rest numerals are large
_SYSTEM_Y_PAD = 45          # a system's multi-rest numerals sit this far above its first row
_X_ORIGIN, _X_SPAN = 62, 500

Word = Tuple[float, float, float, float, str]
Count = Tuple[float, int]            # (x, bar count)
Source = Tuple[ET.Element, int]      # (homr measure, its <divisions>)
Unit = Tuple[int, int]               # (index into all measures, <multiple-rest> tag or 0)
Plan = List[Tuple[int, int, Optional[Source], bool]]   # (first_bar, last_bar, source, is_multirest)


def pdf_words(pdf: str, page: int, pdftotext: str = "pdftotext") -> List[Word]:
    """Words with bounding boxes ``(xmin, ymin, xmax, ymax, text)`` via ``pdftotext -bbox``."""
    t = subprocess.run([pdftotext, "-bbox", "-f", str(page), "-l", str(page), pdf, "-"],
                       capture_output=True, text=True).stdout
    return [(float(a), float(b), float(c), float(d), w) for a, b, c, d, w in
            re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', t)]


_OCR_DPI = 150
_NUM_ZONE_PT = 60        # width of the strip above a staff's left end that is searched for the bar number
_LEFT_REF_PT = 50        # scans: staff-left is reported at this x so page_text_layout's left-margin test holds


def _staff_systems(img) -> List[Tuple[int, int, int]]:
    """(top_row, bottom_row, left_col) of each 5-line staff on a greyscale page image (PIL 'L').

    Staff lines are the rows that are dark across a band just right of where staves start (clef and
    key signature there, so no stems or notes break the lines).
    """
    import numpy as np
    a = np.asarray(img) < 200
    h, w = a.shape
    band = a[:, int(w * .21):int(w * .45)].mean(axis=1)
    lines: List[List[int]] = []
    for y in np.flatnonzero(band > 0.4):
        if lines and y - lines[-1][-1] <= 2:
            lines[-1].append(int(y))
        else:
            lines.append([int(y)])
    ys = [sum(g) // len(g) for g in lines]
    out: List[Tuple[int, int, int]] = []
    starts: List[List[int]] = []                  # per staff: where runs of staff-line columns >= 8 px wide begin
    i = 0
    while i + 4 < len(ys):
        gaps = [ys[i + j + 1] - ys[i + j] for j in range(4)]
        if max(gaps) <= 1.5 * min(gaps) + 1:                             # evenly spaced: one staff
            top, bot = ys[i], ys[i + 4]
            hit = np.mean([a[max(y - 1, 0):y + 2, :int(w * .5)].any(axis=0) for y in ys[i:i + 5]], axis=0) >= 0.6
            run = np.convolve(hit, np.ones(12), mode="valid") >= 10        # first dense run of staff lines (a clef may hide some)
            xs = np.flatnonzero(run)
            reach = int(w * .25)                  # the staff's own lines carry on for a good way; a spiral binding's blobs do not
            real = [x for x in xs if hit[x:x + reach].mean() >= .8]
            xs = real or xs
            out.append((top, bot, int(xs[0]) if len(xs) else int(w * .15)))
            starts.append([int(x) for x in np.flatnonzero(hit[1:] & ~hit[:-1]) + 1
                           if x > .06 * w and hit[x:x + 8].all()])
            i += 5
        else:
            i += 1
    common = _common_left(starts, w)
    return [(t, b, common if common is not None else left) for t, b, left in out]


def _levelled(img):
    """(page image, its staves): the page as scanned or rotated by an estimated skew, whichever yields more staves.

    A camera scan's staves slope, which smears the row projection ``_staff_systems`` relies on and loses
    whole systems (often the first on a page, so the page's start bar goes unread). Rotating by the page's
    skew -- measured with and without the binding's margin, which skews the estimate -- finds them; but on a
    warped page it can also lose some, so every candidate is tried and the one with the most staves wins.
    """
    from PIL import Image
    from musicocr.preprocess import _estimate_skew
    best = (img, _staff_systems(img))
    w = img.width
    for src in (img, img.crop((int(w * .12), 0, w, img.height))):
        try:
            angle = _estimate_skew(src, 5.0)
        except Exception:  # noqa: BLE001
            continue
        if abs(angle) >= 0.2:
            rot = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            staves = _staff_systems(rot)
            if len(staves) > len(best[1]):
                best = (rot, staves)
    return best


def _common_left(starts: List[List[int]], width: int) -> Optional[int]:
    """The column where most staves of a page start (their start barline), else None.

    A camera scan of a bound book is warped: the left end found from each staff's own lines is unreliable
    (a spiral binding's blobs, lines fading near the margin), but the systems all begin at one x.
    """
    if len(starts) < 3:
        return None
    best = max((sum(1 for st in starts if any(abs(x - c) <= 12 for x in st)), -c, c) for st in starts for c in st)
    return best[2] if best[0] >= max(3, 0.4 * len(starts)) else None


_RANGE_TOKEN = re.compile(r"\D{0,2}(\d{1,3})\s*[-\u2013\u2014_]\s*(\d{1,3})\D{0,2}")   # OCR adds stray marks ("130-133\u0142")
_OCR_CACHE_VERSION = "v18"   # bump when the strip geometry / token handling changes: cached OCR words are stale
_START_MAX_DY_SP = 3.0   # a system's first bar number sits right under its staff; digits lower down are tempo marks etc.
_MAX_RANGE = 60       # longest multi-rest read as a range; "1-123" is two numbers glued together


def _strip_tokens(img, path, box, ranges: bool = False):
    """OCR one strip of the page image: [(x0, y0, x1, y1, text, range_match)] in image pixels, left to right.

    Digit words only, plus (with ``ranges``) printed multi-rest ranges such as "1-6".
    """
    from PIL import Image
    x0, y0, x1, y1 = box
    crop = img.crop((x0, y0, x1, y1))
    if crop.width < 4 or crop.height < 4:
        return []
    toks = _ocr_tokens(crop, path, x0, y0, Image.LANCZOS, ranges)
    if ranges:
        # the same strip under another resampling filter: Vision drops a range label ("130-133")
        # under one and finds it under the other, so keep the ranges the first pass lacks
        for t in _ocr_tokens(crop, path, x0, y0, Image.BICUBIC, ranges):
            if t[5] and not any(o[0] < t[2] and t[0] < o[2] and o[1] < t[3] and t[1] < o[3] for o in toks):
                toks.append(t)
    return sorted(toks)


def _ocr_tokens(crop, path, x0, y0, resample, ranges):
    """Vision OCR of ``crop`` upscaled 3x with ``resample``: (x0, y0, x1, y1, text, range_match) tuples in page-image pixels."""
    from musicocr.text.ocr_backends import ocr_words
    crop = crop.resize((crop.width * 3, crop.height * 3), resample)
    crop.save(path)
    toks = []
    for w in ocr_words(path, "vision"):
        t = w["text"].strip()
        bx0, by0, bx1, by1 = (v / 3 for v in w["xyxy"])
        rng = _RANGE_TOKEN.fullmatch(t)
        if rng and not 0 < int(rng.group(2)) - int(rng.group(1)) <= _MAX_RANGE:
            rng = None
        if (t.isdigit() and len(t) <= 3) or (ranges and rng):
            toks.append((bx0 + x0, by0 + y0, bx1 + x0, by1 + y0, t, rng))
    return toks


def _tight_number(img, path, box) -> List[tuple]:
    """The bar number in a strip when whole-strip OCR found none: the topmost blob of ink at the strip's left
    (the number is printed above everything else there) cut out on its own and read at a few scales.

    On low-resolution scans a lone small italic "5" or "11" is not read from the strip as a whole: the top of
    the clef and the ends of slurs that reach into it throw Vision off.
    """
    import numpy as np
    from PIL import Image, ImageOps
    from musicocr.text.ocr_backends import ocr_words
    x0, y0, x1, y1 = box
    crop = img.crop((x0, y0, x1, y1))
    ink = np.asarray(crop) < 150
    rows = np.flatnonzero(ink[:, :max(int(crop.width * 0.6), 1)].any(axis=1))
    if not len(rows):
        return []
    top = rows[0]
    end = top
    for r in rows:                              # the first run of ink rows, allowing a 2-row gap
        if r - end > 2:
            break
        end = r
    if end - top < 5 or end - top > 0.45 * crop.height:
        return []
    cols = np.flatnonzero(ink[top:end + 1].any(axis=0))
    if not len(cols):
        return []
    c0, c1 = cols[0], cols[0]
    for c in cols:                              # one run of columns: the digits, not a slur further right
        if c - c1 > max(3, (end - top) // 2):
            break
        c1 = c
    band = crop.crop((max(c0 - 4, 0), max(top - 3, 0), c1 + 5, end + 4))
    for scale, resample in ((5, Image.LANCZOS), (8, Image.LANCZOS), (3, Image.BICUBIC)):
        big = ImageOps.expand(band.resize((band.width * scale, band.height * scale), resample), border=60, fill=255)
        big.save(path)
        for w in ocr_words(path, "vision"):
            t = w["text"].strip()
            if t.isdigit() and len(t) <= 3:
                return [(x0 + c0, y0 + top, x0 + c1 + 1, y0 + end + 1, t, None)]
    return []


def _strip_numbers(img, path, box, left: int, k: float) -> List[Word]:
    """Digit words from a strip above a staff, in PDF points, x shifted so the staff's left end is at ``_LEFT_REF_PT``."""
    toks = [t for t in _strip_tokens(img, path, box) if t[4].isdigit()]
    tight = _tight_number(img, path, box)
    if len(toks) <= 1:          # a lone number is read two ways; _increasing_by_y keeps the one the page's sequence allows ("68" read as "89")
        toks += [t for t in tight if not any(o[4] == t[4] for o in toks)]
    return [((x0 - left) * k + _LEFT_REF_PT, y0 * k, (x1 - left) * k + _LEFT_REF_PT, y1 * k, t)
            for x0, y0, x1, y1, t, _ in toks]


def _start_candidates(toks: List[tuple], top: float, bot: float) -> List[tuple]:
    """The strip tokens that can be a system's first bar number: ranges, and digits right under the staff.

    Digits further down are something else -- a tempo mark ("\u2669 = 104") printed between systems read
    as bar 10 and threw a whole page's numbering off (HTTYD2 5M38-39A).
    """
    space = max((bot - top) / 4, 4)
    return [t for t in toks if t[5] or (t[1] - bot) / space <= _START_MAX_DY_SP]


def _pick_system_starts(cands: List[List[tuple]]) -> List[Optional[tuple]]:
    """One token per staff (or None) so the first-bar numbers strictly increase down the page.

    Under-the-staff strips also hold tuplet digits and misreads, so the leftmost token is not always
    the system's first bar. Maximises the number of staves given a number, then prefers leftmost.
    """
    val = lambda t: int((t[5].group(1) if t[5] else t[4]))
    state: dict = {0: ((0, 0), [])}          # last bar number taken -> ((staves covered, -leftness), choice per staff)
    for toks in cands:
        nxt = {v: (sc, ch + [None]) for v, (sc, ch) in state.items()}      # skip this staff
        for v, (sc, ch) in state.items():
            for rank, t in enumerate(toks[:4]):
                if val(t) > v:
                    cand = ((sc[0] + 1, sc[1] - rank), ch + [t])
                    if val(t) not in nxt or cand[0] > nxt[val(t)][0]:
                        nxt[val(t)] = cand
        state = nxt
    return max(state.values(), key=lambda x: x[0])[1]


def pdf_words_or_ocr(pdf: str, page: int, pdftotext: str = "pdftotext") -> List[Word]:
    """``pdf_words``, falling back to OCR of the printed bar numbers when the page has no text layer.

    For a scan, each staff's left end is found by line projection and a strip beside it is OCR'd on its
    own, upscaled (whole-page OCR misses most of these small italic numerals). Engravers print the
    system's first bar number either above the staff's left end or, with every bar numbered, below the
    staff; both zones are read and the one that yields more system starts is used. Digit words are
    returned with y in PDF points and x shifted so the system start sits at ``_LEFT_REF_PT``
    (page_text_layout wants bar numbers in the left margin; scans' margins vary), plus the printed
    multi-rest ranges ("1-6", as en-dash words) from the below-staff strips. Cached per PDF/page.
    """
    words = pdf_words(pdf, page, pdftotext)
    if words:
        return words
    import hashlib
    import json
    import tempfile
    from pathlib import Path
    from PIL import Image
    from musicocr.text.ocr_backends import _CACHE
    st = Path(pdf).stat()
    key = hashlib.sha1(f"{_OCR_CACHE_VERSION}|{Path(pdf).resolve()}|{st.st_size}|{st.st_mtime_ns}|{page}|{_OCR_DPI}".encode()).hexdigest()[:16]
    cache = _CACHE / "pdf-ocr" / f"{key}.json"
    if cache.exists():
        return _below_header([tuple(w) for w in json.loads(cache.read_text())])
    k = 72.0 / _OCR_DPI
    above: List[Word] = []
    below: List[Word] = []
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "pg"
        subprocess.run(["pdftoppm", "-r", str(_OCR_DPI), "-f", str(page), "-l", str(page), "-gray", "-png",
                        "-singlefile", pdf, str(stem)], capture_output=True)
        png = stem.with_suffix(".png")
        if png.exists():
            img, staves = _levelled(Image.open(png).convert("L"))
            lower: List[List[tuple]] = []
            for n, (top, bot, left) in enumerate(staves):
                space = max((bot - top) / 4, 4)
                x0, x1 = max(left - 40, 0), left + int(_NUM_ZONE_PT / k)
                y0, y1 = max(int(top - 7 * space), 0), max(int(top - 0.3 * space), 1)
                above += _strip_numbers(img, Path(tmp) / f"a{n}.png", (x0, y0, x1, y1), left, k)
                nxt = staves[n + 1][0] - space if n + 1 < len(staves) else img.height
                y0, y1 = int(bot + 0.8 * space), int(min(bot + 6 * space, nxt))
                lower.append(_strip_tokens(img, Path(tmp) / f"b{n}.png", (x0, y0, int(img.width * .97), y1), ranges=True))
            near = [_start_candidates(toks, top, bot) for (top, bot, _), toks in zip(staves, lower)]
            for (top, bot, left), toks, pick in zip(staves, lower, _pick_system_starts(near)):
                for t in toks:
                    if t[5]:                                 # a printed multi-rest range, at its own place
                        below.append((t[0] * k, t[1] * k, t[2] * k, t[3] * k, f"{t[5].group(1)}\u2013{t[5].group(2)}"))
                for t in toks:                               # every other bar number, "#12": ignored by the layout, read by printed_max_bar
                    if t is not pick and t[4].isdigit():
                        below.append((t[0] * k, t[1] * k, t[2] * k, t[3] * k, f"#{t[4]}"))
                if pick is not None:                         # the system's first bar number, in the left margin
                    first = pick[5].group(1) if pick[5] else pick[4]
                    below.append((_LEFT_REF_PT, pick[1] * k, _LEFT_REF_PT + (pick[2] - pick[0]) * k, pick[3] * k, first))
    ab = _increasing_by_y(above)
    n_starts = lambda ws: sum(1 for w in ws if w[4].isdigit())
    out = below if n_starts(below) > n_starts(ab) else ab
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return _below_header(out)


def _below_header(words: List[Word]) -> List[Word]:
    """OCR'd bar numbers only come from strips beside a staff, never from a running header, so a
    number high on the page is a first system's: move it just under the text layer's header cut-off
    (``page_text_layout`` drops digits above ``_TOP_Y``, the page number of a born-digital PDF)."""
    out = []
    for x0, y0, x1, y1, w in words:
        if w.isdigit() and y0 <= _TOP_Y:
            y0, y1 = _TOP_Y + 1, _TOP_Y + 1 + (y1 - y0)
        out.append((x0, y0, x1, y1, w))
    return out


def _increasing_by_y(words: List[Word]) -> List[Word]:
    """Keep the longest top-to-bottom run of bar numbers that strictly increases (drops OCR misreads)."""
    ws = sorted(words, key=lambda w: w[1])
    best: List[List[Word]] = []
    for i, w in enumerate(ws):
        chain = [w]
        for j in range(i):
            if int(ws[j][4]) < int(w[4]) and len(best[j]) + 1 > len(chain):
                chain = best[j] + [w]
        best.append(chain)
    return max(best, key=len, default=[])


def pdf_page_count(pdf: str) -> int:
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    if not m:
        raise AlignmentError(f"cannot read page count of {pdf} (is poppler's pdfinfo installed?)")
    return int(m.group(1))


def page_text_layout(words: List[Word], ignore: Tuple[int, ...] = ()) -> Tuple[List[Tuple[float, int]],
                                                                               List[Tuple[float, float, int]]]:
    """-> (system_starts [(y, bar_no)], multirest_counts [(x, y, count)]) for one page."""
    starts = sorted((y0, int(w)) for x0, y0, x1, y1, w in words
                    if w.isdigit() and x0 < _LEFT_MARGIN_X and y0 > _TOP_Y and (y1 - y0) < 20)
    cand = [(x0, y0, y1, w) for x0, y0, x1, y1, w in words
            if w.isdigit() and x0 >= _LEFT_MARGIN_X and y0 > _TOP_Y]
    tall = [c for c in cand if c[2] - c[1] >= _COUNT_MIN_HEIGHT]
    # two stacked tall numerals are one two-digit time signature, not counts
    time_sig = {c for a in tall for b in tall for c in (a, b)
                if a is not b and abs(a[0] - b[0]) < 6 and 0 < b[1] - a[1] < 14}
    counts = [(x, y, int(w)) for x, y, y1, w in cand
              if (x, y, y1, w) not in time_sig and y1 - y >= _COUNT_MIN_HEIGHT
              and int(w) not in ignore]
    return starts, counts


def systems_of(spec: Any, pid: Any) -> Tuple[List[Dict[str, Any]], List[Source]]:
    """-> (systems, all_measures).

    Each system is ``dict(page, idx, start, B, units=[Unit], counts=[(x, count)])``.
    """
    P = spec.PARTS[pid]
    pdf, d, key = P["pdf"], P["dir"], P["key"]
    pattern = getattr(spec, "PAGE_XML", "{dir}/{key}.p{page:03d}.musicxml")
    ignore = tuple(getattr(spec, "IGNORE_NUMBERS", ()))
    systems: List[Dict[str, Any]] = []
    allm: List[Source] = []
    offset = 0
    for pg in range(1, pdf_page_count(pdf) + 1):
        starts, counts = page_text_layout(pdf_words(pdf, pg), ignore)
        page = ET.parse(pattern.format(dir=d, key=key, page=pg)).getroot()
        ms = page.find("part").findall("measure")
        groups: List[List[Unit]] = [[]]
        div = 1
        for i, m in enumerate(ms):
            dv = m.find("attributes/divisions")
            if dv is not None:
                div = int(dv.text)
            allm.append((m, div))
            p = m.find("print")
            if p is not None and p.get("new-system") == "yes" and groups[-1]:
                groups.append([])
            mr = m.find(".//multiple-rest")
            groups[-1].append((offset + i, int(mr.text) if mr is not None else 0))
        ys, sn = [y for y, _ in starts], [n for _, n in starts]
        if pg == 1 and len(groups) == len(sn) + 1:
            ys, sn = [0] + ys, [1] + sn
        if len(groups) != len(sn):
            raise AlignmentError(f"{key} page {pg}: {len(groups)} homr systems "
                                 f"but {len(sn)} printed system numbers")
        for gi, g in enumerate(groups):
            lo = ys[gi] - _SYSTEM_Y_PAD if gi else -1
            hi = ys[gi + 1] - _SYSTEM_Y_PAD if gi + 1 < len(ys) else 1e9
            systems.append(dict(page=pg, idx=gi, start=sn[gi], units=g,
                                counts=sorted((x, c) for x, y, c in counts if lo <= y < hi)))
        offset += len(ms)
    for i, s in enumerate(systems):
        nxt = systems[i + 1]["start"] if i + 1 < len(systems) else spec.TOTAL_BARS + 1
        s["B"] = nxt - s["start"]
    return systems, allm


def _norm_total(src: Source) -> float:
    """A measure's duration normalised to ``DIV`` divisions."""
    return measure_total(src[0]) * DIV / src[1]


def _assign_multirests(units: List[Unit], counts: List[Count], M: List[Source],
                       e: int, tag: str, report: List[str]) -> List[int]:
    """Choose which unit indices carry the system's multi-rest counts (sorted ascending)."""
    def is_r(k: int) -> bool:
        return is_rest_only(M[units[k][0]][0])

    weights = [1.3 if is_r(k) else 1.0 for k in range(len(units))]
    tot = sum(weights) or 1
    centre = [(sum(weights[:k]) + weights[k] / 2) / tot for k in range(len(units))]
    cand = [k for k in range(len(units)) if is_r(k)]
    chosen = [k for k in cand if units[k][1]][:len(counts)]
    n_tagged = len(chosen)
    pool = [k for k in cand if k not in chosen]
    for x, c in sorted(counts, key=lambda t: -t[0]):   # untagged counts: nearest rest-only unit by x
        if len(chosen) >= len(counts) or not pool:
            break
        fr = (x - _X_ORIGIN) / _X_SPAN
        k = min(pool, key=lambda q: abs(centre[q] - fr))
        pool.remove(k)
        chosen.append(k)
    if counts and len(cand) > len(counts) and n_tagged < len(counts):
        report.append(f"  {tag}: {len(cand)} rest-only measures for {len(counts)} multi-rest(s) -- "
                      f"picked by position on the page, check")
    if len(chosen) < len(counts) and e == 0:           # multi-rest misread as a note: pick the odd one out
        notes = [k for k in range(len(units)) if k not in chosen and not is_r(k)]
        durs = sorted(_norm_total(M[units[k][0]]) for k in notes)
        med = durs[len(durs) // 2] if durs else 0
        for x, c in sorted(counts, key=lambda t: -t[0])[:len(counts) - len(chosen)]:
            if not notes:
                break
            k = max(notes, key=lambda q: abs(_norm_total(M[units[q][0]]) - med))
            notes.remove(k)
            chosen.append(k)
            report.append(f"  {tag}: {c}-bar multi-rest was read as notes "
                          f"(homr measure {units[k][0] + 1}) -> replaced by rests")
    return sorted(chosen)


def plan_system(s: Dict[str, Any], M: List[Source], report: List[str]) -> Plan:
    """Plan for one system: bar ranges with their homr source measure (None = synthesised rest)."""
    tag = f"p{s['page']}s{s['idx']} (bar {s['start']})"
    units, counts = list(s["units"]), list(s["counts"])
    e = len(units) - (s["B"] - sum(c - 1 for _, c in counts))
    if e > 0:
        dropped, units = units[-e:], units[:-e]
        report.append(f"  {tag}: homr had {e} extra trailing measure(s) -> dropped "
                      f"(homr measure {[u[0] + 1 for u in dropped]})")
    chosen = _assign_multirests(units, counts, M, e, tag, report)
    slots = {k: c for k, (_, c) in zip(chosen, sorted(counts))}
    missing = sorted(counts)[len(chosen):]
    seq: List[Tuple[Optional[Unit], Optional[int]]] = [(u, slots.get(k)) for k, u in enumerate(units)]
    if e < 0:
        if len(missing) == -e:
            for x, c in missing:       # re-insert lost multi-rest(s), position by x estimate
                pos = min(len(seq), max(0, round((x - _X_ORIGIN) / _X_SPAN * (len(seq) + 1) - 0.5)))
                seq.insert(pos, (None, c))
                report.append(f"  {tag}: homr lost a {c}-bar multi-rest -> re-inserted after "
                              f"unit {pos} (position estimated, check)")
        else:
            report.append(f"  {tag}: homr has {-e} measure(s) too few and no multi-rest to blame "
                          f"-- NEEDS MANUAL FIX; padding with rests")
            seq += [(None, 1)] * (-e)
    plan: Plan = []
    bar = s["start"]
    for u, c in seq:
        span = c if c else 1
        plan.append((bar, bar + span - 1, None if u is None else M[u[0]], c is not None))
        bar += span
    if bar != s["start"] + s["B"]:
        report.append(f"  {tag}: !! bars {s['start']}..{bar - 1} but expected {s['B']} bars -- mismatch")
    return plan


def plan_part(spec: Any, pid: Any, report: List[str]) -> Plan:
    """Plan covering bars 1..TOTAL_BARS for part ``pid``."""
    systems, M = systems_of(spec, pid)
    out: Plan = []
    for s in systems:
        out.extend(plan_system(s, M, report))
    return out


def _clef_of(src: Optional[ET.Element]) -> Optional[Tuple[str, str]]:
    el = src.find("attributes/clef") if src is not None else None
    return (el.findtext("sign"), el.findtext("line")) if el is not None else None


def build_part(spec: Any, pid: Any, report: List[str]) -> List[ET.Element]:
    """Measures for one part aligned to printed bars; problems are appended to ``report``."""
    P = spec.PARTS[pid]
    default_clef = ("G", "2") if P["clef"] == "treble" else ("F", "4")
    cur_clef = None
    prev_meter = prev_key = None
    out: List[ET.Element] = []
    for a, b, srcd, is_mr in plan_part(spec, pid, report):
        src, sdiv = srcd if srcd is not None else (None, 1)
        for bar in range(a, b + 1):
            m = ET.Element("measure", number=str(bar))
            rest = is_mr or src is None or is_rest_only(src)
            clef = _clef_of(src)
            if cur_clef is None:
                want = clef or default_clef
            else:
                want = clef if (clef and clef != cur_clef) else None
            first = not out
            meter, key = spec.meter(bar), spec.key_fifths(bar)
            add_attributes(
                m,
                divisions=DIV if first else None,
                key=key if (first or key != prev_key) else None,
                meter=meter if (first or meter != prev_meter) else None,
                clef=want)
            if want:
                cur_clef = want
            length = bar_len(spec, bar)
            if rest:
                full = length == meter[0] * DIV * 4 // meter[1]
                m.append(rest_measure(bar, length, full).find("note"))
            else:
                copy_notes(src, m, sdiv)
                bad = check_length(m, spec, bar)
                if bad:
                    report.append(f"  bar {bar}: LENGTH {bad[0]:g} beats, expected {bad[1]:g}")
            prev_meter, prev_key = meter, key
            out.append(m)
    return out


def combine(spec: Any) -> Tuple[ET.Element, List[str]]:
    """Build the combined score for every part in ``spec.PARTS``; returns (score, report lines)."""
    report: List[str] = []
    parts, infos = {}, {}
    for pid, P in spec.PARTS.items():
        infos[pid] = dict(name=P["name"], abbr=P["abbr"], program=P["program"])
        report.append(f"part {pid} ({P['name']}):")
        parts[pid] = build_part(spec, pid, report)
    return build_score(spec, parts, infos), report
