"""Measure arithmetic on raw MusicXML ``<measure>`` elements.

Two lengths are in use, and they differ on purpose:

* :func:`sounding_length` -- what the notes and rests add up to. A ``<forward>`` (a gap) is
  *not* counted. Use it to ask "is this bar's content the right size?".
* :func:`extent_length` -- how far the bar reaches on the time axis, counting ``<forward>`` and
  rewinding on ``<backup>``. Use it to ask "how long does this bar play?", gaps included.

Both are in the measure's own ``<divisions>`` units. For a single-voice bar without gaps they are
equal.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Iterator, Optional, Tuple
from xml.etree import ElementTree as ET


def note_duration(el: ET.Element) -> int:
    """The ``<duration>`` of a note/forward/backup element (0 when absent, as for grace notes)."""
    return int(el.findtext("duration") or 0)


def _counts(note: ET.Element) -> bool:
    """A note that advances time: neither a chord member nor a grace note."""
    return note.find("chord") is None and note.find("grace") is None


def sounding_length(measure: ET.Element) -> int:
    """Sum of the note and rest durations (chord members and grace notes excluded).

    A bar with a ``<backup>`` holds several voices; its length is that of the longest voice.
    """
    if measure.find("backup") is not None:
        per_voice: dict = {}
        for n in measure.findall("note"):
            if _counts(n):
                v = n.findtext("voice") or "1"
                per_voice[v] = per_voice.get(v, 0) + note_duration(n)
        return max(per_voice.values(), default=0)
    return sum(note_duration(n) for n in measure.findall("note") if _counts(n))


def extent_length(measure: ET.Element) -> int:
    """The furthest point any voice reaches: notes and ``<forward>`` advance, ``<backup>`` rewinds."""
    pos = longest = 0
    for el in measure:
        if el.tag == "note":
            if el.find("chord") is None:
                pos += note_duration(el)
        elif el.tag == "forward":
            pos += note_duration(el)
        elif el.tag == "backup":
            pos -= note_duration(el)
        longest = max(longest, pos)
    return longest


MULTIREST_TAG = "./attributes/measure-style/multiple-rest"


def multirest_count(measure: ET.Element) -> int:
    """The ``<multiple-rest>`` count the bar is tagged with (0 when untagged or unreadable)."""
    tag = measure.find(MULTIREST_TAG)
    try:
        return int((tag.text or "").strip()) if tag is not None else 0
    except ValueError:
        return 0


def check_direction(text: str, color: str) -> ET.Element:
    """A ``<direction>`` placing a coloured ``text`` above the staff (the "check:" notes)."""
    d = ET.Element("direction", placement="above")
    ET.SubElement(ET.SubElement(d, "direction-type"), "words", color=color).text = text
    return d


def measures_with_capacity(part: ET.Element) -> Iterator[Tuple[ET.Element, int, Optional[int]]]:
    """Walk a MusicXML ``<part>`` tracking ``<divisions>`` and the time signature in force.

    Yields ``(measure, divisions, capacity)`` per measure, ``capacity`` being the bar length in
    divisions, or None before any time signature or when it is not a whole number of divisions.
    """
    div, beats, beat_type = 1, None, None
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            if (a.findtext("divisions") or "").strip().isdigit():
                div = int(a.findtext("divisions"))
            if (a.findtext("time/beats") or "").strip().isdigit() and (a.findtext("time/beat-type") or "").strip().isdigit():
                beats, beat_type = int(a.findtext("time/beats")), int(a.findtext("time/beat-type"))
        cap = Fraction(div * 4 * beats, beat_type) if beats else None
        yield m, div, (int(cap) if cap is not None and cap.denominator == 1 else None)
