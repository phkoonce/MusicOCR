"""Rehearsal marks across the parts of a combined score.

The circled numbers of an ensemble book are found by shape in every part (``musicocr.text.circles``) as
unnumbered ``<rehearsal>`` placeholders. A mark that two or more parts print over the same bar (a bar either
side counts as the same: OCR attaches a mark at a barline to either) is real; the marks are then numbered in
bar order with MuseScore's default letters (A, B, ... Z, AA, ...), written once in the first part, and the other
parts' copies go. Position and sequence are what matter, so the printed 1, 2, 3 become A, B, C.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from typing import Dict, List, Optional, Tuple

from musicocr.assemble.text_system import insert_at
from musicocr.text.layer import CIRCLE_TEXT

MIN_PARTS = 2
BAR_TOLERANCE = 1


def letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _placeholder(d: ET.Element) -> bool:
    return d.tag == "direction" and any((r.text or "").strip() == CIRCLE_TEXT for r in d.iter("rehearsal"))


def unify_rehearsal(parts: Dict[int, List[ET.Element]]) -> List[str]:
    """Number the rehearsal marks the parts agree on and write each once, in the first part."""
    order = sorted(parts)
    found: List[Tuple[int, int, ET.Element, ET.Element]] = []        # (part, bar, measure, direction)
    for n in order:
        for b, m in enumerate(parts[n]):
            found += [(n, b, m, d) for d in m.findall("direction") if _placeholder(d)]
    if not found:
        return []
    need = min(MIN_PARTS, len(order))
    left = sorted(found, key=lambda f: (f[1], f[0]))
    marks: List[Tuple[int, List[tuple]]] = []                        # (bar, copies)
    while left:
        first = left[0]
        group = [f for f in left if abs(f[1] - first[1]) <= BAR_TOLERANCE]
        voters = {}
        for f in group:
            voters.setdefault(f[0], f)
        if len(voters) >= need:
            bars = Counter(f[1] for f in voters.values())
            bar = max(sorted(bars), key=lambda b: bars[b])
            marks.append((bar, group))
            left = [f for f in left if f not in group]
        else:
            left = [f for f in left if f is not first]
    marks.sort(key=lambda t: t[0])
    for f in found:                                                  # placeholders nobody confirmed are noise
        f[2].remove(f[3])
    first_part = order[0]
    for i, (bar, group) in enumerate(marks, 1):
        src = next((f for f in group if f[1] == bar and f[0] == first_part), None) \
            or next((f for f in group if f[1] == bar), group[0])
        d = src[3]
        for r in d.iter("rehearsal"):
            r.text = letter(i)
        for st in d.findall("staff"):
            d.remove(st)
        for off in d.findall("offset"):
            d.remove(off)
        d.set("placement", "above")
        insert_at(parts[first_part][bar], d, 0)           # a rehearsal mark sits at the start of its bar
    return [f"rehearsal: {len(marks)} circled number(s) agreed by {need}+ parts, written once as "
            f"{letter(1)}..{letter(len(marks))} at bars " + ", ".join(str(b + 1) for b, _ in marks)]
