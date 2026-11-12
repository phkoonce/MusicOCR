"""Aligning ensemble parts by their rehearsal numbers.

An ensemble book prints no bar numbers, but most editions number the rehearsal marks (the boxed or ringed 1, 2, 3 ...
that restart in each movement). The OMR places a ``<rehearsal>#N</rehearsal>`` placeholder over the bar each mark
sits at (``musicocr.text.circles``, ``#`` when the digits could not be read). Every part prints the same marks over the
same bars, so they cut the piece into stretches of known extent: a part that lost or invented a bar can only be
wrong *inside* its stretch, and is aligned there by rhythm (``musicocr.assemble.unnumbered``) with the length the
other parts agree on.

``lay_out_by_marks`` returns ``None`` when the parts do not carry enough marks to be useful; ``combine`` then falls
back to aligning each part as one run.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from statistics import median_low
from typing import Any, Dict, List, Optional, Sequence, Tuple

from musicocr.assemble.labels import book_style, label_values
from musicocr.assemble.text_system import text_keys
from musicocr.assemble.unnumbered import align_by_rhythm, onsets

Src = Tuple[ET.Element, int]
Label = Tuple[int, int]                 # (movement, printed number)

MIN_COMMON = 3                          # fewer agreed marks than this: not worth it


def mark_numbers(measure: ET.Element, style: Optional[str] = "number") -> List[Optional[int]]:
    """The rehearsal marks of a bar as numbers (letters count A=1, B=2 ...): None where the label was not read."""
    return label_values(measure, style)


def part_marks(bars: Sequence[Optional[Src]], style: Optional[str] = "number") -> Dict[Label, int]:
    """``{(movement, number): bar index}`` of the labelled marks of a part. The labels run 1, 2, 3 (or A, B, C) in a
    movement and start again at the next; a 1 or 2 after a run reaching 4 or more starts one, anything else that is
    not above the last label is a misread and is left out."""
    out: Dict[Label, int] = {}
    movement, last = 1, 0
    for i, src in enumerate(bars):
        if src is None:
            continue
        for n in mark_numbers(src[0], style):
            if n is None:
                continue
            if n <= 2 and last >= 4 and n < last:
                movement, last = movement + 1, 0
            if n <= last:
                continue
            out[(movement, n)] = i
            last = n
    return out


def _consensus_labels(marks: Dict[str, Dict[Label, int]], need: int) -> List[Label]:
    votes = Counter(l for m in marks.values() for l in m)
    return sorted(l for l, c in votes.items() if c >= need)


def _monotonic(marks: Dict[Label, int], labels: Sequence[Label]) -> Dict[Label, int]:
    """The part's marks among ``labels`` whose bar indices increase (a misplaced one is dropped)."""
    out, prev = {}, -1
    for l in labels:
        b = marks.get(l)
        if b is not None and b > prev:
            out[l] = b
            prev = b
    return out


def _plurality(values: Sequence[int]) -> int:
    top = Counter(values).most_common()
    best = [v for v, c in top if c == top[0][1]]
    return median_low(sorted(best))


def lay_out_by_marks(data: Dict[str, Dict[str, Any]], report: List[str]):
    """``(total, {pid: [Src | None] * total}, {pid: {bar: "check: ..."}})`` or None.

    ``data[pid]["pages"]`` is one page holding the part's whole run of bars (a scan without bar numbers)."""
    runs = {pid: [s for pg in d["pages"] for s in pg] for pid, d in data.items()}
    style = book_style(m for r in runs.values() for m, _div in r)
    marks = {pid: part_marks(r, style) for pid, r in runs.items()}
    need = max(2, (len(runs) + 1) // 2)
    labels = _consensus_labels(marks, need)
    if len(labels) < MIN_COMMON:
        return None
    marks = {pid: _monotonic(m, labels) for pid, m in marks.items()}
    # every stretch between two agreed marks must be measured by at least one part; a mark that leaves one
    # without a measure is dropped
    while True:
        labels = [l for l in labels if sum(l in m for m in marks.values()) >= 2]
        lens: List[List[int]] = []
        bad = None
        for k in range(len(labels) + 1):
            lo = labels[k - 1] if k else None
            hi = labels[k] if k < len(labels) else None
            got = []
            for pid, m in marks.items():
                a = 0 if lo is None else m.get(lo)
                b = len(runs[pid]) if hi is None else m.get(hi)
                if a is not None and b is not None:
                    got.append(b - a)
            lens.append(got)
            if not got and bad is None:
                bad = k
        if bad is None:
            break
        labels.pop(bad if bad < len(labels) else len(labels) - 1)      # stretch `bad` ends at label `bad`
        if len(labels) < MIN_COMMON:
            return None
    marks = {pid: {l: b for l, b in m.items() if l in labels} for pid, m in marks.items()}
    target = [_plurality(g) for g in lens]
    offsets = [0]
    for t in target:
        offsets.append(offsets[-1] + t)
    total = offsets[-1]
    report.append(f"score: {len(labels)} rehearsal marks ({style}s) agreed by {need}+ parts cut the piece into "
                  f"{len(target)} stretches ({total} bars)")

    # per part: spans between its own marks; a span covers the stretches from after one mark to the next one it has
    spans: Dict[str, List[Tuple[int, int, int, int]]] = {}       # (src a, src b, first stretch, last stretch)
    for pid, m in marks.items():
        cuts = [(-1, 0)] + [(labels.index(l), b) for l, b in sorted(m.items(), key=lambda kv: kv[1])]
        cuts.append((len(labels), len(runs[pid])))
        spans[pid] = [(a, b, i0 + 1, i1) for (i0, a), (i1, b) in zip(cuts, cuts[1:])]

    bars: Dict[str, List[Optional[Src]]] = {pid: [None] * total for pid in runs}
    need_align: Dict[str, List[Tuple[int, int, int, int]]] = {pid: [] for pid in runs}
    for pid, sp in spans.items():
        for a, b, s0, e0 in sp:
            g0, g1 = offsets[s0], offsets[e0 + 1]
            if b - a == g1 - g0:
                bars[pid][g0:g1] = runs[pid][a:b]
            else:
                need_align[pid].append((a, b, g0, g1))
    flags: Dict[str, Dict[int, str]] = {pid: {} for pid in runs}
    on = {pid: [onsets(*s) if s is not None else None for s in seq] for pid, seq in bars.items()}
    tx = {pid: [text_keys(s[0]) if s is not None else None for s in seq] for pid, seq in bars.items()}
    for round_ in range(2):
        for pid, todo in need_align.items():
            for a, b, g0, g1 in todo:
                pg = runs[pid][a:b]
                ref = [[o[g] for q, o in on.items() if q != pid and o[g] is not None] for g in range(g0, g1)]
                tref = [[t[g] for q, t in tx.items() if q != pid and t[g] is not None] for g in range(g0, g1)]
                slots = align_by_rhythm([onsets(*s) for s in pg], ref, [text_keys(s[0]) for s in pg], tref)
                for j, s in enumerate(slots):
                    bars[pid][g0 + j] = None if s is None else pg[s]
                    on[pid][g0 + j] = None if s is None else onsets(*pg[s])
                    tx[pid][g0 + j] = None if s is None else text_keys(pg[s][0])
                if round_ == 1:
                    dropped = sorted(set(range(len(pg))) - {s for s in slots if s is not None})
                    added = [g0 + j + 1 for j, s in enumerate(slots) if s is None]
                    report.append(f"  {pid} bars {g0 + 1}-{g1}: {len(pg)} bars read for {g1 - g0} between the "
                                  f"rehearsal marks -> aligned by rhythm; inserted rest bar(s) at {added}, dropped "
                                  f"source bar(s) {[g0 + k + 1 for k in dropped]} -- check")
                    for g in added:
                        flags[pid][g] = "check: bar inserted (this stretch read one bar short)"
                    for k in dropped:
                        nxt = next((j for j, s in enumerate(slots) if s is not None and s > k), None)
                        if nxt is not None:
                            flags[pid].setdefault(g0 + nxt + 1, "check: a source bar was dropped just before here")
    return total, bars, {pid: f for pid, f in flags.items() if f}


def check_against_marks(bars: Dict[str, Sequence[Optional[Src]]], flags: Dict[str, Dict[int, str]],
                        report: List[str]) -> None:
    """Independent evidence on a finished layout (by printed bar numbers or by rhythm): a rehearsal mark is printed over
    the same bar in every part, so a part whose copy of a mark that most parts agree on sits more than a bar away from
    where they have it has lost or gained bars on the way there. Reported, and the bar flagged for a look."""
    style = book_style(s[0] for seq in bars.values() for s in seq if s is not None)
    if style is None:
        return
    marks = {pid: part_marks(seq, style) for pid, seq in bars.items()}
    for label in _consensus_labels(marks, max(2, (len(bars) + 1) // 2)):
        at = {pid: m[label] for pid, m in marks.items() if label in m}
        want = _plurality(list(at.values()))
        for pid, b in sorted(at.items()):
            if abs(b - want) > 1:
                flags.setdefault(pid, {}).setdefault(b + 1, f"check: rehearsal mark {label[1]} is {abs(b - want)} "
                                                           f"bar(s) {'late' if b > want else 'early'} against the other parts")
                report.append(f"  {pid} bar {b + 1}: rehearsal mark {label[1]} (movement {label[0]}) sits {b - want:+d} "
                              f"bar(s) from where the other parts have it -- check")
