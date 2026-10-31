"""Bar numbers on a page of systems, with first/second endings (voltas).

Printed bar numbers count a first and a second ending as one bar: the second ending takes the number of the
first. The OMR reads every notated bar, so a page with k endings reads k more bars than its numbers span. This
module numbers a part's measures from the systems' printed start numbers (``walk``), collects the endings the
parts agree on (``consensus``) and maps printed numbers onto *slots*, one per notated bar (``Grid``), the
space in which ``combine`` lays the parts out.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

Src = Tuple[ET.Element, int]


def system_breaks(pg: Sequence[Src]) -> Optional[List[int]]:
    """Index of the measure that opens each system on a page, from the OMR's ``<print new-system>``;
    None when the page carries no such marks."""
    out = [i for i, (m, _) in enumerate(pg)
           if any(p.get("new-system") == "yes" for p in m.findall("print"))]
    return out if out and out[0] == 0 else None


def _endings(m: ET.Element) -> List[Tuple[int, str]]:
    out = []
    for e in m.iter("ending"):
        n = re.match(r"\d+", e.get("number", "1"))
        if e.get("type") in ("start", "stop", "discontinue"):
            out.append((int(n.group()) if n else 1, e.get("type")))
    return out


def roles(pages: Sequence[Sequence[Src]]) -> List[List[int]]:
    """Per page and measure: 0 = an ordinary bar, 1 = in a first ending, 2 = in a second (or later) ending."""
    state = 0
    out: List[List[int]] = []
    for pg in pages:
        row = []
        for m, _ in pg:
            ends = _endings(m)
            for n, typ in ends:
                if typ == "start":
                    state = 1 if n == 1 else 2
            row.append(state)
            if any(typ in ("stop", "discontinue") for _, typ in ends):
                state = 0
        out.append(row)
    return out


@dataclass
class Walk:
    """The result of numbering one part's pages: ``numbers[page][measure]`` (None = unknown) and the
    endings seen as ``voltas[n] = [first-ending bars, second-ending bars]``."""
    numbers: List[List[Optional[int]]] = field(default_factory=list)
    voltas: Dict[int, List[int]] = field(default_factory=dict)


def walk(pages: Sequence[Sequence[Src]], rl: Sequence[Sequence[int]],
         breaks: Sequence[Optional[Sequence[int]]], anchors: Sequence[Dict[int, int]],
         starts: Sequence[Optional[int]]) -> Walk:
    """Number every measure: a system's printed start number where known, then +1 per bar, the second
    ending going back to the number its first ending started at."""
    w = Walk()
    cur: Optional[int] = None
    e1_start: Optional[int] = None
    prev = 0
    for pi, pg in enumerate(pages):
        start_of = {b: i for i, b in enumerate(breaks[pi] or [])}
        row: List[Optional[int]] = []
        for k in range(len(pg)):
            r = rl[pi][k]
            if r == 2 and prev == 1 and e1_start is not None:
                cur = e1_start
            i = start_of.get(k)
            if i is not None and i in anchors[pi]:
                cur = anchors[pi][i]
            elif k == 0 and starts[pi] is not None:
                cur = starts[pi]
            if r == 1 and prev != 1:
                e1_start = cur
            if cur is not None and e1_start is not None:
                if r == 1:
                    w.voltas.setdefault(e1_start, [0, 0])[0] += 1
                elif r == 2:
                    w.voltas.setdefault(e1_start, [0, 0])[1] += 1
            row.append(cur)
            if cur is not None:
                cur += 1
            prev = r
        w.numbers.append(row)
    return w


Volta = Tuple[int, int, int]          # (number, bars in the first ending, bars in the second)


def consensus(walks: Sequence[Walk]) -> List[Volta]:
    """The endings that at least half of the parts (and at least one) saw at the same number."""
    votes: Dict[int, List[Tuple[int, int]]] = {}
    for w in walks:
        for n, (a, b) in w.voltas.items():
            if a and b:
                votes.setdefault(n, []).append((a, b))
    need = max((len(walks) + 1) // 2, 1)
    out = []
    for n, v in sorted(votes.items()):
        if len(v) >= need:
            (a, b), _ = Counter(v).most_common(1)[0]
            out.append((n, a, b))
    return out


class Grid:
    """Printed bar numbers <-> slots (one per notated bar)."""

    def __init__(self, voltas: Sequence[Volta] = ()):
        self.voltas = sorted(voltas)

    def _before(self, n: int) -> int:
        return sum(a for v, a, b in self.voltas if v + b <= n)

    def slot(self, number: int, role: int = 0) -> int:
        """The slot of the bar printed ``number``; ``role`` 1/2 marks a bar of a first/second ending."""
        for v, a, b in self.voltas:
            if v <= number < v + max(a, b):
                return number + self._before(v) + (a if role == 2 else 0)
        return number + self._before(number)

    def __bool__(self) -> bool:
        return bool(self.voltas)


def segments(n_src: int, first: int, length: int, breaks: Sequence[int],
             anchor: Dict[int, int]) -> List[Tuple[int, int, int, int]]:
    """Cut a page at its anchored systems: [(src_lo, src_hi, slot_lo, slot_hi)].

    ``anchor`` maps a system index to the slot its first bar must land on. Anchors that cannot be right
    (not increasing, fewer slots than systems, more than 14 bars per system) are ignored."""
    edge = list(breaks) + [n_src]
    bounds = [(0, first)]
    for i in sorted(anchor):
        s = anchor[i]
        li, ls = bounds[-1]
        if i <= li or s - ls < i - li or s - ls > 14 * (i - li):
            continue
        if first + length - s < len(breaks) - i or first + length - s > 14 * (len(breaks) - i):
            continue
        bounds.append((i, s))
    bounds.append((len(breaks), first + length))
    return [(edge[i0], edge[i1], s0, s1) for (i0, s0), (i1, s1) in zip(bounds, bounds[1:])]
