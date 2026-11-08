"""Repeat signs and first/second endings across the parts of a combined score.

Every part of an ensemble book prints the same repeats, and OMR reads them per part. ``copy_marks`` only
carries notes, directions and double bars across, so the combined score used to lose all of them even
though most parts had read them. Here each bar's repeat marks are voted over the aligned parts (a mark
needs ``MIN_PARTS`` parts, and half of the parts that have the bar) and the winners are written to every part:

* ``forward``  -- a forward repeat on the bar's left barline;
* ``backward`` -- a backward repeat on its right barline;
* ``start N``  -- the bar opens ending N (volta bracket);
* ``stop N``   -- the bar closes ending N.

Repeat signs go to every part; endings only to the first (MuseScore draws a bracket once, over the top staff).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Dict, List, Optional, Sequence, Set, Tuple

MIN_PARTS = 2

Src = Tuple[ET.Element, int]


def bar_marks(m: ET.Element) -> Set[str]:
    """The repeat marks of one source bar: ``forward``, ``backward``, ``start N``, ``stop N``."""
    out: Set[str] = set()
    for bl in m.findall("barline"):
        for r in bl.findall("repeat"):
            out.add("forward" if r.get("direction") == "forward" else "backward")
        for e in bl.findall("ending"):
            if e.get("type") == "start":
                out.add(f"start {e.get('number')}")
            else:
                out.add(f"stop {e.get('number')}")
    return out


def _barline(m: ET.Element, location: str) -> ET.Element:
    for bl in m.findall("barline"):
        if bl.get("location", "right") == location:
            return bl
    bl = ET.Element("barline", location=location)
    if location == "left":
        m.insert(0, bl)
    else:
        m.append(bl)
    return bl


def _clear(m: ET.Element) -> None:
    for bl in m.findall("barline"):
        for e in list(bl):
            if e.tag in ("repeat", "ending"):
                bl.remove(e)


def _style(bl: ET.Element, style: str) -> None:
    s = bl.find("bar-style")
    if s is None:
        s = ET.Element("bar-style")
        bl.insert(0, s)
    s.text = style


def unify_repeats(bars: Dict[str, Sequence[Optional[Src]]], built: Dict[int, List[ET.Element]]) -> List[str]:
    """Write the voted repeat marks into the built measures of every part. ``bars`` holds each part's aligned
    source bars (by part id), ``built`` its written measures (by part number, in the same order)."""
    seqs = list(bars.values())
    parts = [built[n] for n in sorted(built)]
    if len(seqs) < MIN_PARTS or not parts:
        return []
    total = len(parts[0])
    voted: Dict[int, Set[str]] = {}
    for b in range(total):
        have = [s[b] for s in seqs if b < len(s) and s[b] is not None]
        votes = Counter(mark for src in have for mark in bar_marks(src[0]))
        need = max(MIN_PARTS, math.ceil(len(have) / 2))
        win = {mark for mark, c in votes.items() if c >= need}
        # an ending that opens needs the bar that closes it, and the other way round, even with one reading short
        if win:
            voted[b] = win
    if not voted:
        return []
    for pi, ms in enumerate(parts):
        for b, win in voted.items():
            m = ms[b]
            _clear(m)
            starts = sorted(x for x in win if x.startswith("start "))
            stops = sorted(x for x in win if x.startswith("stop "))
            if "forward" in win or (starts and pi == 0):
                bl = _barline(m, "left")
                if "forward" in win:
                    _style(bl, "heavy-light")
                for s in starts if pi == 0 else ():          # a bracket is drawn once, over the top staff
                    ET.SubElement(bl, "ending", number=s.split()[1], type="start").text = s.split()[1] + "."
                if "forward" in win:
                    ET.SubElement(bl, "repeat", direction="forward")
            if "backward" in win or (stops and pi == 0):
                bl = _barline(m, "right")
                if "backward" in win:
                    _style(bl, "light-heavy")
                elif bl.find("bar-style") is None:
                    _style(bl, "regular")
                for s in stops if pi == 0 else ():
                    ET.SubElement(bl, "ending", number=s.split()[1], type="stop")
                if "backward" in win:
                    ET.SubElement(bl, "repeat", direction="backward")
    nrep = sum(1 for w in voted.values() for x in w if x in ("forward", "backward"))
    nend = sum(1 for w in voted.values() for x in w if x.startswith("start "))
    bars_at = ", ".join(f"{b + 1}:{'/'.join(sorted(w))}" for b, w in sorted(voted.items()))
    return [f"repeats: {nrep} repeat sign(s) and {nend} ending(s) voted over the parts -- bar {bars_at}"]
