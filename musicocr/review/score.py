"""Bar-level reading and rewriting of a MuseScore 4 score."""
from __future__ import annotations

import hashlib
import re
from fractions import Fraction
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

Key = Tuple[int, int]                  # (staff id, 0-based bar)

_STAFF = re.compile(r'<Staff id="(\d+)">.*?</Staff>', re.S)
_MEASURE = re.compile(r"<Measure(?:\s[^>]*)?>.*?</Measure>", re.S)
_EID = re.compile(r"[ \t]*<eid>[^<]*</eid>\n?")

_VALUES = {"long": Fraction(4), "breve": Fraction(2), "whole": Fraction(1), "half": Fraction(1, 2),
           "quarter": Fraction(1, 4), "eighth": Fraction(1, 8), "16th": Fraction(1, 16),
           "32nd": Fraction(1, 32), "64th": Fraction(1, 64), "128th": Fraction(1, 128)}


class ScoreError(Exception):
    pass


def measure_spans(mscx: str) -> Dict[int, List[Tuple[int, int]]]:
    """``staff id -> [(start, end) of each bar]`` in ``mscx``."""
    out: Dict[int, List[Tuple[int, int]]] = {}
    for staff in _STAFF.finditer(mscx):
        spans = [(staff.start() + m.start(), staff.start() + m.end()) for m in _MEASURE.finditer(staff.group())]
        if spans:
            out[int(staff.group(1))] = spans
    if not out:
        raise ScoreError("no bars found in the score")
    if len({len(v) for v in out.values()}) != 1:
        raise ScoreError("the staves have different numbers of bars")
    return out


def get_measure(mscx: str, spans, key: Key) -> str:
    a, b = spans[key[0]][key[1]]
    return mscx[a:b]


def replace_measures(mscx: str, new: Dict[Key, str]) -> str:
    spans = measure_spans(mscx)
    for (a, b), text in sorted(((spans[s][k], t) for (s, k), t in new.items()), reverse=True):
        mscx = mscx[:a] + text + mscx[b:]
    return mscx


def canonical(measure: str) -> str:
    """A bar without ids and indentation: equal for two bars that read the same.

    MuseScore rewrites ids and spacing when it saves, so these say nothing about the music.
    """
    return "".join(line.strip() for line in _EID.sub("", measure).splitlines())


def digest(mscx: str, spans, keys) -> str:
    h = hashlib.sha1()
    for key in keys:
        h.update(canonical(get_measure(mscx, spans, key)).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def _length(chord: ET.Element, bar: Fraction) -> Fraction:
    kind = chord.findtext("durationType")
    if kind == "measure":
        text = chord.findtext("duration")
        return Fraction(text) if text else bar
    if kind not in _VALUES:
        raise ScoreError(f"unknown note value {kind!r}")
    dots = int(chord.findtext("dots") or 0)
    return _VALUES[kind] * (2 - Fraction(1, 2 ** dots))


def voice_lengths(measure: str, bar: Fraction) -> List[Fraction]:
    """How long each voice of a bar lasts, in whole notes; ``bar`` is the time signature's length."""
    root = ET.fromstring(measure)
    out = []
    for voice in root.findall("voice"):
        total, ratios = Fraction(0), []
        for el in voice:
            if el.tag == "Tuplet":
                ratios.append(Fraction(int(el.findtext("normalNotes")), int(el.findtext("actualNotes"))))
            elif el.tag == "endTuplet":
                if not ratios:
                    raise ScoreError("a tuplet ends that never started")
                ratios.pop()
            elif el.tag == "location":
                total += Fraction(el.findtext("fractions") or 0)
            elif el.tag in ("Chord", "Rest"):
                if any(el.find(g) is not None for g in
                       ("acciaccatura", "appoggiatura", "grace4", "grace16", "grace32", "grace8after",
                        "grace16after", "grace32after")):
                    continue
                value = _length(el, bar)
                for r in ratios:
                    value *= r
                total += value
            elif el.tag == "RepeatMeasure":
                total += bar
        if ratios:
            raise ScoreError("a tuplet is not closed")
        out.append(total)
    return out


def time_at(mscx: str, spans, staff: int, bar: int) -> Fraction:
    """Length of a bar under the time signature in force at ``bar``."""
    current = Fraction(1)
    for k in range(bar + 1):
        text = get_measure(mscx, spans, (staff, k))
        m = re.search(r"<TimeSig>.*?<sigN>(\d+)</sigN>.*?<sigD>(\d+)</sigD>", text, re.S)
        if m:
            current = Fraction(int(m.group(1)), int(m.group(2)))
    return current


def context_at(mscx: str, spans, staff: int, bar: int) -> str:
    """Clef, key and time signature in force at the start of ``bar``, in words."""
    clef, key, time = "G (treble)", 0, "4/4"
    for k in range(bar + 1):
        text = get_measure(mscx, spans, (staff, k))
        if (m := re.search(r"<concertClefType>([^<]+)</concertClefType>", text)):
            clef = m.group(1)
        if (m := re.search(r"<KeySig>.*?<(?:concertKey|accidental)>(-?\d+)<", text, re.S)):
            key = int(m.group(1))
        if (m := re.search(r"<TimeSig>.*?<sigN>(\d+)</sigN>.*?<sigD>(\d+)</sigD>", text, re.S)):
            time = f"{m.group(1)}/{m.group(2)}"
    return f"clef {clef}, key signature {key} fifths (concert), time {time}"


def check_replacement(old: str, new: str, bar: Fraction) -> Optional[str]:
    """Why ``new`` must not replace ``old`` -- or ``None`` if it may."""
    try:
        root = ET.fromstring(new)
    except ET.ParseError as exc:
        return f"not well-formed XML ({exc})"
    if root.tag != "Measure" or not root.findall("voice"):
        return "not a <Measure> with a <voice>"
    before = ET.fromstring(old)
    for tag in ("LayoutBreak", "stretch"):
        if [ET.tostring(e).split() for e in before.findall(tag)] != [ET.tostring(e).split() for e in root.findall(tag)]:
            return f"the bar's <{tag}> was changed"
    if before.get("len") != root.get("len"):
        return "the bar's actual length attribute was changed"
    try:
        lengths = voice_lengths(new, bar)
        allowed = {bar, *voice_lengths(old, bar)[:1]}
    except (ScoreError, ValueError, TypeError) as exc:
        return str(exc)
    if before.get("len"):
        allowed.add(Fraction(before.get("len")))
    wrong = [v for v in lengths[:1] if v not in allowed]
    wrong += [v for v in lengths[1:] if v > max(allowed)]
    if wrong:
        return f"the notes add up to {wrong[0]} of a whole note; the bar is {bar}"
    return None
