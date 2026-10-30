"""Slurs between two adjacent notes of the same pitch are ties.

OMR models often read a tie as a slur. A slur that starts on one note and stops on the very next note
of its voice (no note, chord or rest between) at the same pitch cannot be a slur in practice: that is
a tie, and it is rewritten as one (``<tie>`` + ``<tied>``) so playback and the note count come out right.
Slurs that already come with a tie only lose the slur. Chords and grace notes are left alone.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from musicocr.musicxml import write_tree


def _pitch(n: ET.Element) -> Optional[Tuple[str, str, str]]:
    p = n.find("pitch")
    if p is None:
        return None
    return (p.findtext("step") or "", p.findtext("alter") or "0", p.findtext("octave") or "")


def _slur(n: ET.Element, kind: str) -> List[ET.Element]:
    notations = n.find("notations")
    return [] if notations is None else [s for s in notations.findall("slur") if s.get("type") == kind]


def _add_tie(n: ET.Element, kind: str) -> None:
    """``<tie type=kind>`` after the note's duration and ``<tied type=kind>`` in its notations (if absent)."""
    if not any(t.get("type") == kind for t in n.findall("tie")):
        idx = next((i + 1 for i, e in enumerate(n) if e.tag == "duration"), len(n))
        n.insert(idx, ET.Element("tie", type=kind))
    notations = n.find("notations")
    if notations is None:
        notations = ET.SubElement(n, "notations")
    if not any(t.get("type") == kind for t in notations.findall("tied")):
        notations.insert(0, ET.Element("tied", type=kind))


def _drop_slur(n: ET.Element, slur: ET.Element) -> None:
    notations = n.find("notations")
    notations.remove(slur)
    if len(notations) == 0:
        n.remove(notations)


def fix_tree(root: ET.Element, log: Callable[[str], None] = print) -> int:
    """Turn same-pitch adjacent slurs into ties; returns how many were changed."""
    changed = 0
    for part in root.findall("part"):
        events: Dict[tuple, List[ET.Element]] = {}      # (staff, voice) -> its notes and rests in order
        chorded = set()                                 # notes that are part of a chord
        for m in part.findall("measure"):
            prev = None
            for n in m.findall("note"):
                if n.find("chord") is None:
                    events.setdefault((n.findtext("staff"), n.findtext("voice")), []).append(n)
                    prev = n
                elif prev is not None:
                    chorded.update((id(prev), id(n)))
        for seq in events.values():
            for s, e in zip(seq, seq[1:]):
                if _pitch(s) is None or _pitch(s) != _pitch(e) or id(s) in chorded or id(e) in chorded:
                    continue
                if s.find("grace") is not None or e.find("grace") is not None:
                    continue
                starts, stops = _slur(s, "start"), _slur(e, "stop")
                nums = {x.get("number", "1") for x in starts} & {x.get("number", "1") for x in stops}
                if not nums:
                    continue
                k = min(nums)
                for x in [x for x in starts if x.get("number", "1") == k]:
                    _drop_slur(s, x)
                for x in [x for x in stops if x.get("number", "1") == k]:
                    _drop_slur(e, x)
                _add_tie(s, "start")
                _add_tie(e, "stop")
                changed += 1
    return changed


def fix_file(path: Path, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place; returns the number of slurs turned into ties."""
    tree = ET.parse(path)
    n = fix_tree(tree.getroot(), log)
    if n:
        write_tree(tree, path)
    return n
