"""Repair time-signature numerators that homr gets wrong.

homr's model predicts the time signature's *denominator* from the glyph, but its
MusicXML generator derives the *numerator* from the median measure length of the
whole page (``find_division_and_time_signature_nominator``). On a page that is
mostly 4/4 with a change to 6/8, the 6/8 therefore comes out as "8/8" -- and the
normalize stage then pads every genuine 6/8 measure to 8/8, destroying real
rhythm. There is no homr option to hint the signature.

``fix_time_signatures`` keeps the denominator and recomputes the numerator of each
declared signature from the measures it governs (up to the next signature): the
most common measure length wins, provided it is a clear majority. Run it before
normalize *and* before the page merge (``fix_time_signatures_files``), treating the
pages as one continuous part: a page with no signature of its own would otherwise
be parsed as 4/4 by music21 and have its real 6/8 bars padded during the merge.
"""
from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Callable, List, Optional, Sequence
from xml.etree import ElementTree as ET

_MIN_SHARE = 0.5     # winning length must cover at least this share of the segment
_MIN_MEASURES = 3


def _measure_quarters(measure: ET.Element, divisions: int) -> float:
    """Length of the measure in quarter notes: the furthest point any voice reaches."""
    pos = longest = 0
    for el in measure:
        if el.tag == "note":
            if el.find("chord") is None:
                pos += int(el.findtext("duration") or 0)
        elif el.tag == "forward":
            pos += int(el.findtext("duration") or 0)
        elif el.tag == "backup":
            pos -= int(el.findtext("duration") or 0)
        longest = max(longest, pos)
    return longest / divisions


def _fix_sequence(parts: List[ET.Element], label: str, log: Callable[[str], None]) -> int:
    """Fix numerators over ``parts`` read as one continuous part; returns changes made.

    Also gives the first measure of every later ``<part>`` the signature in force
    when it starts, if it doesn't declare one.
    """
    divisions = 1
    segments: List[dict] = []    # {"time": element, "lengths": [quarters]}
    flat = [(pi, m) for pi, part in enumerate(parts) for m in part.findall("measure")]
    for i, (pi, measure) in enumerate(flat):
        for attrs in measure.findall("attributes"):    # homr may write several per measure
            if attrs.findtext("divisions"):
                divisions = int(attrs.findtext("divisions"))
            time = attrs.find("time")
            if time is not None:
                segments.append({"time": time, "lengths": []})
        if not segments or i == 0 or i == len(flat) - 1:
            continue     # pickup / final bar may legitimately be short
        length = _measure_quarters(measure, divisions)
        if length > 0:
            segments[-1]["lengths"].append(length)

    changed = 0
    for seg in segments:
        time, lengths = seg["time"], seg["lengths"]
        try:
            beats, beat_type = int(time.findtext("beats")), int(time.findtext("beat-type"))
        except (TypeError, ValueError):
            continue     # senza misura, "3+2" style, ...
        if len(lengths) < _MIN_MEASURES:
            continue
        numerators = Counter(round(q * beat_type / 4, 2) for q in lengths)
        best, count = numerators.most_common(1)[0]
        if count / len(lengths) < _MIN_SHARE or best != int(best) or best < 1:
            continue
        if int(best) != beats:
            time.find("beats").text = str(int(best))
            log(f"  {label}: time signature {beats}/{beat_type} -> {int(best)}/{beat_type} "
                f"({count}/{len(lengths)} following measures are {int(best)}/{beat_type} long)")
            changed += 1

    current: Optional[ET.Element] = None
    for pi, part in enumerate(parts):
        first = part.find("measure")
        if first is None:
            continue
        attrs = first.find("attributes")
        declared = first.find("attributes/time")
        if declared is None and current is not None and pi > 0:
            if attrs is None:
                attrs = ET.Element("attributes")
                first.insert(0, attrs)
            carried = copy.deepcopy(current)
            after = [i + 1 for i, c in enumerate(attrs) if c.tag in ("divisions", "key")]
            attrs.insert(max(after, default=0), carried)
            declared = carried
        for m in part.findall("measure"):
            for t in m.findall("attributes/time"):
                declared = t
        if declared is not None:
            current = declared
    return changed


def _part_label(root: ET.Element, part: ET.Element) -> str:
    names = {sp.get("id"): sp.findtext("part-name") or sp.get("id")
             for sp in root.iter("score-part")}
    return names.get(part.get("id"), part.get("id", ""))


def fix_time_signatures(root: ET.Element, log: Callable[[str], None] = print) -> int:
    """Fix every part of one parsed ``score-partwise``; returns signatures changed."""
    return sum(_fix_sequence([p], _part_label(root, p), log) for p in root.findall("part"))


def fix_time_signatures_file(path: Path, log: Callable[[str], None] = print) -> int:
    """Fix ``path`` in place (only rewritten if something changed)."""
    return fix_time_signatures_files([path], log)


def fix_time_signatures_files(paths: Sequence[Path],
                              log: Callable[[str], None] = print) -> int:
    """Fix consecutive page files as one continuous score, rewriting those that change.

    Parts are matched by position across files. Files that aren't
    ``score-partwise`` or can't be parsed are left alone.
    """
    trees = []
    for path in paths:
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        if tree.getroot().tag == "score-partwise":
            trees.append((path, tree, ET.tostring(tree.getroot())))
    if not trees:
        return 0
    changed = 0
    for idx in range(max(len(t.getroot().findall("part")) for _, t, _ in trees)):
        seq = [t.getroot().findall("part")[idx] for _, t, _ in trees
               if len(t.getroot().findall("part")) > idx]
        changed += _fix_sequence(seq, _part_label(trees[0][1].getroot(), seq[0]), log)
    for path, tree, before in trees:
        if ET.tostring(tree.getroot()) != before:
            ET.indent(tree, space="  ")
            tree.write(path, encoding="UTF-8", xml_declaration=True)
    return changed
