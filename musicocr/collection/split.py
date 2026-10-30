"""Splitting a collection book (many short pieces, several to a page) into one PDF per piece.

The book's table of contents (Inhalt) gives each piece's number, title and composer; the printed page
numbers OCR badly, so each title is instead *found* on the music pages (macOS Vision, matched in
order), which also gives its vertical position. A piece then runs from the top of its heading to the
heading of the next piece, which may be further down the same page: pages are cut at the white band
above that heading and the pieces rebuilt as PDFs of the cropped page images.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from musicocr.assemble.alignment.systems import pdf_page_count
from musicocr.collection.spec import CollectionSpec

OCR_DPI = 150
OUT_DPI = 300
_DOTS = re.compile(r"^[.…·•\s]+$")
_NUM = re.compile(r"^(\d{1,3})\.?$")
_MATCH = 0.78


@dataclass
class Entry:
    num: int
    title: str
    composer: str


@dataclass
class Found:
    num: int
    page: int           # 1-based PDF page
    y: float            # top of the heading (number line), as a fraction of the page height


def render(pdf: Path, page: int, dpi: int, out: Path) -> Path:
    stem = out.with_suffix("")
    subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-gray", "-png",
                    "-singlefile", str(pdf), str(stem)], check=True, capture_output=True)
    return stem.with_suffix(".png")


def ocr_lines(png: Path, tol: float = 0.45) -> List[dict]:
    """OCR words grouped into lines: ``[{"text", "x0", "y0", "y1", "words"}]`` top to bottom."""
    from musicocr.text.ocr_backends import ocr_words
    words = sorted(ocr_words(png, "vision"), key=lambda w: (w["xyxy"][1] + w["xyxy"][3]) / 2)
    lines: List[dict] = []
    for w in words:
        x0, y0, x1, y1 = w["xyxy"]
        for ln in lines:
            if abs((y0 + y1) / 2 - (ln["y0"] + ln["y1"]) / 2) < tol * (ln["y1"] - ln["y0"]):
                ln["words"].append(w)
                ln["y0"], ln["y1"] = min(ln["y0"], y0), max(ln["y1"], y1)
                break
        else:
            lines.append({"y0": y0, "y1": y1, "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["xyxy"][0])
        ln["text"] = " ".join(w["text"] for w in ln["words"])
        ln["x0"] = ln["words"][0]["xyxy"][0]
    return lines


def page_lines(pdf: Path, page: int, dpi: int, tmp: Path, cache: Optional[Path] = None) -> Tuple[List[dict], int, int]:
    """OCR lines and ``(width, height)`` of one page. Vision is slow (~1 s a page), so with ``cache`` the
    result is kept per book (named by file size and mtime, so a replaced scan is read again) and page."""
    path = None
    if cache:
        st = pdf.stat()
        path = cache / "ocr" / f"{pdf.stem}-{st.st_size}-{int(st.st_mtime)}" / f"p{page}-{dpi}.json"
        if path.exists():
            d = json.loads(path.read_text())
            return d["lines"], d["w"], d["h"]
    png = render(pdf, page, dpi, tmp / f"ocr{page}.png")
    w, h = Image.open(png).size
    lines = ocr_lines(png)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"w": w, "h": h, "lines": lines}, ensure_ascii=False))
    return lines, w, h


def _norm(s: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", s.lower())


def toc_entries(pdf: Path, pages: Sequence[int], tmp: Path, cache: Optional[Path] = None) -> List[Entry]:
    """The numbered entries of the table of contents: number, title (dot leaders and page number dropped)
    and composer (the last line of the entry)."""
    entries: List[List] = []
    for pg in pages:
        lines, width, _ = page_lines(pdf, pg, OCR_DPI, tmp, cache)
        for ln in lines:
            ws = [w for w in ln["words"] if not _DOTS.match(w["text"]) and w["xyxy"][0] < 0.8 * width]
            if not ws:
                continue
            m = _NUM.match(ws[0]["text"])
            if m and len(ws) > 1:
                entries.append([int(m.group(1)), [" ".join(w["text"] for w in ws[1:])]])
            elif entries:
                entries[-1][1].append(" ".join(w["text"] for w in ws))
    out = []
    for num, ls in entries:
        title = " ".join(ls[:-1]) if len(ls) > 1 else ls[0]
        out.append(Entry(num, re.sub(r"\s+", " ", title).strip(), ls[-1] if len(ls) > 1 else ""))
    return out


_HEADING = re.compile(r"^(\d{1,3})\.\s+([^\d\s].*)$")


def heading_entries(pdf: Path, first_page: int, last_page: int, tmp: Path,
                    cache: Optional[Path] = None) -> List[Entry]:
    """Entries for a book with no scanned table of contents: the "NN. Title" headings on the music pages,
    taken in order (the number may run ahead of the last one by a few, a heading the OCR missed). The
    composer is printed on the same line and stays part of the title; ``consensus`` over the books smooths
    out misreads."""
    entries: List[Entry] = []
    last = 0
    for pg in range(first_page, last_page + 1):
        lines, width, _ = page_lines(pdf, pg, OCR_DPI, tmp, cache)
        for ln in lines:
            m = _HEADING.match(ln["text"].strip())
            if m and last < int(m.group(1)) <= last + 3 and 0.1 * width < ln["x0"] < 0.7 * width:
                last = int(m.group(1))
                entries.append(Entry(last, m.group(2).strip(), ""))
    return entries


def consensus(tables: Sequence[List[Entry]]) -> List[Entry]:
    """One table from the same TOC read in several books: per number, the most common spelling."""
    nums = sorted({e.num for t in tables for e in t})
    out = []
    for n in nums:
        cands = [e for t in tables for e in t if e.num == n]
        top = Counter((e.title, e.composer) for e in cands).most_common(1)[0][0]
        out.append(Entry(n, *top))
    return out


_NUM_TITLE = re.compile(r"^(\d{1,3})\.\s+\S")


def _heading(lines: List[dict], i: int, e: Entry, width: int, by_title: bool = True) -> Optional[int]:
    """Index of the line (from ``i``) that opens entry ``e``: its printed number alone on a centred line
    or in front of the title, else (``by_title``) a line that starts with its title."""
    want = _norm(e.title)
    for idx in range(i, len(lines)):
        t = lines[idx]["text"].strip()
        m = _NUM.match(t) or _NUM_TITLE.match(t)
        if m and int(m.group(1)) == e.num and 0.1 * width < lines[idx]["x0"] < 0.7 * width:
            return idx
    if not by_title:
        return None
    for idx in range(i, len(lines)):
        for c in (lines[idx]["text"], lines[idx]["text"] + (lines[idx + 1]["text"] if idx + 1 < len(lines) else "")):
            n = _norm(c)
            if len(want) >= 3 and len(n) >= 3 and difflib.SequenceMatcher(None, n[:len(want) + 2], want).ratio() >= _MATCH:
                return idx
    return None


def find_pieces(pdf: Path, entries: Sequence[Entry], first_page: int, last_page: int, tmp: Path,
                log=print, cache: Optional[Path] = None) -> List[Found]:
    """Find each entry's heading on the music pages, in order (an entry that cannot be found is logged
    and skipped, its music then belongs to the piece before it)."""
    found: List[Found] = []
    k = 0                                     # next entry expected
    for pg in range(first_page, last_page + 1):
        if k >= len(entries):
            break
        for dpi in (OCR_DPI, 250):          # Vision now and then reads nothing on a clean page: retry sharper
            lines, w, h = page_lines(pdf, pg, dpi, tmp, cache)
            i, got = 0, False
            while k < len(entries):
                hit, skip = None, 0
                for skip in range(0, 3):          # the expected heading, else the one after it (a misread)
                    if k + skip < len(entries):
                        hit = _heading(lines, i, entries[k + skip], w, by_title=skip == 0)
                    if hit is not None:
                        break
                if hit is None:
                    break
                for e in entries[k:k + skip]:
                    log(f"  piece {e.num} {e.title!r}: heading not found")
                e = entries[k + skip]
                top = lines[hit]["y0"]
                if not _NUM.match(lines[hit]["text"].strip()) and hit and _NUM.match(lines[hit - 1]["text"].strip()) \
                        and top - lines[hit - 1]["y0"] < 0.12 * h:
                    top = lines[hit - 1]["y0"]          # "4." printed on a line of its own above the title
                found.append(Found(e.num, pg, top / h))
                i, k, got = hit + 1, k + skip + 1, True
            if got or len(lines) > 12:
                break
    for e in entries[k:]:
        log(f"  piece {e.num} {e.title!r}: heading not found")
    return found


def _cut(gray: Image.Image, y: int, dpi: int) -> int:
    """Row to cut at just above a heading at row ``y``: in the white band above it, or above a lone
    piece number printed over the title ("7.") when there is one."""
    import numpy as np
    window = int(0.9 * dpi)
    top = max(0, y - window)
    ink = np.asarray(gray.crop((0, top, gray.width, y))) < 160
    rows = ink.any(axis=1)
    r = len(rows) - 1
    while r >= 0 and not rows[r]:                 # the gap between the heading and what is above it
        r -= 1
    gap_below = r + 1
    if r < 0:
        return top + len(rows) // 2 if len(rows) else y
    end = r                                       # last row of the block above the heading
    blank = 0
    while r >= 0 and blank < max(4, dpi // 40):   # walk up the block until a clear white band
        blank = blank + 1 if not rows[r] else 0
        r -= 1
    start = r + blank + 1                         # first row of the block
    cols = ink[start:end + 1].any(axis=0)
    xs = np.flatnonzero(cols)
    lone_number = (end - start) <= 0.25 * dpi and len(xs) and (xs[-1] - xs[0]) <= 0.12 * gray.width
    if lone_number:                               # skip it: cut in the band above the number
        q = start - 1
        while q >= 0 and not rows[q]:
            q -= 1
        return top + (q + 1 + start) // 2
    return top + (end + 1 + gap_below) // 2


def build_piece_pdfs(pdf: Path, found: Sequence[Found], last_page: int, out_dir: Path, prefix: str,
                     tmp: Path) -> Dict[int, Path]:
    """One PDF per found piece (cropped page images at ``OUT_DPI``); ``{piece number: pdf}``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache: Dict[int, Image.Image] = {}

    def page(p: int) -> Image.Image:
        if p not in cache:
            cache.clear()
            cache[p] = Image.open(render(pdf, p, OUT_DPI, tmp / f"hi{p}.png")).convert("L")
        return cache[p]

    cuts: Dict[Tuple[int, int], int] = {}      # (piece index, page) -> row of the cut above its heading
    for i, f in enumerate(found):
        im = page(f.page)
        y = int(f.y * im.height)
        cuts[(i, f.page)] = 0 if f.y < 0.12 else _cut(im, y, OUT_DPI)
    result: Dict[int, Path] = {}
    for i, f in enumerate(found):
        nxt = found[i + 1] if i + 1 < len(found) else None
        end_page = nxt.page if nxt else last_page
        images = []
        for p in range(f.page, end_page + 1):
            im = page(p)
            top = cuts[(i, f.page)] if p == f.page else 0
            bottom = im.height
            if nxt and p == nxt.page:
                bottom = cuts[(i + 1, p)]
                if bottom <= (cuts[(i, p)] if p == f.page else 0) + 0.1 * OUT_DPI:
                    continue                   # the next heading sits at the top: nothing of this piece here
            images.append(im.crop((0, top, im.width, bottom)))
        if not images:                         # its heading and the next share a spot: keep a strip, not nothing
            images = [page(f.page).crop((0, cuts[(i, f.page)], page(f.page).width, page(f.page).height))]
        path = out_dir / f"{prefix}_p{f.num:02d}.pdf"
        images[0].save(path, "PDF", resolution=OUT_DPI, save_all=True, append_images=images[1:])
        result[f.num] = path
    return result


def split_collection(spec: CollectionSpec, toc_pages: Optional[Tuple[int, int]] = None,
                     first_page: Optional[int] = None, log=print) -> Path:
    """Cut every book of the collection into per-piece PDFs; returns the ``pieces.json`` written.

    The pieces are the table-of-contents entries the books agree on (``consensus``); each book's
    copy of a piece goes to ``<input_dir>/<id prefix><book id>/``.
    """
    a, b = toc_pages or spec.toc_pages
    first = first_page or spec.first_page
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        if spec.headings:
            tables = [heading_entries(book.pdf, first, pdf_page_count(str(book.pdf)), tmp, spec.cache_dir) for book in spec.books]
        else:
            tables = [toc_entries(book.pdf, range(a, b + 1), tmp, spec.cache_dir) for book in spec.books]
        for book, tab in zip(spec.books, tables):
            log(f"book {book.id}: {len(tab)} TOC entries")
        entries = consensus(tables)
        log(f"{len(entries)} pieces in the consensus table")
        table = {"entries": [asdict(e) for e in entries], "found": {}, "pdfs": {}}
        for book in spec.books:
            n = pdf_page_count(str(book.pdf))
            found = find_pieces(book.pdf, entries, first, n, tmp, log=lambda s, i=book.id: log(f"book {i}{s}"),
                                cache=spec.cache_dir)
            log(f"book {book.id}: found {len(found)}/{len(entries)}")
            table["found"][book.id] = [asdict(f) for f in found]
            pieces = build_piece_pdfs(book.pdf, found, n, spec.book_dir(book), spec.book_prefix(book), tmp)
            table["pdfs"][book.id] = {str(k): str(v) for k, v in pieces.items()}
        spec.pieces_json.parent.mkdir(parents=True, exist_ok=True)
        spec.pieces_json.write_text(json.dumps(table, indent=1, ensure_ascii=False))
    return spec.pieces_json
