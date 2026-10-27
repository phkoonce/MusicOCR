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


def _set_voice(note: ET.Element, voice: str) -> None:
    v = note.find("voice")
    if v is None:
        v = ET.Element("voice")
        pos = 0
        for i, ch in enumerate(list(note)):
            if ch.tag in ("pitch", "rest", "unpitched", "duration", "tie", "instrument", "footnote", "level"):
                pos = i + 1
        note.insert(pos, v)
    v.text = voice


def cue_notes_over_rest(measure: ET.Element, capacity: Optional[int]) -> bool:
    """A whole-bar rest plus ordinary-looking notes that last exactly one more bar: the notes are
    cue notes printed over the rest. Keep the rest in voice 1 and move the rest to voice 2
    (marked ``<cue/>``) after a ``<backup>``; True if the bar changed."""
    if capacity is None or measure.find("backup") is not None or measure.find("forward") is not None:
        return False
    notes = [n for n in measure.findall("note") if n.find("grace") is None and n.find("chord") is None]
    if any(n.find("time-modification") is not None or not (n.findtext("duration") or "").strip().isdigit() for n in notes):
        return False
    whole = [n for n in notes if n.find("rest") is not None and n.find("type") is None
             and int(n.findtext("duration")) == capacity]
    if len(whole) != 1 or len([n for n in notes if n.find("rest") is not None and n.find("type") is None]) != 1:
        return False
    if sum(int(n.findtext("duration")) for n in notes) != 2 * capacity:
        return False
    if not any(n.find("pitch") is not None for n in notes):
        return False
    all_notes = measure.findall("note")
    # keep the rest (and any chord/grace notes attached to it) in voice 1; the rest of the bar's notes
    # that follow it, and those before it, form the cue line
    cue = [n for n in all_notes if n is not whole[0]]
    for n in cue:
        measure.remove(n)
        n.attrib.pop("print-object", None)
        if n.find("cue") is None:
            pos = 1 if n.find("grace") is not None else 0
            n.insert(pos, ET.Element("cue"))
        _set_voice(n, "2")
    _set_voice(whole[0], "1")
    at = list(measure).index(whole[0]) + 1
    back = ET.Element("backup")
    ET.SubElement(back, "duration").text = str(capacity)
    measure.insert(at, back)
    for i, n in enumerate(cue):
        measure.insert(at + 1 + i, n)
    return True


def fix_tree(root: ET.Element, log: Callable[[str], None] = print) -> int:
    """Fix every part of a ``score-partwise``; returns the number of bars changed."""
    fixed = 0
    for part in root.findall("part"):
        for m, div, cap in measures_with_capacity(part):
            if cue_notes_over_rest(m, cap):
                log(f"  measure {m.get('number')}: cue notes moved to voice 2")
                fixed += 1
            elif fix_measure(m, cap):
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
