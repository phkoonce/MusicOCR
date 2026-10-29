"""Text above the staff that every part prints, as system text (docs/text-layer-design.md 5.6).

"Allegro", "a tempo" or "rit." printed over the same bar in several parts is a mark of the whole
score, not of one staff. MuseScore imports a ``<words>`` direction as *staff text*, but with
``system="only-top"`` (MusicXML 4.0) as *system text*, written once above the top staff. So a text
that the parts agree on is kept once, in the first part, flagged as system text; the other parts'
copies go. Texts only some parts print (cues, "Solo") stay staff text.

The same agreement is evidence for the alignment of parts without printed bar numbers
(``text_keys`` / ``musicocr.assemble.unnumbered``): a bar with "rit." belongs where the other parts have it.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, FrozenSet, List, Optional

MIN_PARTS = 2           # a text is system text when this many parts print it over the same bar


def text_key(d: ET.Element) -> Optional[str]:
    """Normalised words of an above-staff, words-only direction (``None`` for anything else: dynamics,
    hairpins, metronome marks, flagged "check:" notes)."""
    if d.tag != "direction" or d.get("placement", "above") != "above":
        return None
    types = d.findall("direction-type")
    if not types or any(len(t) != 1 or t[0].tag != "words" for t in types):
        return None
    if any(w.get("color") for t in types for w in t):
        return None
    text = " ".join("".join(t[0].itertext()) for t in types).lower()
    if text.startswith("check:"):
        return None
    key = re.sub(r"[^a-z0-9]+", "", text)
    return key or None


def text_keys(measure: ET.Element) -> FrozenSet[str]:
    """The texts above a bar, for the alignment hints."""
    return frozenset(k for d in measure.findall("direction") if (k := text_key(d)))


def unify_system_text(parts: Dict[int, List[ET.Element]]) -> List[str]:
    """Per bar, make the texts that ``MIN_PARTS`` or more parts print into one system text in the first
    part. Returns report lines."""
    order = sorted(parts)
    if len(order) < MIN_PARTS:
        return []
    moved = removed = 0
    for b in range(max(len(parts[n]) for n in order)):
        found: Dict[str, List[tuple]] = {}
        for n in order:
            if b >= len(parts[n]):
                continue
            for d in parts[n][b].findall("direction"):
                k = text_key(d)
                if k:
                    found.setdefault(k, []).append((n, parts[n][b], d))
        for k, hits in found.items():
            if len({n for n, *_ in hits}) < MIN_PARTS:
                continue
            first = next((h for h in hits if h[0] == order[0]), hits[0])
            keep = first[2]
            keep.set("system", "only-top")
            for st in keep.findall("staff"):
                keep.remove(st)
            for n, m, d in hits:
                if d is not keep:
                    m.remove(d)
                    removed += 1
            if first[1] is not parts[order[0]][b]:        # first part has no copy: move it there
                first[1].remove(keep)
                m0 = parts[order[0]][b]
                idx = 0
                for i, el in enumerate(list(m0)):
                    if el.tag in ("attributes", "direction", "print"):
                        idx = i + 1
                    elif el.tag in ("note", "forward", "backup"):
                        break
                m0.insert(idx, keep)
            moved += 1
    if not moved:
        return []
    return [f"text: {moved} text(s) printed over the same bar in {MIN_PARTS}+ parts are system text, "
            f"written once ({removed} per-part copies merged)"]

