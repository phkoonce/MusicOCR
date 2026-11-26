"""Cut the original and MuseScore's rendering into lines and line them up bar by bar."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw

from musicocr.assemble.alignment.systems import all_staves
from musicocr.util import run_cmd_lenient

DPI = 200
_MPOS_PER_PX = 2400 / DPI          # MuseScore's position units per pixel at DPI
_MARGIN_SP = 8.0                   # most music kept above and below a staff, in staff spaces


class ImageError(Exception):
    pass


@dataclass
class Staff:
    page: int
    top: int
    bottom: int
    left: int
    right: int
    crop_top: int = 0
    crop_bottom: int = 0

    @property
    def sp(self) -> float:
        return (self.bottom - self.top) / 4


@dataclass
class RenderedBar:
    page: int
    y: float
    left: float
    right: float


def _find_staves(img: Image.Image, page: int) -> List[Staff]:
    """Staves of a scanned page."""
    rough = all_staves(img)
    if not rough:
        return []
    dark = np.asarray(img) < 200
    space = float(np.median([(b - t) / 4 for t, b, _ in rough]))
    out = []
    for top, bottom, left in rough:
        top, bottom = _refine(dark, top, space, left)
        extent = _extent(dark, top, bottom)
        if extent:
            out.append(Staff(page, top, bottom, *extent))
    return _with_margins(out, img.height)


def _refine(dark: np.ndarray, top: int, space: float, left: int) -> Tuple[int, int]:
    """Top and bottom line rows of the staff near ``top``.

    The rough detector can take a beam for the outer line; the page's usual line spacing and the
    rows that are darkest across the whole width tell which five rows are the lines.
    """
    profile = dark[:, left:].mean(axis=1)
    rows = np.maximum(np.maximum(profile, np.roll(profile, 1)), np.roll(profile, -1))
    best = (-1.0, top, space)
    for sp in np.arange(space - 1, space + 1.01, 0.25):
        for t in range(max(0, int(top - 1.5 * space)), int(top + 1.5 * space) + 1):
            at = [round(t + k * sp) for k in range(5)]
            if at[-1] < len(rows):
                score = float(rows[at].sum() + 0.5 * profile[at].sum())
                if score > best[0]:
                    best = (score, t, sp)
    return best[1], round(best[1] + 4 * best[2])


def _extent(dark: np.ndarray, top: int, bottom: int, window: int = 40) -> Optional[Tuple[int, int]]:
    """First and last column of a staff: where at least four of its lines run."""
    rows = [round(top + k * (bottom - top) / 4) for k in range(5)]
    hit = np.mean([dark[max(y - 2, 0):y + 3].any(axis=0) for y in rows], axis=0) >= 0.8
    dense = np.flatnonzero(np.convolve(hit.astype(float), np.ones(window), mode="valid") >= window * 0.9)
    if not len(dense):
        return None
    first = dense[0] + int(np.argmax(hit[dense[0]:]))
    last = dense[-1] + window - 1 - int(np.argmax(hit[dense[-1]:dense[-1] + window][::-1]))
    return int(first), int(last)


def rendered_staves(img: Image.Image, page: int) -> List[Staff]:
    """Staves of a page MuseScore drew: five evenly spaced, unbroken, level lines.

    The scan detector samples a band of the page, where a beam between two lines can hide a staff.
    """
    dark = np.asarray(img) < 200
    need = img.width // 4
    lines: List[List[int]] = []                  # [first row, last row, left, right]
    for y in np.flatnonzero(dark.sum(axis=1) >= need):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], dark[y].view(np.int8), [0]))))
        starts, ends = edges[::2], edges[1::2]
        k = int(np.argmax(ends - starts))
        if ends[k] - starts[k] < need:
            continue
        if lines and y - lines[-1][1] <= 1:
            lines[-1][1] = int(y)
        else:
            lines.append([int(y), int(y), int(starts[k]), int(ends[k]) - 1])
    out: List[Staff] = []
    i = 0
    while i + 5 <= len(lines):
        five = lines[i:i + 5]
        mids = [(a + b) / 2 for a, b, _, _ in five]
        gaps = np.diff(mids)
        if gaps.max() - gaps.min() <= 2:
            out.append(Staff(page, round(mids[0]), round(mids[-1]), five[0][2], five[0][3]))
            i += 5
        else:
            i += 1
    return _with_margins(out, img.height)


def _with_margins(staves: List[Staff], height: int) -> List[Staff]:
    """Set how much above and below each staff is shown, keeping clear of its neighbours."""
    for i, s in enumerate(staves):
        up = down = int(_MARGIN_SP * s.sp)
        if i:
            up = min(up, (s.top - staves[i - 1].bottom) * 3 // 4)
        if i + 1 < len(staves):
            down = min(down, (staves[i + 1].top - s.bottom) * 3 // 4)
        s.crop_top, s.crop_bottom = max(0, s.top - up), min(height, s.bottom + down)
    return staves


def seam(page: Image.Image, upper: Staff, lower: Staff) -> int:
    """The row where the pictures of two staves of a system meet: the emptiest one between them,
    the nearest to halfway among equals, within what both pictures show."""
    a, b = max(upper.bottom, lower.crop_top), min(lower.top, upper.crop_bottom)
    middle = (upper.bottom + lower.top) // 2
    if b <= a:
        return middle
    ink = (np.asarray(page)[a:b] < 128).sum(axis=1)
    return min(range(a, b), key=lambda y: (int(ink[y - a]), abs(y - middle)))


def render_original(pdf: Path, out: Path, pdftoppm: str = "pdftoppm") -> Tuple[List[Image.Image], List[Staff]]:
    """Page images of the original and every staff on them, in reading order."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("orig-*.png"):
        old.unlink()
    subprocess.run([pdftoppm, "-r", str(DPI), "-gray", "-png", str(pdf), str(out / "orig")],
                   check=True, capture_output=True)
    pages = [Image.open(p).convert("L") for p in sorted(out.glob("orig-*.png"), key=lambda p: int(p.stem.split("-")[-1]))]
    staves = [s for n, img in enumerate(pages) for s in _find_staves(img, n)]
    if not staves:
        raise ImageError("no staves found in the original")
    return pages, staves


def _on_white(path: Path) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("L")


def render_score(mscz: Path, out: Path, musescore: str) -> Tuple[List[Image.Image], List[Staff], List[RenderedBar]]:
    """Page images of the score, their staves, and where MuseScore put each bar."""
    out.mkdir(parents=True, exist_ok=True)
    for old in [*out.glob("score*.png"), out / "score.mpos"]:
        old.unlink(missing_ok=True)
    mpos = out / "score.mpos"
    # MuseScore may crash on exit; judge by the files
    # -r scales the measure positions as well as the pictures, so both exports need it
    run_cmd_lenient([musescore, "-f", "-r", str(DPI), "-o", str(mpos), str(mscz)], timeout=300)
    run_cmd_lenient([musescore, "-f", "-r", str(DPI), "-o", str(out / "score.png"), str(mscz)], timeout=300)
    files = sorted(out.glob("score-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if not files or not mpos.exists() or not mpos.stat().st_size:
        raise ImageError("MuseScore did not render the score")
    pages = [_on_white(p) for p in files]
    bars = []
    for e in ET.parse(mpos).getroot().find("elements"):
        x = float(e.get("x")) / _MPOS_PER_PX
        bars.append(RenderedBar(int(e.get("page")), float(e.get("y")) / _MPOS_PER_PX,
                                x, x + float(e.get("sx")) / _MPOS_PER_PX))
    staves = [s for n, img in enumerate(pages) for s in rendered_staves(img, n)]
    return pages, staves, bars


def rendered_lines(bars: Sequence[RenderedBar]) -> List[List[int]]:
    """Bar numbers (0-based) of each line of the rendering."""
    lines: List[List[int]] = []
    last = None
    for k, b in enumerate(bars):
        key = b.page, round(b.y)
        if key != last:
            lines.append([])
            last = key
        lines[-1].append(k)
    return lines


def systems(staves: Sequence[Staff], per_system: int) -> List[List[Staff]]:
    """Group a page-ordered staff list into systems of ``per_system`` staves."""
    out: List[List[Staff]] = []
    for page in sorted({s.page for s in staves}):
        here = [s for s in staves if s.page == page]
        if len(here) % per_system:
            raise ImageError(f"page {page + 1}: found {len(here)} staves, not a multiple of {per_system}")
        out += [here[i:i + per_system] for i in range(0, len(here), per_system)]
    return out


def barline_columns(img: Image.Image, staff: Staff) -> List[int]:
    """Columns that could be bar lines: dark from the top staff line to the bottom one."""
    inset = 2                                                # a slightly tilted staff
    dark = np.asarray(img)[staff.top + inset:staff.bottom + 1 - inset] < 160
    cols = np.flatnonzero(dark.mean(axis=0) >= 0.93)
    groups: List[List[int]] = []
    for x in cols:
        if groups and x - groups[-1][-1] <= staff.sp:        # a double bar line is one place
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    return [(g[0] + g[-1]) // 2 for g in groups
            if g[-1] - g[0] <= 1.5 * staff.sp and staff.left + 2 * staff.sp < g[0] < staff.right - 2 * staff.sp]


def match_barlines(expected: Sequence[float], candidates: Sequence[int], sp: float) -> List[int]:
    """For each expected position the candidate that fits it, keeping their order.

    A position with no candidate near it keeps the expected place (dynamic programme: the total
    distance is least, and going without a candidate costs as much as being 6 staff spaces out).
    """
    skip = 6 * sp
    best = {-1: (0.0, [])}                          # last candidate used -> (cost, chosen positions)
    for e in expected:
        step = {}
        for last, (cost, chosen) in best.items():
            options = [(last, cost + skip, round(e))]
            options += [(j, cost + abs(candidates[j] - e), candidates[j])
                        for j in range(last + 1, len(candidates))]
            for j, c, x in options:
                if chosen and x <= chosen[-1]:
                    continue
                if j not in step or c < step[j][0]:
                    step[j] = (c, chosen + [x])
        best = step or {k: (c, v + [v[-1] + 1]) for k, (c, v) in best.items()}
    return min(best.values())[1]


def bar_edges(original: Image.Image, o: Staff, r: Staff, bars: Sequence[RenderedBar]) -> Tuple[List[int], List[int]]:
    """Where each bar of a line starts and ends: (in the rendering, in the original)."""
    inner = [b.left for b in bars[1:]]
    left, right = r.left, round(bars[-1].right)
    mine = [left, *[round(x) for x in inner], right]
    scale = (o.right - o.left) / max(right - left, 1)
    expected = [o.left + (x - left) * scale for x in inner]
    found = match_barlines(expected, barline_columns(original, o), o.sp) if inner else []
    return mine, [o.left, *found, o.right]


def warp(rendering: Image.Image, original: Image.Image, o: Staff, r: Staff,
         mine: Sequence[int], theirs: Sequence[int]) -> Image.Image:
    """The rendered staff redrawn in the original's coordinates: same size, bar lines in the same places."""
    height = o.crop_bottom - o.crop_top
    scale = o.sp / r.sp
    top = round(r.top - (o.top - o.crop_top) / scale)
    bottom = round(top + height / scale)
    out = Image.new("L", (original.width, height), 255)

    def paste(a: int, b: int, x: int, y: int) -> None:
        if b > a and y > x:
            piece = rendering.crop((a, top, b, bottom))
            out.paste(piece.resize((y - x, height), Image.LANCZOS), (x, 0))

    lead = round(o.left - mine[0] * scale)
    paste(0, mine[0], max(lead, 0), theirs[0])
    for i in range(len(mine) - 1):
        paste(mine[i], mine[i + 1], theirs[i], theirs[i + 1])
    paste(mine[-1], rendering.width, theirs[-1], theirs[-1] + round((rendering.width - mine[-1]) * scale))
    return out


def tint(img: Image.Image, colour: Tuple[int, int, int]) -> Image.Image:
    """Black ink in ``colour`` on white."""
    ink = 1 - np.asarray(img, dtype=float)[..., None] / 255
    rgb = 255 - ink * (255 - np.array(colour, dtype=float))
    return Image.fromarray(rgb.astype("uint8"))


def overlay(original: Image.Image, mine: Image.Image) -> Image.Image:
    """Both at once: original in red, rendering in blue, black where they agree."""
    a = np.asarray(original, dtype=float) / 255
    b = np.asarray(mine, dtype=float) / 255
    return Image.fromarray((np.stack([b, a * b, a], axis=-1) * 255).astype("uint8"))


def marked(img: Image.Image, points: Sequence[Tuple[float, float]]) -> Image.Image:
    """``img`` with numbered circles at ``points`` (fractions of width and height)."""
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)
    for n, (fx, fy) in enumerate(points, 1):
        x, y = fx * out.width, fy * out.height
        draw.ellipse((x - 16, y - 16, x + 16, y + 16), outline=(230, 80, 0), width=3)
        draw.text((x + 18, y - 22), str(n), fill=(230, 80, 0), font_size=22)
    return out


def bar_at(x_fraction: float, width: int, edges: Sequence[int]) -> Tuple[int, int]:
    """``(index of the bar within the line, percent across it)`` for a tag position."""
    x = x_fraction * width
    i = max(0, min(len(edges) - 2, sum(1 for e in edges[1:-1] if x >= e)))
    span = max(edges[i + 1] - edges[i], 1)
    return i, round(100 * min(max((x - edges[i]) / span, 0), 1))


def stack(images: Sequence[Image.Image], gap: int = 12) -> Optional[Image.Image]:
    """``images`` one under another, with a grey rule between them."""
    if not images:
        return None
    width = max(i.width for i in images)
    out = Image.new("RGB", (width, sum(i.height for i in images) + gap * (len(images) - 1)), "white")
    y = 0
    for i, img in enumerate(images):
        if i:
            ImageDraw.Draw(out).line((0, y + gap // 2, width, y + gap // 2), fill=(170, 170, 170), width=2)
            y += gap
        out.paste(img.convert("RGB"), (0, y))
        y += img.height
    return out
