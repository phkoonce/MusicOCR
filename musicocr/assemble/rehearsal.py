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

from musicocr.assemble.labels import book_style, kind, ordinal
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
    return d.tag == "direction" and any((r.text or "").strip().startswith(CIRCLE_TEXT) for r in d.iter("rehearsal"))


def _token(d: ET.Element, style: Optional[str]) -> Optional[str]:
    """The printed label a placeholder carries ("#12" -> "12", "#C" -> "C") when it was read in the book's style."""
    for r in d.iter("rehearsal"):
        t = (r.text or "").strip()[len(CIRCLE_TEXT):]
        if kind(t if t and (t.isdigit() or t.isalpha()) else None) == style and style:
            return t
    return None


def _fill(values: List[Optional[int]]) -> List[int]:
    """Labels for marks whose label was not read: the one after the last, or 1 where the sequence starts again."""
    out, last = [], 0
    for i, v in enumerate(values):
        if v is None:
            nxt = next((w for w in values[i + 1:] if w is not None), None)
            v = 1 if nxt is not None and nxt <= last else last + 1
        out.append(v)
        last = v
    return out


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
    style = book_style(m for n in order for m in parts[n]) if marks else None       # before the placeholders go
    for f in found:                                                  # placeholders nobody confirmed are noise
        f[2].remove(f[3])
    first_part = order[0]
    printed = [Counter(t for t in (_token(f[3], style) for f in g) if t).most_common(1) for _, g in marks]
    values = [ordinal(p[0][0]) if p else None for p in printed]
    texts = [p[0][0] if p else (letter(v) if style != "number" else str(v))
             for p, v in zip(printed, _fill(values))]
    for i, (bar, group) in enumerate(marks, 1):
        src = next((f for f in group if f[1] == bar and f[0] == first_part), None) \
            or next((f for f in group if f[1] == bar), group[0])
        d = src[3]
        for r in d.iter("rehearsal"):
            r.text = texts[i - 1]                    # the printed label when the parts read it
        for st in d.findall("staff"):
            d.remove(st)
        for off in d.findall("offset"):
            d.remove(off)
        d.set("placement", "above")
        insert_at(parts[first_part][bar], d, 0)           # a rehearsal mark sits at the start of its bar
    return [f"rehearsal: {len(marks)} circled number(s) agreed by {need}+ parts, written once as "
            f"{texts[0] if texts else ''}..{texts[-1] if texts else ''} at bars " + ", ".join(str(b + 1) for b, _ in marks)]
