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


def position(measure: ET.Element, el: Optional[ET.Element]) -> int:
    """Ticks from the start of the bar to where direction ``el`` sits (its place among the notes plus its offset);
    for ``None``, the length of the bar."""
    t = end = 0
    for e in measure:
        if e is el:
            return t + int(float(el.findtext("offset") or 0))
        if e.tag == "note" and e.find("chord") is None:
            t += int(float(e.findtext("duration") or 0))
        elif e.tag == "backup":
            t -= int(float(e.findtext("duration") or 0))
        elif e.tag == "forward":
            t += int(float(e.findtext("duration") or 0))
        end = max(end, t)
    return end if el is None else t


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


def _gap(a: tuple, b: tuple) -> float:
    """Ticks between two copies (part, bar, measure, direction, position, bar length) of a text: within a bar
    the difference of their positions, across a barline from the earlier copy to the end of its bar plus the later
    copy's position; copies more than a bar apart are infinitely far."""
    if a[1] == b[1]:
        return abs(a[4] - b[4])
    if abs(a[1] - b[1]) == 1:
        lo, hi = (a, b) if a[1] < b[1] else (b, a)
        return max(lo[5] - lo[4], 0) + hi[4]
    return float("inf")


def _majority(hits: List[tuple], tol: int) -> tuple:
    """The copy most others agree with within ``tol`` ticks (even across a barline), and the copies that
    agree there, at most one per part (ties: the most exact matches, then one that is not at the very end
    of its bar -- a word printed on the barline belongs to the bar after it -- then the earliest)."""
    best = None
    for c in hits:
        near = {}
        for h in sorted(hits, key=lambda h: _gap(c, h)):
            if _gap(c, h) <= tol and h[0] not in near:
                near[h[0]] = h
        near = list(near.values())
        at_end = c[5] - c[4] <= tol and c[4] > 0
        rank = (len(near), sum(1 for h in near if _gap(c, h) == 0), not at_end, -c[1], -c[4])
        if best is None or rank > best[0]:
            best = (rank, c, near)
    return best[1], best[2]


def unify_system_text(parts: Dict[int, List[ET.Element]]) -> List[str]:
    """Make the texts that ``MIN_PARTS`` or more parts print over the same place into one system text in
    the first part. Copies up to ``BEAT_TOLERANCE`` beats apart count as one text -- also across a barline, as
    OCR attaches a word printed there to the last note before it or the first after it -- written where the
    most copies agree; flagged "check:" readings need ``CHECK_MIN_PARTS`` agreeing parts and lose their flag.
    Returns report lines."""
    order = sorted(parts)
    if len(order) < MIN_PARTS:
        return []
    tol = BEAT_TOLERANCE * DIV
    moved = removed = upgraded = 0
    found: Dict[str, List[tuple]] = {}
    for n in order:
        for b, m in enumerate(parts[n]):
            seen = set()
            for d in m.findall("direction"):
                k = text_key(d) or check_key(d)
                if k and k not in seen:                    # one copy per part, bar and text
                    seen.add(k)
                    found.setdefault(k, []).append((n, b, m, d, position(m, d), position(m, None)))
    for k, pool_all in found.items():
        while pool_all:
            centre, hits = _majority(pool_all, tol)
            pool_all = [h for h in pool_all if h not in hits]
            plain = [h for h in hits if text_key(h[3])]
            need = MIN_PARTS if plain else min(CHECK_MIN_PARTS, len(order))
            if len(hits) < need:
                continue
            at, bar = centre[4], centre[1]
            pool = plain or hits
            first = next((h for h in pool if h[0] == order[0]), pool[0])
            keep = first[3]
            if not plain:                                  # agreed unknown text: trust it, drop the flag
                for w in keep.iter("words"):
                    w.text = w.text.strip()[6:].strip()
                    w.attrib.pop("color", None)
                upgraded += 1
            keep.set("system", "only-top")
            for st in keep.findall("staff"):
                keep.remove(st)
            for _, _, m, d, _, _ in hits:
                if d is not keep:
                    m.remove(d)
                    removed += 1
            m0 = parts[order[0]][bar]
            if first[2] is not m0 or first[4] != at:       # not in the first part, or not at the agreed place: move it
                first[2].remove(keep)
                for off in keep.findall("offset"):
                    keep.remove(off)
                insert_at(m0, keep, at)
            moved += 1
    if not moved:
        return []
    lines = [f"text: {moved} text(s) printed over the same place in {MIN_PARTS}+ parts are system text, "
             f"written once ({removed} per-part copies merged)"]
    if upgraded:
        lines.append(f"text: {upgraded} of them were flagged 'check:' readings that {CHECK_MIN_PARTS}+ parts agree on")
    return lines
