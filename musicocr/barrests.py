"""Whole-bar rests always last exactly one bar.

Copisteria reads a whole-bar rest's length from the space it fills, and a bar that also holds cue-size
notes (small notes above a rest, "Vln. 1" cues, read as grace notes) comes out too long -- 4.5 or 7
beats in 4/4 -- so the bar is flagged and MuseScore shows an irregular measure. A bar whose only
sounding content is one rest has no other possible length than the time signature's, so the rest is
set to it. Only an overlong rest is shortened (a shorter one may be a pickup bar); bars with notes, or
several rests, are not touched.
"""
from __future__ import annotations

from fractions import Fraction as F
from pathlib import Path
from typing import Callable, Optional
from xml.etree import ElementTree as ET

from musicocr.util import measures_with_capacity


def fix_measure(measure: ET.Element, capacity: Optional[int]) -> bool:
    """Shorten the lone rest of ``measure`` to ``capacity`` if it is longer; True if it changed."""
    if capacity is None or measure.find("backup") is not None:
        return False
    sounding = [n for n in measure.findall("note") if n.find("grace") is None and n.find("chord") is None]
    if len(sounding) != 1 or sounding[0].find("rest") is None or sounding[0].find("time-modification") is not None:
        return False
    du = sounding[0].find("duration")
    if du is None or not (du.text or "").strip().isdigit() or int(du.text) <= capacity:      # a shorter lone rest may be a pickup
        return False
    du.text = str(capacity)
    return True


def fix_tree(root: ET.Element, log: Callable[[str], None] = print) -> int:
    """Fix every part of a ``score-partwise``; returns the number of bars changed."""
    fixed = 0
    for part in root.findall("part"):
        for m, div, cap in measures_with_capacity(part):
            if fix_measure(m, cap):
                log(f"  measure {m.get('number')}: overlong whole-bar rest cut to the bar's {F(cap, div)} beats")
                fixed += 1
    return fixed


def fix_file(path: Path, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place; returns the number of bars changed."""
    tree = ET.parse(path)
    n = fix_tree(tree.getroot(), log)
    if n:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return n
