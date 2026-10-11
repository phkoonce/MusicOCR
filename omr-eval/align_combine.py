"""Align several single-part homr MusicXMLs to the printed bar numbers and
combine them into one multi-part MusicXML.

Why this is needed: each part is OMR'd separately, and homr (a) collapses
multi-measure rests to one measure, (b) loses the time-signature changes, and
(c) knows nothing about the *printed* bar numbers.  The printed numbers are the
only thing that can line the parts up, so they are read off the scans by eye
and fed in here as a SPEC: per part, which printed bar ranges are drawn as a
multi-rest ("21-28"), which single bars are whole-measure rests, plus the
score-wide meter map.  With that, homr's measure k is mapped to the k-th
printed *unit* (a bar or a multi-rest range); the mapping is verified (every
rest unit must land on a rest-only homr measure and vice versa) and every note
bar is checked against the true bar length.  Bars homr got structurally wrong
are repaired via the OVERRIDES hook.

Usage:
    .venv/bin/python omr-eval/align_combine.py <spec.py> <out.musicxml>

The spec module defines: PARTS, TOTAL_BARS, meter(bar), REST_BARS(part),
MULTIREST(part), and optionally fix_bar(part, bar, measure_elem, ctx).
See omr-eval/specs/last_airbender.py.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DIV = 10080  # homr's <divisions> (per quarter)


def load_spec(path: str):
    spec = importlib.util.spec_from_file_location("spec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def units(spec, part) -> list[tuple[int, int]]:
    starts = {a: (a, b) for a, b in spec.MULTIREST(part)}
    out, b = [], 1
    while b <= spec.TOTAL_BARS:
        if b in starts:
            out.append(starts[b])
            b = starts[b][1] + 1
        else:
            out.append((b, b))
            b += 1
    return out


def is_rest_only(m: ET.Element) -> bool:
    notes = m.findall("note")
    return all(n.find("rest") is not None for n in notes)


def bar_len(spec, bar: int) -> int:
    beats, beat_type = spec.meter(bar)
    return beats * DIV * 4 // beat_type


def measure_total(m: ET.Element) -> int:
    return sum(int(n.findtext("duration") or 0) for n in m.findall("note")
               if n.find("chord") is None and n.find("grace") is None)


def whole_rest(number: int, spec, first: bool, prev_meter, part_ctx) -> ET.Element:
    m = ET.Element("measure", number=str(number))
    _attributes(m, spec, number, first, prev_meter, part_ctx)
    n = ET.SubElement(m, "note")
    ET.SubElement(n, "rest", measure="yes")
    ET.SubElement(n, "duration").text = str(bar_len(spec, number))
    return m


def _attributes(m, spec, number, first, prev_meter, part_ctx):
    meter = spec.meter(number)
    if not first and meter == prev_meter:
        return
    at = ET.SubElement(m, "attributes")
    if first:
        ET.SubElement(at, "divisions").text = str(DIV)
        k = ET.SubElement(at, "key")
        ET.SubElement(k, "fifths").text = "0"
    t = ET.SubElement(at, "time")
    ET.SubElement(t, "beats").text = str(meter[0])
    ET.SubElement(t, "beat-type").text = str(meter[1])
    if first:
        c = ET.SubElement(at, "clef")
        ET.SubElement(c, "sign").text = "G"
        ET.SubElement(c, "line").text = "2"
        tr = ET.SubElement(at, "transpose")  # Horn in F: sounds a P5 lower
        ET.SubElement(tr, "diatonic").text = "-4"
        ET.SubElement(tr, "chromatic").text = "-7"


def words(text: str, above=True) -> ET.Element:
    d = ET.Element("direction", placement="above" if above else "below")
    dt = ET.SubElement(d, "direction-type")
    ET.SubElement(dt, "words").text = text
    return d


def tempo(text: str, bpm: int) -> ET.Element:
    d = ET.Element("direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    ET.SubElement(dt, "words").text = text
    ET.SubElement(d, "sound", tempo=str(bpm))
    return d


def clean_copy(src: ET.Element, number: int, spec, first, prev_meter, ctx) -> ET.Element:
    """Copy a homr measure's notes into a fresh measure (drop homr's own
    attributes/print/barline, which are unreliable)."""
    m = ET.Element("measure", number=str(number))
    _attributes(m, spec, number, first, prev_meter, ctx)
    for ch in src:
        if ch.tag != "note":
            continue
        n = copy.deepcopy(ch)
        for tag in ("stem",):
            for e in n.findall(tag):
                n.remove(e)
        m.append(n)
    return m


def build_part(spec, part, homr_path, report):
    root = ET.parse(homr_path).getroot()
    src = root.find("part").findall("measure")
    U = units(spec, part)
    if len(src) != len(U):
        report.append(f"  !! part {part}: homr has {len(src)} measures but the printed "
                      f"structure has {len(U)} units -- aligning by index would be wrong")
        raise SystemExit("\n".join(report))
    rest_bars = spec.REST_BARS(part)
    out, prev_meter = [], None
    for k, ((a, b), sm) in enumerate(zip(U, src), 1):
        multi = a != b
        want_rest = multi or a in rest_bars
        got_rest = is_rest_only(sm)
        override = getattr(spec, "fix_bar", None)
        fixed = None
        if override and not multi:
            fixed = override(part, a, sm, spec, sys.modules[__name__])
        if want_rest != got_rest and fixed is None:
            report.append(f"  part {part}: unit {a}{'-'+str(b) if multi else ''} <- homr#{k}: "
                          f"expected {'rest' if want_rest else 'notes'}, homr has "
                          f"{'rest' if got_rest else 'notes'}; using printed structure")
        for bar in range(a, b + 1):
            first = not out
            if fixed is not None and bar == a:
                m = fixed["measure"](bar, first, prev_meter)
                report.append(f"  part {part}: bar {bar} <- homr#{k}: repaired ({fixed['why']})")
            elif want_rest:
                m = whole_rest(bar, spec, first, prev_meter, None)
                if not multi and not got_rest:
                    report.append(f"  part {part}: bar {bar} <- homr#{k}: notes replaced by rest "
                                  f"(printed as a rest)")
            else:
                m = clean_copy(sm, bar, spec, first, prev_meter, None)
                tot, want = measure_total(m), bar_len(spec, bar)
                if tot != want:
                    report.append(f"  part {part}: bar {bar} <- homr#{k}: LENGTH {tot/DIV:g} beats, "
                                  f"expected {want/DIV:g} -- NEEDS MANUAL FIX")
            prev_meter = spec.meter(bar)
            out.append(m)
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    spec = load_spec(argv[0])
    report: list[str] = []
    score = ET.Element("score-partwise", version="3.1")
    ET.SubElement(ET.SubElement(score, "work"), "work-title").text = spec.TITLE
    ident = ET.SubElement(score, "identification")
    ET.SubElement(ident, "creator", type="composer").text = spec.COMPOSER
    pl = ET.SubElement(score, "part-list")
    parts = {}
    for p, (name, path) in spec.PARTS.items():
        sp = ET.SubElement(pl, "score-part", id=f"P{p}")
        ET.SubElement(sp, "part-name").text = name
        ET.SubElement(sp, "part-abbreviation").text = f"Hn {p}"
        mi = ET.SubElement(sp, "midi-instrument", id=f"P{p}-I1")
        ET.SubElement(mi, "midi-channel").text = str(p)
        ET.SubElement(mi, "midi-program").text = "61"
        report.append(f"part {p} ({name}):")
        parts[p] = build_part(spec, p, path, report)
    marks = getattr(spec, "MARKS", {})
    for p, ms in parts.items():
        pe = ET.SubElement(score, "part", id=f"P{p}")
        for m in ms:
            num = int(m.get("number"))
            # directions go after <attributes>, before notes
            extras = list(marks.get(p, {}).get(num, [])) + list(marks.get("all", {}).get(num, []))
            idx = 1 if m.find("attributes") is not None else 0
            for kind, *args in extras:
                m.insert(idx, tempo(*args) if kind == "tempo" else words(*args)); idx += 1
            pe.append(m)
        last = ms[-1]
        bl = ET.SubElement(last, "barline", location="right")
        ET.SubElement(bl, "bar-style").text = "light-heavy"
    ET.indent(score, space="  ")
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    body = ET.tostring(score, encoding="unicode")
    out.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
                   '"http://www.musicxml.org/dtds/partwise.dtd">\n' + body + "\n")
    print("\n".join(report))
    print(f"\nwrote {out} ({len(parts)} parts x {spec.TOTAL_BARS} bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
