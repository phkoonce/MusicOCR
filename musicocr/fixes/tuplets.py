"""Generic tuplet clean-up for OMR MusicXML.

OMR engines that infer tuplets from note spacing (Copisteria) can read one tuplet as a mix: the
notes get different ratios (3:2, 6:4, 5:4), the ``<tuplet>`` start lands on the third note while the
first two carry a ratio but belong to no group, a rest ends up 0.4 of a beat long. The bar then
does not add up, and MuseScore, which rebuilds tuplets from those tags, shows a misaligned bracket
and fills the leftover time with ever shorter rests -- deleting the bar spreads it to the next one.

``fix_measure`` looks at each run of consecutive time-modified notes of a voice and, when the run
is inconsistent, rebuilds it: the ratio most of its notes carry (value first, then notation) is
applied to all of them, each duration is recomputed from the printed note value, and the
``<tuplet>`` tags are rewritten as complete groups of ``actual-notes`` notes. A run that cannot be
split into whole groups is left alone and reported.

Only bars that do not add up are touched: a bar whose length differs from its time signature and
that holds an inconsistent run (ratios differing in value, a duration that does not match the
printed value under the note's own ratio, tuplet tags leaving a note outside a group or a group
open). The rebuilt bar is kept only if it then has exactly the length of the bar; otherwise it is
left as read. Plenty of OMR tuplets look odd (adjacent groups of mixed note values, a trailing
note without a bracket) yet add up, and are left alone. A rebuilt run is coloured and gets a
"check" text above it (``flag=True``): the right fix is a guess from the printed note values.

A bar with a tuplet that has no notation at all (groups filling 4/3 of a beat, say) is different: it
may add up to the time signature, yet MuseScore cannot lay it out. ``clear_unwritable`` replaces such
a bar by a rest (flagged), to be re-entered by hand.
"""
from __future__ import annotations

import copy
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from musicocr.musicxml import check_direction, measures_with_capacity, note_duration, sounding_length, write_tree

FLAG_COLOR = "#7B1FA2"
_BASE = {"breve": F(8), "whole": F(4), "half": F(2), "quarter": F(1), "eighth": F(1, 2),
         "16th": F(1, 4), "32nd": F(1, 8), "64th": F(1, 16), "128th": F(1, 32)}


def _ratio(n: ET.Element) -> Optional[Tuple[int, int]]:
    tm = n.find("time-modification")
    if tm is None:
        return None
    try:
        return int(tm.findtext("actual-notes")), int(tm.findtext("normal-notes"))
    except (TypeError, ValueError):
        return None


def _printed_beats(n: ET.Element) -> Optional[F]:
    """Quarter notes of the printed value (type and dots), ignoring any tuplet ratio."""
    base = _BASE.get(n.findtext("type") or "")
    if base is None:
        return None
    for k in range(len(n.findall("dot"))):
        base += _BASE[n.findtext("type")] / 2 ** (k + 1)
    return base


def _tuplet_tags(n: ET.Element) -> List[ET.Element]:
    return [t for nt in n.findall("notations") for t in nt.findall("tuplet")]


def _voice_notes(measure: ET.Element) -> List[List[ET.Element]]:
    """The bar's lead notes (no grace notes, no chord tones) grouped by voice, in document order."""
    by_voice: Dict[str, List[ET.Element]] = {}
    for n in measure.findall("note"):
        if n.find("grace") is None and n.find("chord") is None:
            by_voice.setdefault(n.findtext("voice") or "1", []).append(n)
    return list(by_voice.values())


def _voice_runs(measure: ET.Element) -> List[List[ET.Element]]:
    """Runs of consecutive time-modified notes (chord tones follow their lead note), per voice."""
    runs: List[List[ET.Element]] = []
    for notes in _voice_notes(measure):
        cur: List[ET.Element] = []
        for n in notes:
            if _ratio(n) is not None:
                cur.append(n)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
    return runs


def _tags_ok(run: List[ET.Element]) -> bool:
    """Every note inside a start..stop group, and every group closed."""
    open_ = 0
    for n in run:
        tags = _tuplet_tags(n)
        open_ += sum(t.get("type") == "start" for t in tags)
        if open_ <= 0:
            return False
        open_ -= sum(t.get("type") == "stop" for t in tags)
        if open_ < 0:
            return False
    return open_ == 0


def _inconsistent(run: List[ET.Element], div: int) -> Optional[str]:
    """What is wrong with ``run`` ("" = nothing), or None when its notes cannot be judged."""
    ratios = [_ratio(n) for n in run]
    printed = [_printed_beats(n) for n in run]
    if any(p is None for p in printed):
        return None
    if len({F(a, b) for a, b in ratios}) > 1:
        return "mixed tuplet ratios " + "/".join(f"{a}:{b}" for a, b in sorted(set(ratios)))
    if any(note_duration(n) != p * div * r[1] / r[0] for n, p, r in zip(run, printed, ratios)):
        return "note durations off their printed values"
    if any(_tuplet_tags(n) for n in run) and not _tags_ok(run):
        return "tuplet brackets misaligned"
    return ""


def rebuild_run(run: List[ET.Element], div: int, measure: ET.Element) -> bool:
    """Rebuild ``run`` in place with its dominant ratio; False (run untouched) if it cannot form whole groups."""
    ratios = [_ratio(n) for n in run]
    pairs = Counter(ratios)
    target = max(pairs, key=lambda r: (sum(c for q, c in pairs.items() if F(*q) == F(*r)), pairs[r], -r[0]))
    a, b = target
    if len(run) % a:
        return False
    new = [_printed_beats(n) * div * b / a for n in run]
    if any(x.denominator != 1 for x in new):
        return False
    kids = list(measure)
    for n, d in zip(run, new):
        group = [n]
        for k in kids[kids.index(n) + 1:]:           # chord tones follow their lead note and share its timing
            if k.tag != "note" or k.find("chord") is None:
                break
            group.append(k)
        for g in group:
            g.find("duration").text = str(int(d))
            tm = g.find("time-modification")
            tm.find("actual-notes").text, tm.find("normal-notes").text = str(a), str(b)
    proto = next((t for n in run for t in _tuplet_tags(n) if t.get("type") == "start"), None)
    for n in run:
        for nt in n.findall("notations"):
            for t in nt.findall("tuplet"):
                nt.remove(t)
    for g in range(0, len(run), a):
        for n, kind in ((run[g], "start"), (run[g + a - 1], "stop")):
            nt = n.find("notations")
            if nt is None:
                nt = ET.SubElement(n, "notations")
            t = copy.deepcopy(proto) if (proto is not None and kind == "start") else ET.Element("tuplet")
            t.attrib.pop("number", None)
            t.set("type", kind)
            nt.append(t)
    return True


def _flag(measure: ET.Element, run: List[ET.Element], text: str) -> None:
    for n in run:
        n.set("color", FLAG_COLOR)
    measure.insert(list(measure).index(run[0]), check_direction(text, FLAG_COLOR))


def _beam1(n: ET.Element) -> str:
    """The note's primary-beam state ("begin", "continue", "end", "" for none)."""
    beams = n.findall("beam")
    b = next((b for b in beams if b.get("number", "1") == "1"), None)
    return (b.text or "").strip() if b is not None else ""


def _orphans(measure: ET.Element) -> List[Tuple[ET.Element, ET.Element, bool]]:
    """Notes without a ratio that are beamed to a time-modified neighbour: (note, neighbour, after_it)."""
    found = []
    for notes in _voice_notes(measure):
        for i, n in enumerate(notes):
            if _ratio(n) is not None or _printed_beats(n) is None:
                continue
            prev = notes[i - 1] if i else None
            nxt = notes[i + 1] if i + 1 < len(notes) else None
            if prev is not None and _ratio(prev) and _beam1(prev) in ("begin", "continue") and _beam1(n) in ("continue", "end"):
                found.append((n, prev, True))
            elif nxt is not None and _ratio(nxt) and _beam1(nxt) in ("continue", "end") and _beam1(n) in ("begin", "continue"):
                found.append((n, nxt, False))
    return found


def _adopt(measure: ET.Element, note: ET.Element, nb: ET.Element, after: bool, div: int) -> bool:
    """Give ``note`` its beamed neighbour's ratio and let the neighbour's bracket reach it."""
    a, b = _ratio(nb)
    d = _printed_beats(note) * div * b / a
    if d.denominator != 1:
        return False
    note.find("duration").text = str(int(d))
    tm = copy.deepcopy(nb.find("time-modification"))
    for tag in ("normal-type", "normal-dot"):
        for e in tm.findall(tag):
            tm.remove(e)
    pos = max((list(note).index(e) for e in note if e.tag in ("type", "dot", "accidental")), default=len(note) - 1)
    note.insert(pos + 1, tm)
    kind = "stop" if after else "start"
    for nt in nb.findall("notations"):
        for t in nt.findall("tuplet"):
            if t.get("type") == kind:
                nt.remove(t)
                dest = note.find("notations")
                if dest is None:
                    dest = ET.SubElement(note, "notations")
                dest.append(t)
    return True


def fix_orphans(measure: ET.Element, div: int, capacity: int, flag: bool = True) -> List[str]:
    """Adopt a note that is beamed to a tuplet but lacks its ratio, when that makes the bar add up.

    The usual Copisteria slip: the last (or first) note of a beamed tuplet group is read as an
    ordinary note, so the bar runs a fraction of a beat long. The note takes the neighbour's ratio
    (value unchanged, duration recomputed) and the neighbour's bracket end moves onto it. All
    candidates are tried together, then one at a time; the bar is left as read unless it then adds up.
    """
    cands = _orphans(measure)
    if not cands:
        return []
    orig = copy.deepcopy(measure)
    before = sounding_length(measure)
    for subset in [cands] + ([[c] for c in cands] if len(cands) > 1 else []):
        if all(_adopt(measure, n, nb, after, div) for n, nb, after in subset) and sounding_length(measure) == capacity:
            if flag:
                for n, nb, _ in subset:
                    _flag(measure, [n], "check: tuplet note fixed")
            return [f"rebuilt: {len(subset)} note(s) beamed to a tuplet had no ratio "
                    f"(bar was {F(before, div)} beats, now {F(capacity, div)})"]
        measure[:] = list(copy.deepcopy(orig))
    return []


def fix_measure(measure: ET.Element, div: int, capacity: Optional[int], flag: bool = True) -> List[str]:
    """Rebuild the inconsistent tuplet runs of a bar that does not add up to ``capacity`` divisions.

    Returns one message per outcome ("rebuilt: ..." or "not fixed: ..."); the bar is left exactly as
    read unless the rebuild gives it the capacity's length.
    """
    if capacity is None or measure.find("backup") is not None or sounding_length(measure) == capacity:
        return []
    msgs = fix_orphans(measure, div, capacity, flag)
    if msgs:
        return msgs
    faults = []
    for run in _voice_runs(measure):
        what = _inconsistent(run, div)
        if what:
            faults.append((run, what))
    if not faults:
        return []
    before, orig = sounding_length(measure), copy.deepcopy(measure)
    for run, _ in faults:
        if not rebuild_run(run, div, measure):
            break
    else:
        if sounding_length(measure) == capacity:
            if flag:
                for run, what in faults:
                    _flag(measure, run, "check: tuplet rebuilt")
            return ["rebuilt: " + "; ".join(w for _, w in faults)
                    + f" (bar was {F(before, div)} beats, now {F(capacity, div)})"]
    measure[:] = list(orig)
    return [f"not fixed: {'; '.join(w for _, w in faults)}; a rebuild does not give the bar its "
            f"{F(capacity, div)} beats (it has {F(before, div)})"]


def _note_value(beats: F) -> bool:
    """A span MuseScore can write as one (dotted) note: 1/8 .. 8 quarters, plain or dotted."""
    return any(beats == F(2) ** k * m for k in range(-3, 4) for m in (1, F(3, 2)))


def _unwritable(run: List[ET.Element], div: int) -> Optional[str]:
    """Why ``run`` (one ratio throughout) cannot be notated as complete tuplets, else None.

    A tuplet of ``actual-notes`` notes fills one note value (a triplet of eighths a quarter). A run
    whose consecutive groups fill something else (a quarter, an eighth rest and an eighth in a
    3:2 run fill 4/3 of a beat) has no notation: MuseScore lays it out with overlapping groups at
    negative offsets and pads the bar with ever shorter rests.
    """
    ratios = {_ratio(n) for n in run}
    if len(ratios) != 1 or None in ratios:
        return None
    actual, _ = next(iter(ratios))
    if actual <= 0:
        return None
    if len(run) % actual:
        return f"{len(run)} notes in {actual}-note tuplets"
    for i in range(0, len(run), actual):
        span = F(sum(note_duration(n) for n in run[i:i + actual]), div)
        if not _note_value(span):
            return f"a {actual}-note tuplet filling {span} beats"
    return None


def clear_unwritable(measure: ET.Element, div: int, capacity: Optional[int], flag: bool = True) -> List[str]:
    """Replace a bar whose tuplets cannot be notated by one bar-long rest; returns the message, if any.

    Nothing in such a bar is a faithful reading, so keeping it only gives a bar that cannot be
    edited (deleting its contents corrupts the next bar). The rest is flagged for re-entry by hand.
    """
    if measure.find("backup") is not None or measure.find("forward") is not None:
        return []
    why = next((w for run in _voice_runs(measure) if (w := _unwritable(run, div))), None)
    if why is None:
        return []
    notes = measure.findall("note")
    total = capacity if capacity is not None else sounding_length(measure)       # a regular bar, whatever the notes added up to
    first = list(measure).index(notes[0])
    for n in notes:
        measure.remove(n)
    rest = ET.Element("note", color=FLAG_COLOR) if flag else ET.Element("note")
    whole_bar = capacity is not None
    ET.SubElement(rest, "rest", measure="yes") if whole_bar else ET.SubElement(rest, "rest")
    ET.SubElement(rest, "duration").text = str(total)
    ET.SubElement(rest, "voice").text = "1"
    if not whole_bar:
        ET.SubElement(rest, "type").text = "quarter"
    measure.insert(first, rest)
    if flag:
        measure.insert(first, check_direction("check: bar cleared (unreadable tuplet)", FLAG_COLOR))
    return [f"cleared: {why}; bar replaced by a rest"]


def fix_tree(root: ET.Element, flag: bool = True, log: Callable[[str], None] = print) -> int:
    """Clean up every part of a ``score-partwise``; returns the number of bars rebuilt or cleared."""
    fixed = 0
    for part in root.findall("part"):
        for m, div, cap in measures_with_capacity(part):
            msgs = fix_measure(m, div, cap, flag)
            if not any(msg.startswith("rebuilt") for msg in msgs):
                msgs += clear_unwritable(m, div, cap, flag)
            for msg in msgs:
                log(f"  measure {m.get('number')}: {msg}")
                fixed += msg.startswith(("rebuilt", "cleared"))
    return fixed


def fix_tuplets_file(path: Path, flag: bool = True, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place; returns the number of tuplet runs rebuilt."""
    tree = ET.parse(path)
    n = fix_tree(tree.getroot(), flag, log)
    if n:
        write_tree(tree, path)
    return n
