#!/usr/bin/env python
"""Score the marks pass against a hand-cleaned reference.

    scripts/compare_marks.py before.musicxml reference.musicxml [--rules dedupe,hairpins,fill]

``before`` is a combined score, ``reference`` the same score after a manual pass (export it from
MuseScore as MusicXML); parts are matched by name and parts missing from either are skipped.
Dynamics are compared per (bar, value), hairpins per (kind, start bar) by where they end.
Hairpin positions are absolute, so this assumes both scores have the same bar lengths.
"""
import argparse
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musicocr.scoreops.marks import MarksOptions, _pairs, _scan, normalize_marks  # noqa: E402


def load(path):
    root = ET.parse(path).getroot()
    names = {sp.get("id"): sp.findtext("part-name") for sp in root.iterfind("part-list/score-part")}
    return [(names[p.get("id")], p.findall("measure")) for p in root.findall("part")]


def facts(parts, names):
    dyn, hp = {}, {}
    for n, ms in parts:
        if n not in names:
            continue
        p = _scan(n, ms)
        dyn[n] = {(m.bar, m.value) for m in p.marks if m.kind == "dyn"}
        hp[n] = {(a.value, a.bar): p.abs((z.bar, z.pos)) for a, z in _pairs(p)}
    return dyn, hp


def prf(got, want):
    tp = len(got & want)
    return tp, len(got - want), len(want - got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("reference")
    ap.add_argument("--rules", default="dedupe,hairpins")
    a = ap.parse_args()
    before, ref = load(a.before), load(a.reference)
    names = {n for n, _ in before} & {n for n, _ in ref}
    after = copy.deepcopy(before)
    stats, _ = normalize_marks(after, MarksOptions.parse(a.rules))
    print(stats)
    rd, rh = facts(ref, names)
    for tag, parts in (("before", before), ("after ", after)):
        d, h = facts(parts, names)
        tp = fp = fn = 0
        for n in names:
            x, y, z = prf(d[n], rd[n])
            tp, fp, fn = tp + x, fp + y, fn + z
        ends = [abs(h[n][k] - rh[n][k]) <= 1 for n in names for k in h[n] if k in rh[n]]
        print(f"{tag} dynamics: {tp} match, {fp} not in reference, {fn} missing "
              f"(precision {tp / max(tp + fp, 1):.2f}, recall {tp / max(tp + fn, 1):.2f}); "
              f"hairpin ends within a beat of the reference: {sum(ends)}/{len(ends)}")


if __name__ == "__main__":
    main()
