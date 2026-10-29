"""Optional post-pass: make the dynamics and hairpins of a combined score consistent.

OMR reads each part on its own, so the combined score inherits its slips: the same dynamic
twice in a bar (a copy above and below the staff), a hairpin that stops on the note it starts
on, a ``f`` that most of the section has but one part lost. This module repairs those, using
the other parts as evidence. Every rule is separate and every change is reported.

* ``dedupe``   -- the same dynamic twice within a beat of itself in one bar: keep the first.
* ``hairpins`` -- a hairpin of ``short_beats`` or less is a truncated one. Extend its stop to
  (1) where the same hairpin ends in the other parts, if some are longer; else (2) the next
  dynamic of this part when that lies within ``ahead_bars`` of the bar's end; else (3) the end
  of its bar. Never past the part's next hairpin.
* ``fill``     -- a bar where at least ``quorum`` of a section's playing parts (and at least
  ``min_voters``) carry a dynamic gets it in the parts of that section that play but carry none.
  Off by default: a missing mark is sometimes missing in the print too.

Inserted dynamics and moved hairpin stops are coloured ``mark`` so they can be checked and are
easy to find; ``None`` leaves them plain. ``normalize_marks`` works on any list of
``(part name, measures)`` -- ``combine`` calls it on its built parts, ``musicocr marks`` on a file.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction as F
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

Loc = Tuple[int, F]             # (1-based bar, position in quarter notes from the bar start)


@dataclass
class MarksOptions:
    dedupe: bool = True
    hairpins: bool = True
    fill: bool = False
    short_beats: float = 2.0    # a hairpin this short (quarter notes) counts as truncated
    ahead_bars: int = 1         # how far past its bar a hairpin may run to reach the next dynamic
    quorum: float = 0.5         # share of a section's playing parts that must carry a dynamic
    min_voters: int = 4         # ... and how many parts must be playing for the vote to count
    mark: Optional[str] = "#1976D2"

    @classmethod
    def parse(cls, spec: str) -> "MarksOptions":
        """``all`` or a comma list of rule names (``dedupe,hairpins,fill``); ``all`` = every rule."""
        names = {s.strip() for s in spec.split(",") if s.strip()}
        if "all" in names:
            names = {"dedupe", "hairpins", "fill"}
        bad = names - {"dedupe", "hairpins", "fill"}
        if bad:
            raise ValueError(f"unknown mark rule(s) {sorted(bad)}; known: dedupe, hairpins, fill, all")
        return cls(dedupe="dedupe" in names, hairpins="hairpins" in names, fill="fill" in names)


@dataclass
class Mark:
    bar: int
    pos: F
    kind: str                   # "dyn" | "wedge"
    value: str                  # dynamic name, or crescendo / diminuendo / stop
    m: ET.Element               # its measure
    d: ET.Element               # its <direction>
    dt: ET.Element              # its <direction-type>


@dataclass
class PartScan:
    name: str
    measures: List[ET.Element]
    div: List[int]              # <divisions> in force in each bar
    marks: List[Mark]
    lens: List[F]               # bar lengths (quarter notes)
    plays: List[bool]           # the bar has a sounding (non-rest) note

    def start(self, bar: int) -> F:
        return sum(self.lens[:bar - 1], F(0))

    def abs(self, loc: Loc) -> F:
        return self.start(loc[0]) + loc[1]

    def loc(self, a: F) -> Loc:
        """A position as (bar, offset); the end of a bar is that bar's, not the next one's start."""
        s = F(0)
        for i, n in enumerate(self.lens, 1):
            if a <= s + n:
                return i, a - s
            s += n
        return len(self.lens), self.lens[-1]


def _q(e: Optional[ET.Element], div: int) -> F:
    return F(int(float(e.text)), div) if e is not None and e.text else F(0)


def _scan(name: str, measures: List[ET.Element]) -> PartScan:
    div, divs, marks, lens, plays = 1, [], [], [], []
    for b, m in enumerate(measures, 1):
        pos = end = F(0)
        sounds = False
        t = m.findtext("attributes/divisions")
        if t:
            div = int(float(t))
        divs.append(div)
        for e in m:
            if e.tag == "note":
                if e.find("chord") is None:
                    pos += _q(e.find("duration"), div)
                if e.find("rest") is None:
                    sounds = True
            elif e.tag == "backup":
                pos -= _q(e.find("duration"), div)
            elif e.tag == "forward":
                pos += _q(e.find("duration"), div)
            elif e.tag == "direction":
                at = pos + _q(e.find("offset"), div)
                for dt in e.findall("direction-type"):
                    dyn, w = dt.find("dynamics"), dt.find("wedge")
                    if dyn is not None and len(dyn) == 1:
                        v = dyn[0].tag if dyn[0].tag != "other-dynamics" else "other:" + (dyn[0].text or "")
                        marks.append(Mark(b, at, "dyn", v, m, e, dt))
                    elif w is not None:
                        marks.append(Mark(b, at, "wedge", w.get("type", ""), m, e, dt))
            end = max(end, pos)
        lens.append(end)
        plays.append(sounds)
    return PartScan(name, measures, divs, marks, lens, plays)


def _remove(mk: Mark) -> None:
    mk.d.remove(mk.dt)
    if mk.d.find("direction-type") is None and mk.d in list(mk.m):
        mk.m.remove(mk.d)


def _insert(m: ET.Element, d: ET.Element, pos: F, div: int, first: bool = False) -> None:
    """Put direction ``d`` into bar ``m`` before the first note that starts at or after ``pos``;
    at the end of the bar (before its closing barline) if there is none. ``first`` puts it ahead of
    the directions already sitting on that note (a hairpin stop goes before the next start)."""
    p = F(0)
    for i, e in enumerate(list(m)):
        if e.tag == "note" and e.find("chord") is None:
            if p >= pos:
                while first and i and m[i - 1].tag == "direction":
                    i -= 1
                m.insert(i, d)
                return
            p += _q(e.find("duration"), div)
        elif e.tag == "backup":
            p -= _q(e.find("duration"), div)
        elif e.tag == "forward":
            p += _q(e.find("duration"), div)
    i = len(m)
    while i and m[i - 1].tag == "barline":
        i -= 1
    while first and i and m[i - 1].tag == "direction":
        i -= 1
    m.insert(i, d)


def _dyn_direction(src: Mark, colour: Optional[str]) -> ET.Element:
    """A fresh direction carrying only ``src``'s dynamic."""
    d = ET.Element("direction", {k: v for k, v in src.d.attrib.items() if k == "placement"})
    dt = copy.deepcopy(src.dt)
    ET.SubElement(d, "direction-type").extend(list(dt))
    if colour:
        for e in d.iter("dynamics"):
            e.set("color", colour)
    return d


# ---- rules ------------------------------------------------------------------------------

def dedupe(p: PartScan, report: List[str]) -> int:
    kept: List[Mark] = []
    n = 0
    for mk in p.marks:
        if mk.kind != "dyn":
            continue
        if any(k.bar == mk.bar and k.value == mk.value and abs(k.pos - mk.pos) <= 1 for k in kept):
            _remove(mk)
            n += 1
        else:
            kept.append(mk)
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            if a.bar == b.bar and a.pos == b.pos and a.value != b.value:
                report.append(f"  {p.name} bar {a.bar}: {a.value} and {b.value} at the same spot -- check")
    return n


def _pairs(p: PartScan) -> List[Tuple[Mark, Mark]]:
    out, opened = [], None
    for mk in p.marks:
        if mk.kind != "wedge":
            continue
        if mk.value in ("crescendo", "diminuendo"):
            opened = mk
        elif mk.value == "stop" and opened is not None:
            out.append((opened, mk))
            opened = None
    return out


def extend_hairpins(scans: Sequence[PartScan], opts: MarksOptions, report: List[str]) -> int:
    short = F(opts.short_beats).limit_denominator(64)
    # where each hairpin (kind, start bar) ends in the parts that already draw it long
    long_ends: Dict[Tuple[str, int], List[Loc]] = defaultdict(list)
    for p in scans:
        for a, z in _pairs(p):
            if p.abs((z.bar, z.pos)) - p.abs((a.bar, a.pos)) > short:
                long_ends[a.value, a.bar].append((z.bar, z.pos))
    # the dynamic that follows each hairpin's start, per part: the section usually agrees where it lands
    next_dyn: Dict[Tuple[str, int], List[Loc]] = defaultdict(list)
    for p in scans:
        for a, _ in _pairs(p):
            s = p.abs((a.bar, a.pos))
            after = [d for d in p.marks if d.kind == "dyn" and p.abs((d.bar, d.pos)) > s + 1]
            if after:
                next_dyn[a.value, a.bar].append(p.loc(p.abs((after[0].bar, after[0].pos))))
    moved = 0
    for p in scans:
        pairs = _pairs(p)
        starts = [p.abs((a.bar, a.pos)) for a, _ in pairs]
        for i, (a, z) in enumerate(pairs):
            s, e = p.abs((a.bar, a.pos)), p.abs((z.bar, z.pos))
            end_of_bar = p.start(a.bar) + p.lens[a.bar - 1]
            votes = Counter(next_dyn.get((a.value, a.bar), []))
            landing = p.abs(votes.most_common(1)[0][0]) if votes and votes.most_common(1)[0][1] >= 3 else None
            # a hairpin that fills its bar but stops short of where >= 3 parts agree it lands
            runs_on = (landing is not None and e >= end_of_bar - 1 and e < landing
                       and landing - end_of_bar <= opts.ahead_bars * p.lens[a.bar - 1])
            if e - s > short and not runs_on:
                continue
            target, why = None, ""
            peers = Counter(long_ends.get((a.value, a.bar), []))
            if peers and peers.most_common(1)[0][1] >= 2 and p.abs(peers.most_common(1)[0][0]) > e:
                target, why = p.abs(peers.most_common(1)[0][0]), "as in the other parts"
            if target is None:
                nxt = [p.abs((d.bar, d.pos)) for d in p.marks if d.kind == "dyn"
                       and p.abs((d.bar, d.pos)) > s + 1]
                if votes and votes.most_common(1)[0][1] >= 2:       # the section's agreed landing
                    nxt = [p.abs(votes.most_common(1)[0][0])]
                if nxt and end_of_bar < nxt[0] <= end_of_bar + opts.ahead_bars * p.lens[a.bar - 1]:
                    target, why = nxt[0], "to the next dynamic"
                else:
                    target, why = end_of_bar, "to the end of the bar"
            if i + 1 < len(pairs):
                target = min(target, starts[i + 1])
            if target <= e:
                continue
            bar, pos = p.loc(target)
            if z.d.findall("direction-type") == [z.dt]:
                d = z.d
                z.m.remove(d)
            else:
                d = ET.Element("direction", {k: v for k, v in z.d.attrib.items() if k == "placement"})
                ET.SubElement(d, "direction-type").extend(list(z.dt))
                _remove(z)
            if opts.mark:
                for w in d.iter("wedge"):
                    w.set("color", opts.mark)
            _insert(p.measures[bar - 1], d, pos, p.div[bar - 1], first=True)
            moved += 1
            report.append(f"  {p.name}: {a.value} from bar {a.bar} ended in bar {z.bar} -> bar {bar} "
                          f"({why})")
    return moved


def fill_dynamics(scans: Sequence[PartScan], opts: MarksOptions, report: List[str]) -> int:
    groups: Dict[str, List[PartScan]] = defaultdict(list)
    for p in scans:
        groups[re.sub(r"\s*\d+$", "", p.name)].append(p)
    todo: List[Tuple[PartScan, int, F, Mark]] = []
    for g, ps in groups.items():
        for b in range(1, max(len(p.lens) for p in ps) + 1):
            voters = [p for p in ps if b <= len(p.plays) and p.plays[b - 1]]
            if len(voters) < opts.min_voters:
                continue
            here = {p.name: [m for m in p.marks if m.bar == b and m.kind == "dyn"] for p in voters}
            for v in sorted({m.value for ms in here.values() for m in ms}):
                have = [m for p in voters for m in here[p.name] if m.value == v]
                if len({id(m.m) for m in have}) / len(voters) < opts.quorum:
                    continue
                pos = Counter(m.pos for m in have).most_common(1)[0][0]
                src = next(m for m in have if m.pos == pos)
                todo += [(p, b, pos, src) for p in voters if not here[p.name]]
    seen = set()
    n = 0
    for p, b, pos, src in todo:
        if (p.name, b) in seen:                 # one fill per bar; a second would pile up
            continue
        seen.add((p.name, b))
        _insert(p.measures[b - 1], _dyn_direction(src, opts.mark), pos, p.div[b - 1])
        n += 1
        report.append(f"  {p.name} bar {b}: added {src.value} (the rest of the section has it)")
    return n


def normalize_marks(parts: Sequence[Tuple[str, List[ET.Element]]], opts: Optional[MarksOptions] = None
                    ) -> Tuple[Dict[str, int], List[str]]:
    """Apply the enabled rules to ``(name, measures)`` parts in place; returns counts and report."""
    opts = opts or MarksOptions()
    report: List[str] = []
    stats = dict(deduped=0, filled=0, extended=0)
    scans = [_scan(n, ms) for n, ms in parts]
    if opts.dedupe:
        stats["deduped"] = sum(dedupe(p, report) for p in scans)
        scans = [_scan(n, ms) for n, ms in parts]
    if opts.fill:
        stats["filled"] = fill_dynamics(scans, opts, report)
        scans = [_scan(n, ms) for n, ms in parts]
    if opts.hairpins:
        stats["extended"] = extend_hairpins(scans, opts, report)
    report.insert(0, f"marks: {stats['deduped']} duplicate dynamic(s) dropped, {stats['filled']} "
                     f"dynamic(s) filled, {stats['extended']} hairpin(s) extended")
    return stats, report


def normalize_file(src: Path, dst: Path, opts: Optional[MarksOptions] = None) -> Tuple[Dict[str, int], List[str]]:
    """Run the pass over a partwise MusicXML file (e.g. a combined score) and write ``dst``."""
    tree = ET.parse(src)
    root = tree.getroot()
    names = {sp.get("id"): sp.findtext("part-name") or sp.get("id")
             for sp in root.iterfind("part-list/score-part")}
    parts = [(names.get(p.get("id"), p.get("id")), p.findall("measure")) for p in root.findall("part")]
    out = normalize_marks(parts, opts)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return out
