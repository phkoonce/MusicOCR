"""MusicXML building blocks shared by both alignment front-ends.

All functions operate on ``xml.etree.ElementTree`` elements and generate
measures in ``DIV`` divisions per quarter (see :mod:`musicocr.alignment.spec`).
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Tuple, Union

from .spec import DIV, bar_len

Clef = Tuple[str, str]  # (sign, line)


def is_rest_only(m: ET.Element) -> bool:
    """True if every ``<note>`` in the measure is a rest (also for empty measures)."""
    return all(n.find("rest") is not None for n in m.findall("note") if n.find("cue") is None)


def measure_total(m: ET.Element) -> int:
    """Sum of note durations (chords and grace notes excluded), in the measure's divisions.
    A bar with a ``<backup>`` holds several voices: its length is that of the longest voice."""
    if m.find("backup") is not None:
        per: dict = {}
        for n in m.findall("note"):
            if n.find("chord") is None and n.find("grace") is None:
                v = n.findtext("voice") or "1"
                per[v] = per.get(v, 0) + int(n.findtext("duration") or 0)
        return max(per.values(), default=0)
    return sum(int(n.findtext("duration") or 0) for n in m.findall("note")
               if n.find("chord") is None and n.find("grace") is None)


def add_attributes(m: ET.Element, *, divisions: Optional[int] = None,
                   key: Optional[int] = None, meter: Optional[Tuple[int, int]] = None,
                   clef: Optional[Clef] = None,
                   transpose: Optional[Tuple[int, int]] = None) -> Optional[ET.Element]:
    """Append an ``<attributes>`` element with only the given pieces.

    Returns None (and adds nothing) when no piece is given.  ``transpose`` is
    ``(diatonic, chromatic)``.
    """
    if (divisions, key, meter, clef, transpose) == (None,) * 5:
        return None
    at = ET.SubElement(m, "attributes")
    if divisions is not None:
        ET.SubElement(at, "divisions").text = str(divisions)
    if key is not None:
        ET.SubElement(ET.SubElement(at, "key"), "fifths").text = str(key)
    if meter is not None:
        t = ET.SubElement(at, "time")
        ET.SubElement(t, "beats").text = str(meter[0])
        ET.SubElement(t, "beat-type").text = str(meter[1])
    if clef is not None:
        c = ET.SubElement(at, "clef")
        ET.SubElement(c, "sign").text = clef[0]
        ET.SubElement(c, "line").text = clef[1]
    if transpose is not None:
        tr = ET.SubElement(at, "transpose")
        ET.SubElement(tr, "diatonic").text = str(transpose[0])
        ET.SubElement(tr, "chromatic").text = str(transpose[1])
    return at


def direction(kind: str, *args: Any) -> ET.Element:
    """Build a ``<direction>``: ``("tempo", text, bpm)``, ``("rehearsal", letter)``
    or ``("words", text)`` (any unknown kind is treated as words)."""
    d = ET.Element("direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    ET.SubElement(dt, "rehearsal" if kind == "rehearsal" else "words").text = args[0]
    if kind == "tempo":
        ET.SubElement(d, "sound", tempo=str(args[1]))
    return d


def rest_measure_notes(m: ET.Element, length: int, full: bool = True) -> ET.Element:
    """Append one rest to ``m``: a whole-measure rest if ``full`` else a quarter-typed rest."""
    n = ET.SubElement(m, "note")
    if full:
        ET.SubElement(n, "rest", measure="yes")
    else:
        ET.SubElement(n, "rest")
    ET.SubElement(n, "duration").text = str(length)
    if not full:
        ET.SubElement(n, "type").text = "quarter"
    return n


def rest_measure(number: int, length: int, full: bool = True) -> ET.Element:
    """A new ``<measure>`` holding a single rest of ``length`` divisions."""
    m = ET.Element("measure", number=str(number))
    rest_measure_notes(m, length, full)
    return m


def copy_notes(src: ET.Element, dst: ET.Element, src_divisions: int = DIV) -> None:
    """Deep-copy ``src``'s notes into ``dst`` (stems dropped, durations rescaled to ``DIV``)."""
    for ch in src:
        if ch.tag not in ("note", "backup", "forward"):
            continue
        nn = copy.deepcopy(ch)
        for e in nn.findall("stem"):
            nn.remove(e)
        du = nn.find("duration")
        if du is not None and src_divisions != DIV:
            du.text = str(round(int(du.text) * DIV / src_divisions))
        dst.append(nn)


def apply_marks(measures: Iterable[ET.Element], marks: Mapping[Any, Any], part_id: Any) -> None:
    """Insert the spec's ``MARKS`` directions for ``part_id`` (and ``"all"``) into measures,
    after any ``<attributes>`` and before the notes."""
    per_part = marks.get(part_id, {})
    for_all = marks.get("all", {})
    for m in measures:
        num = int(m.get("number"))
        extras = list(per_part.get(num, [])) + list(for_all.get(num, []))
        idx = 1 if m.find("attributes") is not None else 0
        for kind, *args in extras:
            m.insert(idx, direction(kind, *args))
            idx += 1


def add_final_barline(measure: ET.Element) -> None:
    """Append a light-heavy right barline to ``measure``."""
    bl = ET.SubElement(measure, "barline", location="right")
    ET.SubElement(bl, "bar-style").text = "light-heavy"


def build_score(spec: Any, parts: Mapping[int, List[ET.Element]],
                infos: Mapping[int, Mapping[str, Any]]) -> ET.Element:
    """Assemble a ``score-partwise`` from built measures.

    ``infos[pid]`` supplies ``name``, ``abbr`` and ``program`` for the part list.
    """
    score = ET.Element("score-partwise", version="3.1")
    ET.SubElement(ET.SubElement(score, "work"), "work-title").text = spec.TITLE
    if spec.COMPOSER:
        ET.SubElement(ET.SubElement(score, "identification"), "creator",
                      type="composer").text = spec.COMPOSER
    pl = ET.SubElement(score, "part-list")
    for pid, info in infos.items():
        sp = ET.SubElement(pl, "score-part", id=f"P{pid}")
        ET.SubElement(sp, "part-name").text = info["name"]
        ET.SubElement(sp, "part-abbreviation").text = info["abbr"]
        if info.get("sound"):       # MuseScore picks the playback voice from instrument-sound
            si = ET.SubElement(sp, "score-instrument", id=f"P{pid}-I1")
            ET.SubElement(si, "instrument-name").text = info["name"]
            ET.SubElement(si, "instrument-abbreviation").text = info["abbr"]
            ET.SubElement(si, "instrument-sound").text = info["sound"]
        mi = ET.SubElement(sp, "midi-instrument", id=f"P{pid}-I1")
        ET.SubElement(mi, "midi-channel").text = str(pid)
        ET.SubElement(mi, "midi-program").text = str(info["program"])
    marks = getattr(spec, "MARKS", {})
    for pid, ms in parts.items():
        pe = ET.SubElement(score, "part", id=f"P{pid}")
        apply_marks(ms, marks, pid)
        pe.extend(ms)
        add_final_barline(ms[-1])
    return score


def write_score(score: ET.Element, out: Union[str, Path]) -> Path:
    """Write ``score`` as MusicXML 3.1 (creating parent directories)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(score, space="  ")
    out.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
                   '"http://www.musicxml.org/dtds/partwise.dtd">\n'
                   + ET.tostring(score, encoding="unicode") + "\n")
    return out


def check_length(m: ET.Element, spec: Any, bar: int) -> Optional[Tuple[float, float]]:
    """None if ``m``'s content fills printed ``bar``, else ``(actual_beats, expected_beats)``."""
    tot, want = measure_total(m), bar_len(spec, bar)
    return None if tot == want else (tot / DIV, want / DIV)
