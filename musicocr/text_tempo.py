"""Tempo marks across the parts of a combined score (docs/text-layer-design.md 5.6).

Every part prints the same tempo marks, and the text layer reads them per part, so a combined score
would carry N copies, and a part whose OCR missed one would disagree with the rest. Per bar the
plurality reading (beat unit, dot, bpm) wins and is written once, in the first part (like the
transposition declaration); the other parts' copies are removed. Tempo *words* ("Sl. Faster") travel
with the direction. Tempo changes ("rit.") and expression words stay per part.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

Key = Tuple[str, bool, int]          # beat unit, dotted, bpm


def tempo_key(d: ET.Element) -> Optional[Key]:
    """(beat unit, dotted, bpm) of a direction carrying a metronome mark and ``<sound tempo>``."""
    met = d.find("direction-type/metronome")
    snd = d.find("sound")
    if met is None or snd is None or not snd.get("tempo"):
        return None
    try:
        bpm = int(float(snd.get("tempo")))
    except ValueError:
        return None
    return (met.findtext("beat-unit") or "quarter", met.find("beat-unit-dot") is not None, bpm)


def _insert_at(m: ET.Element) -> int:
    """After the bar's attributes and the directions already there, before its first note."""
    idx = 0
    for i, el in enumerate(list(m)):
        if el.tag in ("attributes", "direction", "print"):
            idx = i + 1
        elif el.tag in ("note", "forward", "backup"):
            break
    return idx


def unify_tempo(parts: Dict[int, List[ET.Element]]) -> List[str]:
    """Keep one tempo mark per bar: the plurality reading, in the first part. Returns report lines."""
    order = sorted(parts)
    if len(order) < 2:
        return []
    lines: List[str] = []
    moved = removed = 0
    for b in range(max(len(parts[n]) for n in order)):
        found: List[Tuple[int, ET.Element, ET.Element, Key]] = []
        for n in order:
            if b >= len(parts[n]):
                continue
            for d in parts[n][b].findall("direction"):
                k = tempo_key(d)
                if k:
                    found.append((n, parts[n][b], d, k))
        if not found:
            continue
        votes = Counter(k for *_, k in found)
        best = max(votes.values())
        win = next(k for *_, k in found if votes[k] == best)         # a tie goes to the earliest part
        if len(votes) > 1:
            lines.append(f"  bar {b + 1}: tempo marks disagree across parts "
                         + ", ".join(f"{u}{'.' if dot else ''}={bpm} x{c}" for (u, dot, bpm), c in votes.items())
                         + f" -> {win[2]} kept -- check")
        src = next(d for _, _, d, k in found if k == win and d.find("direction-type/words") is not None) \
            if any(k == win and d.find("direction-type/words") is not None for _, _, d, k in found) \
            else next(d for _, _, d, k in found if k == win)
        keep = copy.deepcopy(src)
        for st in keep.findall("staff"):
            keep.remove(st)
        first = parts[order[0]][b]
        for _, m, d, _ in found:
            m.remove(d)
            removed += 1
        first.insert(_insert_at(first), keep)
        moved += 1
    if moved:
        lines.insert(0, f"tempo: {moved} bar(s) carry a tempo mark, written once in the first part "
                        f"({removed} per-part copies merged)")
    return lines
