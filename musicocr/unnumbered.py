"""Aligning parts that print no bar numbers (an ensemble book: four horns, one piece).

``combine`` normally lines the parts up by the printed bar numbers. Without them every part is one
run of bars and the only evidence left is what the parts play: in an ensemble the parts mostly move
together, so a part that lost (or invented) a few bars is aligned to the others by the *rhythm* of its
bars. The rest-only pattern ``align_to_target`` uses is no help here, since a quartet rarely rests in
sync.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import FrozenSet, List, Optional, Sequence

TICKS = 12          # onset resolution: twelfths of a quarter note (triplets and sixteenths both land on it)
INDEL_BASE = 0.3    # an inserted/dropped bar costs this plus up to 0.7 for the bar that has notes
INSERT_TIEBREAK = 1e-4


def onsets(measure: ET.Element, divisions: int) -> FrozenSet[int]:
    """Where the notes of a bar start (ticks from the bar's start); empty for a rest-only bar."""
    t, out = 0, set()
    for e in measure:
        if e.tag == "note":
            if e.find("chord") is None:
                if e.find("rest") is None and e.find("grace") is None:
                    out.add(round(t * TICKS / divisions))
                t += int(e.findtext("duration") or 0)
        elif e.tag == "backup":
            t -= int(e.findtext("duration") or 0)
        elif e.tag == "forward":
            t += int(e.findtext("duration") or 0)
    return frozenset(out)


def dissimilarity(a: FrozenSet[int], b: FrozenSet[int]) -> float:
    """0 (the same onsets) .. 1 (nothing in common); two rest bars agree, a rest and a played bar do not."""
    if not a and not b:
        return 0.0
    return 1.0 - len(a & b) / len(a | b)


def align_by_rhythm(src: Sequence[FrozenSet[int]], ref: Sequence[Sequence[FrozenSet[int]]]) -> List[Optional[int]]:
    """Map each of ``len(ref)`` slots to a source bar index (None = synthesise a rest).

    ``ref[j]`` holds the other parts' onsets at slot j. A bar matched to a slot costs its mean
    dissimilarity to them; as in ``combine.align_to_target`` only the length difference's worth of
    indels is allowed, so a one-bar misread never becomes a rearrangement. Dropping a rest bar and
    inserting where the others rest are cheap.
    """
    n, m = len(src), len(ref)
    allow_del, allow_ins = n >= m, n <= m
    INF = 1e18

    def match(i: int, j: int) -> float:
        r = ref[j]
        return sum(dissimilarity(src[i], x) for x in r) / len(r) if r else 0.5

    played_ref = [sum(1 for x in r if x) / len(r) if r else 0.5 for r in ref]
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    B: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            d = D[i][j]
            if d >= INF:
                continue
            if i < n and j < m:
                c = d + match(i, j)
                if c < D[i + 1][j + 1]:
                    D[i + 1][j + 1], B[i + 1][j + 1] = c, "m"
            if i < n and allow_del:
                c = d + INDEL_BASE + (1 - INDEL_BASE) * (1.0 if src[i] else 0.0)
                if c < D[i + 1][j]:
                    D[i + 1][j], B[i + 1][j] = c, "d"
            if j < m and allow_ins:
                c = d + INDEL_BASE + (1 - INDEL_BASE) * played_ref[j] + INSERT_TIEBREAK * (m - j) / max(m, 1)
                if c < D[i][j + 1]:
                    D[i][j + 1], B[i][j + 1] = c, "i"
    slots: List[Optional[int]] = [None] * m
    i, j = n, m
    while i or j:
        op = B[i][j]
        if op == "m":
            slots[j - 1] = i - 1
            i, j = i - 1, j - 1
        elif op == "d":
            i -= 1
        else:
            j -= 1
    return slots
