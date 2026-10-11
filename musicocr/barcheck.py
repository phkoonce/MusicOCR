"""Engine-agnostic bar-count check against the printed structure.

Born-digital PDFs carry a text layer in which engraving software prints a bar
number at the start of every system (left margin) and a large count over every
multi-measure rest. From those two facts the number of measure *units* an OMR
engine should have produced is known exactly (the pipeline's collapsed
convention, one unit per multi-rest)::

    expected(system) = next_system_start - system_start - sum(count - 1)

An engine that invents a "ghost" bar (e.g. Copisteria reading a cautionary key
signature as a measure) produces one unit too many; one that loses a bar
(an unread multi-rest, a merged pair) one too few. The check compares per
system when the page's MusicXML carries ``<print new-system="yes">`` marks that
line up with the printed system numbers (homr), otherwise per page (Copisteria
writes no system marks). It reuses the text-layer parsing of
:mod:`musicocr.alignment.systems`, and counts either multi-rest spelling the
same way via :func:`musicocr.multirest.collapsed_units`.

Scanned PDFs have no text layer: nothing is found and nothing is reported.
The last system of the last page (or of a page whose successor is not in the
PDF) has no known end bar and is skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from musicocr.alignment.systems import _SYSTEM_Y_PAD, page_text_layout, pdf_words
from musicocr.multirest import collapsed_units

_PAGE_FILE = re.compile(r"\.p(\d+)\.musicxml$")

WordsFn = Callable[[str, int], list]


@dataclass
class BarIssue:
    page: int
    system: Optional[int]      # 1-based system on the page; None = whole page
    first_bar: int             # printed bar number the span starts at
    last_bar: int              # last printed bar of the span (inclusive)
    expected: int              # measure units the print implies
    actual: int                # measure units found

    @property
    def message(self) -> str:
        where = f"page {self.page}" + (f" system {self.system}" if self.system else "")
        diff = self.actual - self.expected
        hint = (f"{diff} extra, likely a ghost bar" if diff > 0
                else f"{-diff} missing, likely a lost or merged bar")
        return (f"{where} (printed bars {self.first_bar}-{self.last_bar}): {self.actual} "
                f"measure(s) found, print implies {self.expected} ({hint})")


def page_units(page_xml: Path) -> Optional[Tuple[List[int], bool]]:
    """-> (units per system group, has_system_marks) for the first part, or None."""
    try:
        part = ET.parse(page_xml).getroot().find("part")
    except (OSError, ET.ParseError):
        return None
    if part is None:
        return None
    groups: List[int] = [0]
    marks = False
    for measure, _ in collapsed_units(part):
        p = measure.find("print")
        if p is not None and p.get("new-system") == "yes":
            marks = True
            if groups[-1]:
                groups.append(0)
        groups[-1] += 1
    return groups, marks


def check_page(page: int, starts: Sequence[Tuple[float, int]],
               counts: Sequence[Tuple[float, float, int]], end_bar: Optional[int],
               groups: Sequence[int], marks: bool) -> List[BarIssue]:
    """Compare one page's measure units to the print; pure function of its inputs.

    ``starts`` are ``(y, bar)`` system starts from the text layer (page 1's first
    system is unnumbered and implied), ``counts`` ``(x, y, count)`` multi-rest
    numerals, ``end_bar`` the first bar of the next page, ``groups`` the units
    per XML system and ``marks`` whether those groups come from real system marks.
    """
    ys = [y for y, _ in starts]
    bars = [b for _, b in starts]
    per_system = marks and (len(groups) == len(starts)
                            or (page == 1 and len(groups) == len(starts) + 1))
    if per_system and len(groups) == len(starts) + 1:
        ys, bars = [0.0] + ys, [1] + bars
    if not per_system:
        if page == 1:
            ys, bars = ([0.0] + ys, [1] + bars) if bars and bars[0] > 1 else (ys, bars)
        if not bars or end_bar is None:
            return []
        extra = sum(c - 1 for _, _, c in counts)
        expected = end_bar - bars[0] - extra
        actual = sum(groups)
        if expected >= 1 and actual != expected:
            return [BarIssue(page, None, bars[0], end_bar - 1, expected, actual)]
        return []
    issues: List[BarIssue] = []
    for i, actual in enumerate(groups):
        nxt = bars[i + 1] if i + 1 < len(bars) else end_bar
        if nxt is None:
            continue
        lo = ys[i] - _SYSTEM_Y_PAD if i else -1
        hi = ys[i + 1] - _SYSTEM_Y_PAD if i + 1 < len(ys) else 1e9
        extra = sum(c - 1 for _, y, c in counts if lo <= y < hi)
        expected = nxt - bars[i] - extra
        if expected >= 1 and actual != expected:
            issues.append(BarIssue(page, i + 1, bars[i], nxt - 1, expected, actual))
    return issues


def check_bar_counts(pdf: Path, page_files: Dict[int, Path], pdftotext: str = "pdftotext",
                     words: Optional[WordsFn] = None) -> List[BarIssue]:
    """Issues across all pages in ``page_files`` ({pdf page number: MusicXML}).

    Pages whose PDF has no usable text layer (no numbered system starts) or whose
    XML cannot be read are skipped silently.
    """
    get = words or (lambda p, n: pdf_words(p, n, pdftotext))
    cache: Dict[int, Tuple[list, list]] = {}

    def layout(n: int) -> Tuple[list, list]:
        if n not in cache:
            try:
                cache[n] = page_text_layout(get(str(pdf), n))
            except (OSError, ValueError):
                cache[n] = ([], [])
        return cache[n]

    issues: List[BarIssue] = []
    for page in sorted(page_files):
        starts, counts = layout(page)
        units = page_units(page_files[page])
        if not starts or units is None:
            continue
        nxt = layout(page + 1)[0]
        end_bar = nxt[0][1] if nxt else None
        issues += check_page(page, starts, counts, end_bar, *units)
    return issues


def page_files_of(paths: Sequence[Path]) -> Dict[int, Path]:
    """Map pdf page number -> per-page MusicXML (``<book>.pNNN.musicxml`` names only)."""
    out: Dict[int, Path] = {}
    for p in paths:
        m = _PAGE_FILE.search(Path(p).name)
        if m:
            out[int(m.group(1))] = Path(p)
    return out
