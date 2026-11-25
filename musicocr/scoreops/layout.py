"""Give a converted ``.mscz`` the line and page layout of the original.

The OMR reads page by page and marks which bar opens each system (``<print new-system="yes">``), so
the original's bars per line and lines per page are known. This module writes them into the score as
line and page breaks, sets the page size, margins and staff size from the original page, then has
MuseScore lay the score out and corrects what still differs:

* a line MuseScore cannot fit (it wraps, stranding bars on an extra line) gets a lower layout
  stretch on its bars; if many lines wrap, or one still does at the lowest stretch, the staves are
  made smaller instead;
* a page that overflows gets tighter system spacing, then smaller staves.

Only breaks, per-bar stretch and the page style are changed, so notes and hand edits are kept.
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree as ET

from musicocr.scoreops.movements import system_starts
from musicocr.util import run_cmd_lenient

_DPI = 200
_STRETCH_STEPS = (1.0, 0.85, 0.72, 0.6, 0.5)
_SHRINK = 0.95
_MIN_SIZE = 0.75           # never below this fraction of the original's staff size
_MANY_WRAPPED = 0.25       # this share of lines wrapping means the staves are too big
_SPACINGS = (None, (6.5, 12.0), (5.0, 10.0))   # (min, max) system distance, in staff spaces
_MAX_ROUNDS = 20
_TOP_BOTTOM_MM = 10.0


class LayoutError(Exception):
    pass


@dataclass
class Target:
    """The original's layout in bar indices."""
    bars: int
    starts: List[int]               # first bar of each system
    page_of: List[int]              # 0-based output page of each system

    @property
    def pages(self) -> int:
        return self.page_of[-1] + 1

    def end(self, i: int) -> int:
        return (self.starts[i + 1] if i + 1 < len(self.starts) else self.bars) - 1

    def line_of(self, bar: int) -> int:
        return max(i for i, s in enumerate(self.starts) if s <= bar)


@dataclass
class PageStyle:
    width_mm: float
    height_mm: float
    side_mm: float
    spatium_mm: float


@dataclass
class Result:
    systems: int
    pages: int
    spatium_mm: float
    squeezed: Dict[int, float] = field(default_factory=dict)    # line -> stretch
    wrapped: List[int] = field(default_factory=list)            # lines still not fitting
    moved: List[int] = field(default_factory=list)              # lines on a different page

    @property
    def matches(self) -> bool:
        return not self.wrapped and not self.moved


# --- the original ----------------------------------------------------------------

def target_from_pages(page_xmls: Sequence[Path]) -> Target:
    """``page_xmls``: the per-page MusicXML in order; files of one page share its ``.pNNN`` name."""
    bars, starts, page_of, pages = 0, [], [], []
    for f in page_xmls:
        count, opens = system_starts(f)
        if not count:
            continue
        first = ET.parse(f).getroot().find("part").find("measure")
        if first.find("print[@new-system='yes']") is None:
            raise LayoutError(f"{f.name} does not say where its lines start (engine without system marks)")
        m = re.search(r"\.p(\d+)(?:\.\d+)?\.musicxml$", f.name)
        key = m.group(1) if m else f.name
        if key not in pages:
            pages.append(key)
        starts += [bars + s for s in opens]
        page_of += [pages.index(key)] * len(opens)
        bars += count
    if not starts:
        raise LayoutError("no per-page MusicXML with bars in it")
    return Target(bars, starts, page_of)


def staff_right(gray, top: int, bottom: int, left: int) -> Optional[int]:
    """Column where the staff between rows ``top``..``bottom`` ends: its five lines run to there."""
    import numpy as np
    a = np.asarray(gray) < 200
    rows = [round(top + k * (bottom - top) / 4) for k in range(5)]
    hit = np.mean([a[max(y - 1, 0):y + 2].any(axis=0) for y in rows], axis=0) >= 0.8
    window = 40
    if len(hit) <= window:
        return None
    dense = np.flatnonzero(np.convolve(hit.astype(float), np.ones(window), mode="valid") >= window * 0.9)
    dense = dense[dense >= left]
    if not len(dense):
        return None
    cols = np.flatnonzero(hit[: int(dense[-1]) + window])
    return int(cols[-1])


def measure_source(pdf: Path, pages: Sequence[int], pdftoppm: str = "pdftoppm") -> PageStyle:
    """Page size, side margin and staff-space size of ``pages`` (1-based) of the original."""
    from PIL import Image
    from musicocr.assemble.alignment.systems import all_staves
    spaces, rights, sizes = [], [], []
    with tempfile.TemporaryDirectory() as t:
        for n in pages:
            stem = Path(t) / f"p{n}"
            subprocess.run([pdftoppm, "-r", str(_DPI), "-gray", "-png", "-singlefile",
                            "-f", str(n), "-l", str(n), str(pdf), str(stem)],
                           check=True, capture_output=True)
            img = Image.open(stem.with_suffix(".png")).convert("L")
            mm = 25.4 / _DPI
            sizes.append((img.width * mm, img.height * mm))
            for top, bottom, left in all_staves(img):
                spaces.append((bottom - top) / 4 * mm)
                right = staff_right(img, top, bottom, left)
                if right is not None:
                    rights.append((img.width - right) * mm)
    if not spaces:
        raise LayoutError("no staves found on the original pages")
    # the largest: a piece cut from a shared page has a first or last page shorter than the sheet
    width, height = max(w for w, _ in sizes), max(h for _, h in sizes)
    side = statistics.median(rights) if rights else 15.0
    return PageStyle(width, height, min(20.0, max(7.0, side)), statistics.median(spaces))


# --- writing the score -----------------------------------------------------------

_MEASURE = re.compile(r"<Measure(?:\s[^>]*)?>.*?</Measure>", re.S)
_OUR_BREAK = re.compile(r"[ \t]*<LayoutBreak>(?:(?!</LayoutBreak>).)*?<subtype>(?:line|page)</subtype>"
                        r"(?:(?!</LayoutBreak>).)*?</LayoutBreak>\n?", re.S)
_STRETCH = re.compile(r"[ \t]*<stretch>[^<]*</stretch>\n?")


def _first_staff(text: str) -> Tuple[int, int]:
    """Span of the first staff's content (the one that carries breaks and bar settings)."""
    m = re.search(r'<Staff id="1">', text)
    if not m:
        raise LayoutError("no staff content in the .mscx")
    return m.start(), text.index("</Staff>", m.start())


def count_measures(text: str) -> int:
    a, b = _first_staff(text)
    return len(_MEASURE.findall(text[a:b]))


def patch_mscx(text: str, target: Target, stretch: Dict[int, float]) -> str:
    """``text`` with the target's line/page breaks, and ``stretch`` (line index -> factor) on the
    bars of those lines. Section breaks stay; other bars' own stretch is left alone."""
    a, b = _first_staff(text)
    block = text[a:b]
    spans = [m.span() for m in _MEASURE.finditer(block)]
    if len(spans) != target.bars:
        raise LayoutError(f"the score has {len(spans)} bars, the pages read have {target.bars}")
    ends = {}
    for i in range(len(target.starts)):
        page_end = i + 1 < len(target.starts) and target.page_of[i + 1] != target.page_of[i]
        if i + 1 < len(target.starts):
            ends[target.end(i)] = "page" if page_end else "line"
    out, last = [], 0
    for k, (x, y) in enumerate(spans):
        bar = _OUR_BREAK.sub("", block[x:y])
        factor = stretch.get(target.line_of(k))
        if factor is not None:
            bar = _STRETCH.sub("", bar)
        add = []
        kind = ends.get(k)
        if kind == "page" or (kind == "line" and "<subtype>section</subtype>" not in bar):
            add.append(f"<LayoutBreak>\n          <subtype>{kind}</subtype>\n          </LayoutBreak>")
        if factor is not None and factor != 1.0:
            add.append(f"<stretch>{factor:g}</stretch>")
        if add:
            head = re.match(r"<Measure(?:\s[^>]*)?>(\s*<eid>[^<]*</eid>)?", bar)
            bar = bar[:head.end()] + "".join("\n        " + s for s in add) + bar[head.end():]
        out.append(block[last:x] + bar)
        last = y
    out.append(block[last:])
    return text[:a] + "".join(out) + text[b:]


def _set(style: str, tag: str, value) -> str:
    new = f"<{tag}>{value:g}</{tag}>"
    out, n = re.subn(rf"<{tag}>[^<]*</{tag}>", new, style)
    return out if n else style.replace("</Style>", f"  {new}\n    </Style>")


def patch_style(style: str, page: PageStyle, spatium_mm: float,
                spacing: Optional[Tuple[float, float]]) -> str:
    """The ``score_style.mss`` with the original's page size and margins and the given staff size."""
    inch = 1 / 25.4
    side = round(page.side_mm * inch, 4)
    values = {"pageWidth": round(page.width_mm * inch, 4), "pageHeight": round(page.height_mm * inch, 4),
              "pagePrintableWidth": round(page.width_mm * inch - 2 * side, 4),
              "spatium": round(spatium_mm, 4)}
    for which in ("Even", "Odd"):
        values[f"page{which}LeftMargin"] = side
        values[f"page{which}TopMargin"] = values[f"page{which}BottomMargin"] = round(_TOP_BOTTOM_MM * inch, 4)
    if spacing:
        values["minSystemDistance"] = values["minSystemSpread"] = spacing[0]
        values["maxSystemDistance"] = spacing[1]
    for tag, value in values.items():
        style = _set(style, tag, value)
    return style


def read_mscz(mscz: Path) -> Tuple[str, str]:
    with zipfile.ZipFile(mscz) as z:
        score = next((n for n in z.namelist() if n.endswith(".mscx") and "/" not in n), None)
        if score is None or "score_style.mss" not in z.namelist():
            raise LayoutError(f"{mscz.name}: not a MuseScore 4 score")
        return z.read(score).decode("utf-8"), z.read("score_style.mss").decode("utf-8")


def write_mscz(src: Path, dest: Path, mscx: str, style: str) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".mscx") and "/" not in item.filename:
                data = mscx.encode("utf-8")
            elif item.filename == "score_style.mss":
                data = style.encode("utf-8")
            zout.writestr(item, data)


# --- checking with MuseScore -----------------------------------------------------

def rendered_systems(mpos: Path) -> List[Tuple[int, int]]:
    """``(page, first bar)`` of each system, from MuseScore's measure-position export."""
    out: List[Tuple[int, int]] = []
    last = None
    for k, e in enumerate(ET.parse(mpos).getroot().find("elements")):
        key = int(e.get("page")), round(float(e.get("y")))
        if key != last:
            out.append((key[0], k))
            last = key
    return out


def compare(target: Target, rendered: Sequence[Tuple[int, int]]) -> Tuple[List[int], List[int]]:
    """``(lines that wrapped, lines on a later page than the original's)``."""
    wanted: Set[int] = set(target.starts)
    wrapped = sorted({target.line_of(bar) for _, bar in rendered if bar not in wanted})
    page_at = {bar: page for page, bar in rendered}
    moved = [i for i, s in enumerate(target.starts) if page_at.get(s, target.page_of[i]) != target.page_of[i]]
    return wrapped, moved


def _musescore_layout(tool: str) -> Callable[[Path], List[Tuple[int, int]]]:
    def run(mscz: Path) -> List[Tuple[int, int]]:
        out = mscz.with_suffix(".mpos")
        out.unlink(missing_ok=True)
        run_cmd_lenient([tool, "-f", "-o", str(out), str(mscz)], timeout=300)
        if not out.exists() or not out.stat().st_size:       # MuseScore may crash on exit; judge by the file
            raise LayoutError("MuseScore did not export the measure positions")
        return rendered_systems(out)
    return run


# --- fitting ---------------------------------------------------------------------

def fit(mscz: Path, dest: Path, target: Target, page: PageStyle,
        layout: Callable[[Path], List[Tuple[int, int]]], log: Callable[[str], None] = print) -> Result:
    """Write ``dest``: ``mscz`` laid out like the original as nearly as MuseScore allows."""
    mscx, style = read_mscz(mscz)
    size, floor = page.spatium_mm, page.spatium_mm * _MIN_SIZE
    level: Dict[int, int] = {}            # line -> index into _STRETCH_STEPS
    spacing = 0
    with tempfile.TemporaryDirectory() as t:
        trial = Path(t) / "trial.mscz"
        for round_no in range(_MAX_ROUNDS):
            stretch = {i: _STRETCH_STEPS[v] for i, v in level.items()}
            write_mscz(mscz, trial, patch_mscx(mscx, target, stretch), patch_style(style, page, size, _SPACINGS[spacing]))
            rendered = layout(trial)
            wrapped, moved = compare(target, rendered)
            log(f"  layout: staff space {size:.2f} mm: {len(wrapped)} line(s) wrap, "
                f"{len(moved)} on the wrong page")
            if round_no == _MAX_ROUNDS - 1:
                break
            smaller = size * _SHRINK >= floor
            if wrapped:
                stuck = any(level.get(i, 0) == len(_STRETCH_STEPS) - 1 for i in wrapped)
                if smaller and (stuck or len(wrapped) > _MANY_WRAPPED * len(target.starts)):
                    size *= _SHRINK
                    level = {}
                elif not stuck:
                    for i in wrapped:
                        level[i] = level.get(i, 0) + 1
                else:
                    break
            elif moved:
                if spacing + 1 < len(_SPACINGS):
                    spacing += 1
                elif smaller:
                    size *= _SHRINK
                else:
                    break
            else:
                break
        shutil.copyfile(trial, dest)
    return Result(len(rendered), 1 + max(p for p, _ in rendered), size,
                  {i: v for i, v in stretch.items() if v != 1.0}, wrapped, moved)


def apply_layout(mscz: Path, dest: Path, page_xmls: Sequence[Path], source_pdf: Path, musescore: str,
                 pdftoppm: str = "pdftoppm", log: Callable[[str], None] = print) -> Result:
    """Lay ``mscz`` out like ``source_pdf``, writing ``dest`` (which may be ``mscz`` itself)."""
    target = target_from_pages(page_xmls)
    have = count_measures(read_mscz(mscz)[0])
    if have != target.bars:
        raise LayoutError(f"the score has {have} bars, the pages read have {target.bars}")
    pages = sorted({int(m.group(1)) for f in page_xmls
                    for m in [re.search(r"\.p(\d+)(?:\.\d+)?\.musicxml$", f.name)] if m}) or [1]
    result = fit(mscz, dest, target, measure_source(source_pdf, pages, pdftoppm),
                 _musescore_layout(musescore), log)
    log("  " + summary(target, result))
    return result


def summary(target: Target, r: Result) -> str:
    text = (f"layout: {r.systems} lines on {r.pages} page(s); original {len(target.starts)} on {target.pages}; "
            f"staff space {r.spatium_mm:.2f} mm, {len(r.squeezed)} line(s) squeezed")
    for what, lines in (("still wrapping", r.wrapped), ("on the wrong page", r.moved)):
        if lines:
            text += f"; {what}: bars " + ", ".join(f"{target.starts[i] + 1}-{target.end(i) + 1}" for i in lines[:8])
    return text
