"""Combine per-part OMR output into one score aligned to the printed bar numbers.

Engine-agnostic and spec-free: everything the old hand-written ``omr-eval/specs``
carried is derived from the data.

* **Printed structure** comes from each part PDF's text layer: the bar number at the
  start of the first system of every page (``page_text_layout``), so a page's expected
  bar count is the difference of consecutive page starts. The last page's end is the
  consensus total over all parts (the mode of start-of-last-page + bars found).
* **Multi-rests** are expanded from their ``<multiple-rest>`` tag (the tagged bar plus
  N-1 rest bars), so a bar's index within the page is its printed bar number.
* **Page-level repair.** A page whose bar count differs from the printed one is aligned
  to the printed bars by dynamic programming against the *other* parts' rest-only
  pattern at the same bar numbers (bars that most parts rest in are cheap to insert /
  delete); every repair is listed in the report as "check".
* **Key and meter** at each bar are the plurality over the parts' (carried-forward)
  declarations; the pickup length of bar 1 is the plurality of the parts' bar 1.
* Notes are copied from the part's own bar; bars that are rests only are rewritten as
  whole-measure rests of the consensus length, and any other bar whose notes do not fill
  the consensus meter is reported.

``combine`` returns ``(score, report, summary)``; ``musicocr combine`` is the CLI.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from musicocr.alignment.build import (add_attributes, build_score, check_length, copy_notes,
                                      is_rest_only, measure_total, rest_measure)
from musicocr.alignment.spec import DIV, bar_len
from musicocr.alignment.systems import page_text_layout, pdf_page_count, pdf_words
from musicocr.instruments import INFO, canonical_name, semitones_for
from musicocr.transpose import _declare_transpose, clef_family

Src = Tuple[ET.Element, int]            # (measure, its <divisions>)


@dataclass
class PartInput:
    pid: str
    label: str
    instrument: str
    pdf: Optional[Path]
    pages: List[Path]


# ---- reading a part ---------------------------------------------------------------

def _mr_count(m: ET.Element) -> int:
    t = m.find("./attributes/measure-style/multiple-rest")
    try:
        return int((t.text or "").strip()) if t is not None else 0
    except ValueError:
        return 0


def read_page(path: Path) -> List[Src]:
    """A page's measures with multi-rests expanded into one rest bar per printed bar."""
    part = ET.parse(path).getroot().find("part")
    out: List[Src] = []
    div = 1
    for m in (part.findall("measure") if part is not None else []):
        dv = m.find("attributes/divisions")
        if dv is not None and (dv.text or "").strip().isdigit():
            div = int(dv.text)
        out.append((m, div))
        n = _mr_count(m)
        if n > 1:
            tail = _take_tail(m)
            for k in range(n - 1):
                rm = rest_measure(0, max(measure_total(m), 1), True)
                if k == n - 2:
                    rm.extend(tail)          # the closing barline / courtesy clef end the last bar
                out.append((rm, div))
    return out


def _take_tail(m: ET.Element) -> List[ET.Element]:
    """Remove and return what closes a measure: its right barline and any attributes after the notes."""
    tail, seen_note = [], False
    for el in list(m):
        if el.tag == "note":
            seen_note = True
        elif (el.tag == "attributes" and seen_note) or (el.tag == "barline" and el.get("location", "right") == "right"):
            m.remove(el)
            tail.append(el)
    return tail


def printed_page_starts(pdf: Path, pdftotext: str = "pdftotext") -> List[Optional[int]]:
    """Bar number at the start of the first system of each page (None = unreadable)."""
    out: List[Optional[int]] = []
    for pg in range(1, pdf_page_count(str(pdf)) + 1):
        starts, _ = page_text_layout(pdf_words(str(pdf), pg, pdftotext))
        n = starts[0][1] if starts else None
        out.append(1 if pg == 1 and (n is None or n > 3) else n)
    return out


def split_overlong(m: ET.Element, div: int, meter: Tuple[int, int]) -> Optional[List[ET.Element]]:
    """Split a measure that holds k >= 2 whole bars of ``meter`` (two bars read as one).

    Returns the k measures when every bar line falls exactly on a note boundary of this
    single-voice measure, else None. Attributes stay on the first piece, a trailing
    barline goes to the last.
    """
    if m.find("backup") is not None or m.find("forward") is not None:
        return None
    bar = div * 4 * meter[0] // meter[1]
    if bar <= 0 or div * 4 * meter[0] % meter[1]:
        return None
    total = measure_total(m)
    if total < 2 * bar or total % bar:
        return None
    k = total // bar
    pieces = [ET.Element("measure", number="0") for _ in range(k)]
    pos, idx = 0, 0
    for el in list(m):
        if el.tag == "note":
            if el.find("chord") is None and el.find("grace") is None:
                if pos % bar and pos // bar != (pos + int(el.findtext("duration") or 0) - 1) // bar:
                    return None             # a note straddles a bar line
                idx = min(pos // bar, k - 1)
                pos += int(el.findtext("duration") or 0)
        elif el.tag == "barline":
            idx = k - 1
        elif el.tag != "attributes" and pos and pos % bar == 0 and idx < pos // bar:
            idx = min(pos // bar, k - 1)    # a direction right at the bar line starts the next bar
        pieces[0 if el.tag == "attributes" else idx].append(el)
    return pieces if all(p.find("note") is not None for p in pieces) else None


def printed_last_system_start(pdf: Path, pdftotext: str = "pdftotext") -> Optional[int]:
    """Bar number at the start of the last numbered system on the part's last page."""
    n = pdf_page_count(str(pdf))
    for pg in range(n, 0, -1):
        starts, _ = page_text_layout(pdf_words(str(pdf), pg, pdftotext))
        if starts:
            return starts[-1][1]
    return None


_RANGE = re.compile(r"(\d+)\s*[\u2013\u2014]\s*(\d+)")


def printed_multirest_spans(pdf: Path, pdftotext: str = "pdftotext") -> List[Tuple[int, int]]:
    """Bar ranges printed under multi-rests ("58\u201360") in the text layer, when the engraver
    prints them. En/em dash only: a hyphen is too often something else ("1-4")."""
    out: List[Tuple[int, int]] = []
    for pg in range(1, pdf_page_count(str(pdf)) + 1):
        for *_, w in pdf_words(str(pdf), pg, pdftotext):
            m = _RANGE.fullmatch(w.strip())
            if m and int(m.group(2)) > int(m.group(1)):
                out.append((int(m.group(1)), int(m.group(2))))
    return sorted(set(out))


# ---- alignment --------------------------------------------------------------------

def _rest_flag(src: Src) -> float:
    return 1.0 if is_rest_only(src[0]) else 0.0


def align_to_target(rest_src: Sequence[float], ref: Sequence[float],
                    ins_cost: Optional[Sequence[float]] = None) -> List[Optional[int]]:
    """Map each of ``len(ref)`` target slots to a source bar index (None = synthesise a rest).

    Needleman-Wunsch over the rest-only pattern: matching costs the disagreement with the
    reference (fraction of other parts resting there); inserting a slot is cheap where the
    reference rests, deleting a source bar is cheap if it is a rest. Only as many indels as
    the length difference needs are allowed (insertions only when the source is short,
    deletions only when it is long), so a one-bar error never becomes a rearrangement.
    """
    n, m = len(rest_src), len(ref)
    # ``ins_cost`` (per slot) overrides the default insertion cost: a part that prints its
    # multi-rest spans can only have lost bars inside them
    allow_del, allow_ins = n >= m, n <= m
    INF = 1e18
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    B: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            d = D[i][j]
            if d >= INF:
                continue
            if i < n and j < m and d + abs(rest_src[i] - ref[j]) < D[i + 1][j + 1]:
                D[i + 1][j + 1], B[i + 1][j + 1] = d + abs(rest_src[i] - ref[j]), "m"
            if i < n and allow_del:
                c = d + 0.2 + 0.8 * (1 - rest_src[i])
                if c < D[i + 1][j]:
                    D[i + 1][j], B[i + 1][j] = c, "d"
            if j < m and allow_ins:
                # a hair cheaper later in the page: a lost bar is inserted *after* its run's
                # first bar, so a key/meter declaration stays on the first bar of the run
                base = ins_cost[j] if ins_cost is not None else 0.2 + 0.8 * (1 - ref[j])
                c = d + base + 1e-4 * (m - j) / max(m, 1)
                if c < D[i][j + 1]:
                    D[i][j + 1], B[i][j + 1] = c, "i"
    slots: List[Optional[int]] = [None] * m
    i, j = n, m
    while i or j:
        op = B[i][j]
        if op == "m":
            slots[j - 1] = i - 1
            i, j = i - 1, j - 1
        elif op == "d":
            i -= 1
        else:
            j -= 1
    return slots


# ---- declarations -----------------------------------------------------------------

def _declared(m: ET.Element) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
    k = m.find("attributes/key/fifths")
    t = m.find("attributes/time")
    key = int(k.text) if k is not None and (k.text or "").lstrip("-").isdigit() else None
    meter = None
    if t is not None:
        try:
            meter = (int(t.findtext("beats")), int(t.findtext("beat-type")))
        except (TypeError, ValueError):
            pass
    return key, meter


def _clefs(src: Optional[Src]) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
    """(leading, trailing) clef of a source bar. Copisteria writes the courtesy clef printed at the end
    of a system as an ``<attributes>`` *after* the bar's last note: it takes effect in the next bar."""
    lead = trail = None
    seen_note = False
    for el in (src[0] if src is not None else []):
        if el.tag == "note":
            seen_note = True
        elif el.tag == "attributes":
            c = el.find("clef")
            if c is not None and c.findtext("sign"):
                val = (c.findtext("sign"), c.findtext("line") or "2")
                if seen_note:
                    trail = val
                else:
                    lead = val
    return lead, trail


def _first_clef(srcs: Sequence[Optional[Src]]) -> Tuple[str, str]:
    for s in srcs:
        lead, _ = _clefs(s)
        if lead:
            return lead
    return ("G", "2")


def _plurality(values: Sequence[Any], default: Any) -> Any:
    vals = [v for v in values if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else default


def part_info(label: str, instrument: str) -> Dict[str, Any]:
    canon = canonical_name(instrument) if instrument else None
    canon = canon or canonical_name(label)
    num = re.search(r"(\d+)\s*$", label)
    suffix = f" {num.group(1)}" if num else ""
    if canon in INFO:
        _, abbr, sound, program = INFO[canon]
        return dict(name=label, abbr=abbr.rstrip(".") + suffix, program=program, sound=sound)
    return dict(name=label, abbr=label[:4].strip() + suffix, program=1)


def copy_marks(src: ET.Element, dst: ET.Element, src_divisions: int, *, notes: bool = True) -> None:
    """Copy a source bar's content into ``dst`` in document order: notes (as ``copy_notes``),
    the directions (dynamics, hairpins) that sit between them, and double bars. A light-heavy
    barline is dropped (the combined score writes its own final one). ``notes=False`` copies
    only the directions/double bars, for a bar that is rewritten as a whole-bar rest."""
    scale = DIV / src_divisions if src_divisions else 1
    for ch in src:
        if ch.tag == "note" and notes:
            nn = copy.deepcopy(ch)
            for e in nn.findall("stem"):
                nn.remove(e)
            du = nn.find("duration")
            if du is not None and src_divisions != DIV:
                du.text = str(round(int(du.text) * scale))
            dst.append(nn)
        elif ch.tag == "direction" or (ch.tag == "barline" and ch.findtext("bar-style") == "light-light"):
            nn = copy.deepcopy(ch)
            off = nn.find("offset")
            if off is not None and off.text:
                off.text = str(round(int(float(off.text)) * scale))
            if nn.tag == "direction" and not notes:
                for t in nn.findall("staff"):
                    nn.remove(t)
            dst.append(nn)


def balance_wedges(measures: List[ET.Element]) -> int:
    """Drop hairpin starts without a later stop and stops without an open start (a repaired or
    dropped bar can orphan one). Returns how many were removed."""
    opened: Optional[ET.Element] = None
    owner: Dict[int, ET.Element] = {}
    drop: List[Tuple[ET.Element, ET.Element]] = []
    for m in measures:
        for d in m.findall("direction"):
            for w in d.iter("wedge"):
                kind = w.get("type")
                if kind in ("crescendo", "diminuendo"):
                    if opened is not None:
                        drop.append(owner[id(opened)])
                    opened = w
                    owner[id(w)] = (m, d)
                elif kind == "stop":
                    if opened is None:
                        drop.append((m, d))
                    opened = None
    if opened is not None:
        drop.append(owner[id(opened)])
    for m, d in drop:
        if d in list(m):
            m.remove(d)
    return len(drop)


CHECK_COLOR = "#D32F2F"


def _flag_bar(m: ET.Element, text: str) -> None:
    """Colour the bar's notes/rests and put a short note above it, so a bar that needs a human look
    stands out in MuseScore (gap rests keep their own orange)."""
    for n in m.findall("note"):
        n.set("color", n.get("color") or CHECK_COLOR)
    d = ET.Element("direction", placement="above")
    ET.SubElement(ET.SubElement(d, "direction-type"), "words", color=CHECK_COLOR).text = text
    idx = 1 if m.find("attributes") is not None else 0
    m.insert(idx, d)


# ---- combine ----------------------------------------------------------------------

def combine(parts: Sequence[PartInput], title: str, *, pdftotext: str = "pdftotext",
            read_pages: Callable[[Path], List[Src]] = read_page,
            starts_of: Callable[[Path], List[Optional[int]]] = None,
            last_start_of: Callable[[Path], Optional[int]] = None,
            spans_of: Callable[[Path], List[Tuple[int, int]]] = None,
            ) -> Tuple[ET.Element, List[str], Dict[str, Any]]:
    starts_of = starts_of or (lambda pdf: printed_page_starts(pdf, pdftotext))
    last_start_of = last_start_of or (lambda pdf: printed_last_system_start(pdf, pdftotext))
    spans_of = spans_of or (lambda pdf: printed_multirest_spans(pdf, pdftotext))
    report: List[str] = []
    data: Dict[str, Dict[str, Any]] = {}
    for p in parts:
        pages, meter = [], (4, 4)
        for f in p.pages:
            pg: List[Src] = []
            for src in read_pages(f):
                _, mt = _declared(src[0])
                meter = mt or meter
                parts_ = split_overlong(src[0], src[1], meter) if not _mr_count(src[0]) else None
                if parts_:
                    report.append(f"  {p.pid}: a {len(parts_)}-bar-long measure read as one (page "
                                  f"{len(pages) + 1}, measure {len(pg) + 1}) -> split at the bar lines")
                    pg.extend((q, src[1]) for q in parts_)
                else:
                    pg.append(src)
            pages.append(pg)
        starts = starts_of(p.pdf) if p.pdf and p.pdf.exists() else [None] * len(pages)
        if len(starts) != len(pages):
            report.append(f"{p.pid}: {len(pages)} page file(s) but {len(starts)} PDF page(s) "
                          f"-- structure not verified")
            starts = [None] * len(pages)
        # a bar number never repeats: if a later page starts at bar 1, every page before it
        # (a cover or text page) holds no printed bars
        for k, s in enumerate(starts):
            if k and s == 1:
                starts[:k] = [1] * k
                report.append(f"{p.pid}: page {k + 1} starts at bar 1 -> page(s) 1-{k} hold no printed bars")
                break
        if all(s is None for s in starts):       # scan / no text layer: the part is one "page"
            pages, starts = [[s for pg in pages for s in pg]], [1]
            report.append(f"{p.pid}: no printed bar numbers (scan?) -> checked against the "
                          f"other parts' total only")
        spans = spans_of(p.pdf) if p.pdf and p.pdf.exists() else []
        data[p.pid] = dict(p=p, pages=pages, starts=starts, spans=spans,
                           rests={b for a, e in spans for b in range(a, e + 1)})

    # score-wide total: start of a part's last page + the bars it found there
    totals = [d["starts"][-1] - 1 + len(d["pages"][-1]) for d in data.values()
              if d["starts"] and d["starts"][-1] is not None]
    if not totals:
        raise ValueError("no part has a readable printed bar number; cannot derive the structure")
    # the last printed system starts at bar L, so the piece has at least L bars
    lasts = [last_start_of(d["p"].pdf) for d in data.values() if d["p"].pdf and d["p"].pdf.exists()]
    ends = [e for d in data.values() for _, e in d["spans"]]
    lower = max([x for x in lasts if x] + ends + [0])
    cand = [t for t in totals if t >= lower]
    if cand:
        top = Counter(cand).most_common()
        total = max(t for t, c in top if c == top[0][1])     # a tie goes to the larger total
    else:                        # every part is short (lost multi-rests): the print's own lower bound
        total = lower
        report.append(f"score: no part reaches the printed lower bound ({lower}); using it")
    report.append(f"score: {len(data)} parts, {total} bars "
                  f"(last-page totals {dict(Counter(totals))})")

    # per-page target length; mark pages that already match the print
    for pid, d in data.items():
        d["target"], d["start"], bar = [], [], 1
        for i, pg in enumerate(d["pages"]):
            s = d["starts"][i]
            if s is not None:
                bar = s
            nxt = d["starts"][i + 1] if i + 1 < len(d["pages"]) else total + 1
            tgt = (nxt - bar) if nxt is not None else len(pg)
            d["start"].append(bar)
            d["target"].append(tgt)
            bar += tgt
        d["good"] = [len(pg) == t for pg, t in zip(d["pages"], d["target"])]

    # global rest-only pattern over bars that sit on a matching page
    sums: Dict[int, List[float]] = {}
    for d in data.values():
        for pg, a, ok in zip(d["pages"], d["start"], d["good"]):
            if ok:
                for j, s in enumerate(pg):
                    acc = sums.setdefault(a + j, [0.0, 0.0])
                    acc[0] += _rest_flag(s)
                    acc[1] += 1
    ref = {b: v[0] / v[1] for b, v in sums.items()}

    # repair mismatched pages, then lay every part out on bars 1..total
    bars: Dict[str, List[Optional[Src]]] = {}
    flags: Dict[str, Dict[int, str]] = {}
    for pid, d in data.items():
        seq: List[Optional[Src]] = []
        for pg, a, t, ok in zip(d["pages"], d["start"], d["target"], d["good"]):
            if ok:
                seq.extend(pg)
                continue
            if t <= 0:
                report.append(f"  {pid} page starting bar {a}: no printed bars (cover/text page?) "
                              f"-> {len(pg)} measure(s) ignored")
                continue
            r = [1.0 if (a + j) in d["rests"] else ref.get(a + j) for j in range(t)]
            if sum(x is not None for x in r) >= max(t // 4, 1):
                ins = [0.01 if (a + j) in d["rests"] else 2.0 for j in range(t)] if d["rests"] else None
                slots = align_to_target([_rest_flag(s) for s in pg],
                                        [0.5 if x is None else x for x in r], ins)
                dropped = sorted(set(range(len(pg))) - {s for s in slots if s is not None})
                added = [a + j for j, s in enumerate(slots) if s is None]
                outside = [b for b in added if d["rests"] and b not in d["rests"]]
                report.append(f"  {pid} page bars {a}-{a + t - 1}: {len(pg)} bars read for {t} printed"
                              f" -> aligned to the other parts' rest pattern; dropped source bar(s) "
                              f"{[a + k for k in dropped]}, inserted rest bar(s) at {added}"
                              + (f", {len(outside)} of them outside the printed multi-rests {outside}"
                                 if outside else "") + " -- check")
                seq.extend(None if s is None else pg[s] for s in slots)
                fl = flags.setdefault(pid, {})
                for b in added:
                    fl[b] = "check: bar inserted (the page read one bar short)"
                for k in dropped:
                    nxt_slot = next((j for j, s in enumerate(slots) if s is not None and s > k), None)
                    if nxt_slot is not None:
                        fl.setdefault(a + nxt_slot, "check: a source bar was dropped just before here")
            else:
                seq.extend((pg + [None] * t)[:t])
                report.append(f"  {pid} page bars {a}-{a + t - 1}: {len(pg)} bars read for {t} printed"
                              f" and no reference to align by -> padded/truncated at the end -- CHECK")
        if len(seq) < total:
            seq += [None] * (total - len(seq))
            report.append(f"  {pid}: ends {total - len([s for s in seq if s is not None])} bar(s) early -> padded")
        bars[pid] = seq[:total]

    # consensus key / meter per bar, and the pickup
    key_by: Dict[str, List[Optional[int]]] = {}
    met_by: Dict[str, List[Optional[Tuple[int, int]]]] = {}
    for pid, seq in bars.items():
        ck: Optional[int] = None
        cm: Optional[Tuple[int, int]] = None
        ks, ms = [], []
        for s in seq:
            if s is not None:
                k, mt = _declared(s[0])
                ck, cm = (k if k is not None else ck), (mt if mt is not None else cm)
            ks.append(ck)
            ms.append(cm)
        key_by[pid], met_by[pid] = ks, ms
    keys = [_plurality([key_by[p][b] for p in bars], 0) for b in range(total)]
    meters = [_plurality([met_by[p][b] for p in bars], (4, 4)) for b in range(total)]
    first_len = _plurality(
        [round(measure_total(s[0]) / s[1], 4) for s in (seq[0] for seq in bars.values())
         if s is not None and s[0].find("note") is not None], None)
    full = meters[0][0] * 4 / meters[0][1]
    first_beats = first_len if first_len and first_len < full - 1e-6 else None
    spec = SimpleNamespace(TITLE=title, COMPOSER="", TOTAL_BARS=total,
                           meter=lambda b: meters[b - 1], key_fifths=lambda b: keys[b - 1],
                           FIRST_BAR_BEATS=first_beats, MARKS={})
    if first_beats:
        report.append(f"score: bar 1 is a {first_beats:g}-beat pickup (plurality of the parts)")
    changes = [(b + 1, keys[b], meters[b]) for b in range(total)
               if b == 0 or keys[b] != keys[b - 1] or meters[b] != meters[b - 1]]
    report.append("score: key/meter changes (bar, fifths, meter): "
                  + "; ".join(f"{b}: {k:+d} {m[0]}/{m[1]}" for b, k, m in changes))

    # build every part
    built: Dict[int, List[ET.Element]] = {}
    infos: Dict[int, Dict[str, Any]] = {}
    summary = dict(total=total, parts=len(data), key_meter=changes, bad_bars=0, repairs=0)
    for n, (pid, seq) in enumerate(bars.items(), 1):
        d = data[pid]
        infos[n] = part_info(d["p"].label, d["p"].instrument)
        report.append(f"part {n} ({d['p'].label}):")
        clef = _first_clef(seq)
        cur_clef: Optional[Tuple[str, str]] = None
        prev_key = prev_met = None
        prev_trail = None
        out: List[ET.Element] = []
        for b, src in enumerate(seq, 1):
            m = ET.Element("measure", number=str(b))
            lead, trail = _clefs(src)
            here = lead or prev_trail
            prev_trail = trail
            want = clef if cur_clef is None else (here if here and here != cur_clef else None)
            key, meter = spec.key_fifths(b), spec.meter(b)
            add_attributes(m, divisions=DIV if not out else None,
                           key=key if (not out or key != prev_key) else None,
                           meter=meter if (not out or meter != prev_met) else None,
                           clef=want)
            cur_clef = want or cur_clef
            length = bar_len(spec, b)
            if src is None or is_rest_only(src[0]):
                full_bar = length == meter[0] * DIV * 4 // meter[1]
                if src is not None:
                    copy_marks(src[0], m, src[1], notes=False)
                m.append(rest_measure(b, length, full_bar).find("note"))
            else:
                copy_marks(src[0], m, src[1])
                bad = check_length(m, spec, b)
                if bad:
                    report.append(f"  bar {b}: LENGTH {bad[0]:g} beats, expected {bad[1]:g}")
                    summary["bad_bars"] += 1
                    note = f"check: {bad[0]:g} beats, expected {bad[1]:g}"
                    flags.setdefault(pid, {})[b] = note + (" / " + flags[pid][b] if b in flags.get(pid, {}) else "")
            prev_key, prev_met = key, meter
            if b in flags.get(pid, {}):
                _flag_bar(m, flags[pid][b])
            out.append(m)
        built[n] = out
        dropped = balance_wedges(out)
        if dropped:
            report.append(f"  dropped {dropped} unmatched hairpin(s)")
    summary["repairs"] = sum(1 for r in report if "-- check" in r.lower())
    score = build_score(spec, built, infos)
    # The notes stay as printed (written pitch); declare each part's transposition so the
    # score sounds right (the per-part files carry it, the page files this reads do not).
    for n, pe in zip(infos, score.findall("part")):
        d = data[list(bars)[n - 1]]["p"]
        try:
            semis = semitones_for(d.instrument or d.label, clef_family(pe))
        except ValueError as e:
            report.append(f"part {n} ({d.label}): no transposition declared ({e})")
            continue
        _declare_transpose(pe, semis)
    return score, report, summary
