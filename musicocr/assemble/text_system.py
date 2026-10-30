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
CHECK_MIN_PARTS = 3     # ... or, when every copy is a flagged "check:" reading, this many (a misread is rarely repeated)
BEAT_TOLERANCE = 1      # copies up to this many quarter notes apart still count as the same text (OCR attaches a
#                         word to the nearest note, which can be the one before or after the one it is printed over)
DIV = 10080             # <divisions> per quarter in every combined part (musicocr.assemble.alignment.spec.DIV)


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


def check_key(d: ET.Element) -> Optional[str]:
    """Normalised words of a flagged unknown text ("check: <words>", the text layer's italic words it could
    not classify); ``None`` for any other direction, including the red "check: 2 beats" bar flags."""
    if d.tag != "direction" or d.get("placement", "above") != "above":
        return None
    types = d.findall("direction-type")
    if len(types) != 1 or len(types[0]) != 1 or types[0][0].tag != "words":
        return None
    w = types[0][0]
    text = "".join(w.itertext()).strip()
    if not text.lower().startswith("check:") or "italic" not in (w.get("font-style") or ""):
        return None
    return re.sub(r"[^a-z0-9]+", "", text[6:].lower()) or None


def text_keys(measure: ET.Element) -> FrozenSet[str]:
    """The texts above a bar, for the alignment hints."""
    return frozenset(k for d in measure.findall("direction") if (k := text_key(d)))


def position(measure: ET.Element, el: ET.Element) -> int:
    """Ticks from the start of the bar to where direction ``el`` sits (its place among the notes plus its offset)."""
    t = 0
    for e in measure:
        if e is el:
            return t + int(float(el.findtext("offset") or 0))
        if e.tag == "note" and e.find("chord") is None:
            t += int(e.findtext("duration") or 0)
        elif e.tag == "backup":
            t -= int(e.findtext("duration") or 0)
        elif e.tag == "forward":
            t += int(e.findtext("duration") or 0)
    return t


def insert_at(measure: ET.Element, el: ET.Element, ticks: int) -> None:
    """Put direction ``el`` in the bar before the first note that starts at ``ticks`` or later (after the
    attributes and directions when that is the start of the bar)."""
    t, idx = 0, None
    for i, e in enumerate(list(measure)):
        if e.tag in ("attributes", "direction", "print") and t == 0 and idx is None:
            continue
        if e.tag == "note" and e.find("chord") is None:
            if t >= ticks:
                idx = i
                break
            t += int(e.findtext("duration") or 0)
        elif e.tag == "backup":
            t -= int(e.findtext("duration") or 0)
        elif e.tag == "forward":
            t += int(e.findtext("duration") or 0)
    if idx is None:
        idx = len(measure)
    measure.insert(idx, el)


def _majority(hits: List[tuple], tol: int) -> tuple:
    """The position most copies agree on within ``tol`` ticks, and the copies that agree there (ties: the
    most exact matches, then the earliest position)."""
    best = None
    for p in sorted({h[3] for h in hits}):
        near = [h for h in hits if abs(h[3] - p) <= tol]
        rank = (len(near), sum(1 for h in near if h[3] == p), -p)
        if best is None or rank > best[0]:
            best = (rank, p, near)
    return best[1], best[2]


def unify_system_text(parts: Dict[int, List[ET.Element]]) -> List[str]:
    """Per bar, make the texts that ``MIN_PARTS`` or more parts print into one system text in the first
    part. Copies up to ``BEAT_TOLERANCE`` beats apart count as one text, written at the beat most copies
    agree on; flagged "check:" readings need ``CHECK_MIN_PARTS`` agreeing parts and lose their flag.
    Returns report lines."""
    order = sorted(parts)
    if len(order) < MIN_PARTS:
        return []
    tol = BEAT_TOLERANCE * DIV
    moved = removed = upgraded = 0
    for b in range(max(len(parts[n]) for n in order)):
        found: Dict[str, List[tuple]] = {}
        for n in order:
            if b >= len(parts[n]):
                continue
            seen = set()
            for d in parts[n][b].findall("direction"):
                k = text_key(d) or check_key(d)
                if k and k not in seen:                # one copy per part and text
                    seen.add(k)
                    found.setdefault(k, []).append((n, parts[n][b], d, position(parts[n][b], d)))
        for k, all_hits in found.items():
            at, hits = _majority(all_hits, tol)
            plain = [h for h in hits if text_key(h[2])]
            need = MIN_PARTS if plain else min(CHECK_MIN_PARTS, len(order))
            if len(hits) < need:
                continue
            pool = plain or hits
            first = next((h for h in pool if h[0] == order[0]), pool[0])
            keep = first[2]
            if not plain:                              # agreed unknown text: trust it, drop the flag
                for w in keep.iter("words"):
                    w.text = w.text.strip()[6:].strip()
                    w.attrib.pop("color", None)
                upgraded += 1
            keep.set("system", "only-top")
            for st in keep.findall("staff"):
                keep.remove(st)
            for n, m, d, _ in hits:
                if d is not keep:
                    m.remove(d)
                    removed += 1
            m0 = parts[order[0]][b]
            if first[1] is not m0 or first[3] != at:   # not in the first part, or not at the agreed beat: move it
                first[1].remove(keep)
                for off in keep.findall("offset"):
                    keep.remove(off)
                insert_at(m0, keep, at)
            moved += 1
    if not moved:
        return []
    lines = [f"text: {moved} text(s) printed over the same bar in {MIN_PARTS}+ parts are system text, "
             f"written once ({removed} per-part copies merged)"]
    if upgraded:
        lines.append(f"text: {upgraded} of them were flagged 'check:' readings that {CHECK_MIN_PARTS}+ parts agree on")
    return lines
