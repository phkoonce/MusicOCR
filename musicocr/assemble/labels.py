"""Printed rehearsal labels, numbers or letters, as the parts carry them.

An edition numbers its rehearsal marks (1, 2, 3 ...) or lettered them (A, B, C ...); both restart in each movement
or piece. Marks found by shape are ``<rehearsal>#X</rehearsal>`` placeholders (``#`` alone when nothing could be
read inside the frame); marks read from a PDF text layer are plain ``<rehearsal>X</rehearsal>``. A book uses one
style, so the style most of its marks are read in is *the* style, and a token of the other kind is a misread (an
``8`` for a ``B``) and counts as unread.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Iterable, List, Optional

MARK = "#"                              # musicocr.text.layer.CIRCLE_TEXT
_TOKEN = re.compile(r"^(\d{1,3}|[A-Z]{1,2})$")


def kind(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return "number" if token.isdigit() else "letter"


def ordinal(token: Optional[str]) -> Optional[int]:
    """12 -> 12, A -> 1, Z -> 26, AA -> 27 (the same sequence MuseScore letters run in)."""
    if not token:
        return None
    if token.isdigit():
        return int(token)
    n = 0
    for ch in token:
        n = n * 26 + ord(ch) - 64
    return n


def measure_tokens(measure: ET.Element) -> List[Optional[str]]:
    """The rehearsal marks of a bar: their label, None for a frame whose label was not read."""
    out: List[Optional[str]] = []
    for d in measure.findall("direction"):
        for r in d.iter("rehearsal"):
            t = (r.text or "").strip()
            if t.startswith(MARK):
                t = t[len(MARK):]
                out.append(t if _TOKEN.match(t) else None)
            elif _TOKEN.match(t):
                out.append(t)
    return out


def book_style(measures: Iterable[ET.Element]) -> Optional[str]:
    """``"number"`` or ``"letter"``, whichever more of the read labels are; None when none was read."""
    votes = Counter(kind(t) for m in measures for t in measure_tokens(m))
    votes.pop(None, None)
    if not votes:
        return None
    return max(sorted(votes, reverse=True), key=lambda k: votes[k])     # a tie goes to "number"


def label_values(measure: ET.Element, style: Optional[str]) -> List[Optional[int]]:
    """Each mark of a bar as its ordinal, None where it was not read or is not in the book's style."""
    return [ordinal(t) if t and kind(t) == style else None for t in measure_tokens(measure)]
