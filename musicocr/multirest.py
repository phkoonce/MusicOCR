"""Engine-agnostic multi-measure-rest normalisation.

Two MusicXML spellings of "N bars rest" exist in the wild:

* **collapsed** (homr): one measure carrying
  ``<attributes><measure-style><multiple-rest>N</multiple-rest>``; printed bar
  numbers drift by N-1 after it. This is the pipeline's canonical form (the
  ``omr-eval`` / ``musicocr.alignment`` tools expect it).
* **expanded** (Copisteria given OCR digit boxes, MuseScore exports): the tagged
  measure *plus* N-1 following full-rest measures, so bar numbers match print.

``collapse_multirests`` and ``expand_multirests`` convert files in place between
the two. ``expand_multirests`` assumes collapsed input (it cannot tell a
collapsed multi-rest followed by an ordinary rest bar from an already-expanded
one), so don't run it on expanded files.
"""
from __future__ import annotations

import copy
from fractions import Fraction
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

_TAG = "./attributes/measure-style/multiple-rest"


def _count(measure: ET.Element) -> int:
    tag = measure.find(_TAG)
    try:
        return int((tag.text or "").strip()) if tag is not None else 0
    except ValueError:
        return 0


def _is_whole_rest(note: ET.Element) -> bool:
    rest = note.find("rest")
    return rest is not None and rest.get("measure") == "yes"


def _is_pure_rest_measure(measure: ET.Element) -> bool:
    """True if the measure holds only whole-measure rests (a barline is tolerated)."""
    notes = measure.findall("note")
    if not notes or not all(_is_whole_rest(n) for n in notes):
        return False
    return all(c.tag in ("note", "barline") for c in measure)


def _renumber(part: ET.Element) -> None:
    measures = part.findall("measure")
    if not measures:
        return
    try:
        n = int(measures[0].get("number", "1"))
    except ValueError:
        return
    for m in measures:
        m.set("number", str(n))
        n += 1


def _write(tree: ET.ElementTree, path: Path) -> None:
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def collapse_multirests(path: Path, log: Callable[[str], None] = print) -> int:
    """Fold each tagged multi-rest and its N-1 follower bars into one measure.

    Rewrites ``path`` in place and returns the number of bars removed. Followers
    are only dropped while they hold nothing but whole-measure rests; otherwise
    the rest are kept and a warning is logged.
    """
    tree = ET.parse(path)
    removed = 0
    for part in tree.getroot().findall("part"):
        measures = part.findall("measure")
        part_removed = 0
        i = 0
        while i < len(measures):
            n = _count(measures[i])
            if n < 2:
                i += 1
                continue
            head = measures[i]
            take = 0
            for follower in measures[i + 1:i + n]:
                if not _is_pure_rest_measure(follower):
                    break
                take += 1
            if take < n - 1:
                log(f"  WARNING measure {head.get('number')}: multiple-rest={n} but only "
                    f"{take} following bar(s) are pure rests; leaving the rest in place")
            for follower in measures[i + 1:i + 1 + take]:
                for bl in follower.findall("barline"):  # keep e.g. a closing barline
                    for old in head.findall("barline"):
                        if old.get("location", "right") == bl.get("location", "right"):
                            head.remove(old)
                    head.append(bl)
                part.remove(follower)
            if take:
                log(f"  measure {head.get('number')}: collapsed multi-rest of {n} "
                    f"(dropped {take} following bar(s))")
            part_removed += take
            i += 1 + take
        if part_removed:
            _renumber(part)
            removed += part_removed
    if removed:
        _write(tree, path)
    return removed


def expand_multirests(path: Path, log: Callable[[str], None] = print) -> int:
    """Inverse of :func:`collapse_multirests` for collapsed input.

    Adds N-1 whole-measure-rest bars after each tagged measure (bar length from
    ``divisions`` and the time signature in force); the tag stays on the first.
    Rewrites in place; returns the number of bars added.
    """
    tree = ET.parse(path)
    added = 0
    for part in tree.getroot().findall("part"):
        divisions = beats = beat_type = None
        part_added = 0
        for measure in list(part.findall("measure")):
            for d in measure.findall("./attributes/divisions"):
                if d.text:
                    divisions = int(d.text)
            for t in measure.findall("./attributes/time"):
                b, bt = t.find("beats"), t.find("beat-type")
                try:
                    if b is not None and bt is not None:
                        beats, beat_type = int(b.text), int(bt.text)
                except (TypeError, ValueError):
                    pass  # composite/symbolic time; leave previous
            n = _count(measure)
            if n < 2:
                continue
            if None in (divisions, beats, beat_type):
                log(f"  WARNING measure {measure.get('number')}: multiple-rest={n} but no "
                    "divisions/time signature seen yet; not expanding")
                continue
            dur = Fraction(divisions) * beats * 4 / beat_type
            if dur.denominator != 1:
                log(f"  WARNING measure {measure.get('number')}: bar length {dur} is not a "
                    "whole number of divisions; not expanding")
                continue
            dur_text = str(int(dur))
            rest_notes = [x for x in measure.findall("note") if _is_whole_rest(x)]
            if not rest_notes:
                rest_notes = [ET.fromstring(
                    '<note><rest measure="yes"/><duration/><voice>1</voice></note>')]
            idx = list(part).index(measure)
            for k in range(1, n):
                new = ET.Element("measure", {"number": str(k)})
                for tmpl in rest_notes:
                    note = copy.deepcopy(tmpl)
                    note.find("duration").text = dur_text
                    new.append(note)
                part.insert(idx + k, new)
            # a closing barline on the tagged bar belongs on the last expanded bar
            for bl in measure.findall("barline"):
                measure.remove(bl)
                new.append(bl)
            part_added += n - 1
            log(f"  measure {measure.get('number')}: expanded multi-rest of {n} "
                f"(added {n - 1} bar(s) of {dur_text} divisions)")
        if part_added:
            _renumber(part)
            added += part_added
    if added:
        _write(tree, path)
    return added


def apply_multirest_form(path: Path, form: str, log: Callable[[str], None] = print) -> int:
    """Dispatch on the ``multirest_form`` option (``collapse`` | ``expand`` | ``keep``)."""
    if form == "collapse":
        return collapse_multirests(path, log)
    if form == "expand":
        return expand_multirests(path, log)
    return 0
