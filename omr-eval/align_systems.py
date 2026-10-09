"""Align per-part homr output to the printed bar numbers and combine into one score.

Unlike align_combine.py (hand-written per-part multi-rest/rest-bar lists), the bar structure
is derived automatically from the part PDFs themselves:

  * the printed bar number at the start of every system (pdftotext -bbox, left margin),
  * the printed multi-rest counts (large digits above a multi-rest bar),
  * homr's own per-page MusicXML, whose <print new-system> marks give how many measures it
    produced for each system.

For a system with B printed bars and multi-rests c_1..c_m, homr should have produced
B - sum(c_i - 1) measures.  When it did, the multi-rest counts are assigned to rest-only
measures and every measure gets its printed bar number.  When it didn't, the common repairs
are applied and *reported*: stray trailing fragment measures dropped, a lost multi-rest
re-inserted.  Anything else is left as printed-structure rests and flagged.

Usage:  .venv/bin/python omr-eval/align_systems.py omr-eval/specs/zerberus.py out.musicxml
"""
from __future__ import annotations
import copy, importlib.util, re, subprocess, sys
import xml.etree.ElementTree as ET
from pathlib import Path

DIV = 10080


def load_spec(path):
    s = importlib.util.spec_from_file_location("spec", path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def _words(pdf, pg):
    t = subprocess.run(["pdftotext", "-bbox", "-f", str(pg), "-l", str(pg), pdf, "-"],
                       capture_output=True, text=True).stdout
    return [(float(a), float(b), float(c), float(d), w) for a, b, c, d, w in
            re.findall(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', t)]


def is_rest_only(m):
    return all(n.find("rest") is not None for n in m.findall("note"))


def measure_total(m):
    return sum(int(n.findtext("duration") or 0) for n in m.findall("note")
               if n.find("chord") is None and n.find("grace") is None)


def systems_of(spec, pid):
    """-> list of dict(start, B, units=[(merged_index, is_tagged_mr, tag_count)], counts=[(x,c)], x_range)"""
    P = spec.PARTS[pid]
    pdf, d, key = P["pdf"], P["dir"], P["key"]
    npg = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout).group(1))
    systems, offset, allm = [], 0, []
    for pg in range(1, npg + 1):
        ws = _words(pdf, pg)
        starts = sorted((y0, int(w)) for x0, y0, x1, y1, w in ws
                        if w.isdigit() and x0 < 70 and y0 > 80 and (y1 - y0) < 20)
        cand = [(x0, y0, y1, w) for x0, y0, x1, y1, w in ws if w.isdigit() and x0 >= 70 and y0 > 80]
        tall = [c for c in cand if c[2] - c[1] >= 25]
        ts = {c for a in tall for b in tall for c in (a, b)
              if a is not b and abs(a[0] - b[0]) < 6 and 0 < b[1] - a[1] < 14}
        counts = [(x, y, int(w)) for x, y, y1, w in cand
                  if (x, y, y1, w) not in ts and y1 - y >= 25 and int(w) not in (44, 68)]
        page = ET.parse(f"{d}/Zerberus_{key}.p{pg:03d}.musicxml").getroot()
        ms = page.find("part").findall("measure")
        groups = [[]]
        div = 1
        for i, m in enumerate(ms):
            dv = m.find("attributes/divisions")
            if dv is not None: div = int(dv.text)
            allm.append((m, div))
            p = m.find("print")
            if p is not None and p.get("new-system") == "yes" and groups[-1]:
                groups.append([])
            mr = m.find(".//multiple-rest")
            groups[-1].append((offset + i, int(mr.text) if mr is not None else 0))
        ys, sn = [y for y, _ in starts], [n for _, n in starts]
        if pg == 1 and len(groups) == len(sn) + 1:
            ys, sn = [0] + ys, [1] + sn
        if len(groups) != len(sn):
            raise SystemExit(f"{key} page {pg}: {len(groups)} homr systems but {len(sn)} printed system numbers")
        for gi, g in enumerate(groups):
            lo = ys[gi] - 45 if gi else -1
            hi = ys[gi + 1] - 45 if gi + 1 < len(ys) else 1e9
            systems.append(dict(page=pg, idx=gi, start=sn[gi], units=g,
                                counts=sorted((x, c) for x, y, c in counts if lo <= y < hi)))
        offset += len(ms)
    for i, s in enumerate(systems):
        nxt = systems[i + 1]["start"] if i + 1 < len(systems) else spec.TOTAL_BARS + 1
        s["B"] = nxt - s["start"]
    return systems, allm


def plan_part(spec, pid, M, report):
    """-> list of units (a, b, measure_or_None) covering bars 1..TOTAL_BARS"""
    systems, M = systems_of(spec, pid)
    out = []
    for s in systems:
        tag = f"p{s['page']}s{s['idx']} (bar {s['start']})"
        units = list(s["units"]); counts = list(s["counts"])
        expected = s["B"] - sum(c - 1 for _, c in counts)
        e = len(units) - expected
        if e > 0:
            dropped = units[-e:]; units = units[:-e]
            report.append(f"  {tag}: homr had {e} extra trailing measure(s) -> dropped "
                          f"(homr measure {[u[0] + 1 for u in dropped]})")
        # assign multi-rest counts to rest-only units
        def is_r(k): return is_rest_only(M[units[k][0]][0])
        W = [1.3 if is_r(k) else 1.0 for k in range(len(units))]
        tot = sum(W) or 1
        centre = [(sum(W[:k]) + W[k] / 2) / tot for k in range(len(units))]
        cand = [k for k in range(len(units)) if is_r(k)]
        chosen = [k for k in cand if units[k][1]][:len(counts)]
        n_tagged = len(chosen)
        pool = [k for k in cand if k not in chosen]
        todo = sorted(counts)[len(chosen):] if len(chosen) < len(counts) else []
        amb = len(cand) > len(counts) and not todo and len(chosen) < len(counts)
        for x, c in sorted(counts, key=lambda t: -t[0]):     # untagged counts: nearest rest-only unit by x
            if len(chosen) >= len(counts) or not pool: break
            fr = (x - 62) / 500
            k = min(pool, key=lambda q: abs(centre[q] - fr)); pool.remove(k); chosen.append(k)
        if counts and len(cand) > len(counts) and n_tagged < len(counts):
            report.append(f"  {tag}: {len(cand)} rest-only measures for {len(counts)} multi-rest(s) -- "
                          f"picked by position on the page, check")
        if len(chosen) < len(counts) and e == 0:             # multi-rest misread as a note: pick the odd one out
            notes = [k for k in range(len(units)) if k not in chosen and not is_r(k)]
            durs = sorted(measure_total(M[units[k][0]][0]) * DIV / M[units[k][0]][1] for k in notes)
            med = durs[len(durs) // 2] if durs else 0
            for x, c in sorted(counts, key=lambda t: -t[0])[:len(counts) - len(chosen)]:
                if not notes: break
                k = max(notes, key=lambda q: abs(measure_total(M[units[q][0]][0]) * DIV / M[units[q][0]][1] - med))
                notes.remove(k); chosen.append(k)
                report.append(f"  {tag}: {c}-bar multi-rest was read as notes (homr measure {units[k][0] + 1}) -> replaced by rests")
        chosen.sort()
        slots = {k: c for k, (_, c) in zip(chosen, sorted(counts))}
        missing = sorted(counts)[len(chosen):]
        seq = [(u, slots.get(k)) for k, u in enumerate(units)]
        if e < 0:
            if len(missing) == -e:
                for x, c in missing:       # re-insert lost multi-rest(s), position by x estimate
                    pos = min(len(seq), max(0, round((x - 62) / 500 * (len(seq) + 1) - 0.5)))
                    seq.insert(pos, (None, c))
                    report.append(f"  {tag}: homr lost a {c}-bar multi-rest -> re-inserted after "
                                  f"unit {pos} (position estimated, check)")
            else:
                report.append(f"  {tag}: homr has {-e} measure(s) too few and no multi-rest to blame "
                              f"-- NEEDS MANUAL FIX; padding with rests")
                seq += [(None, 1)] * (-e)
        bar = s["start"]
        for u, c in seq:
            span = c if c else 1
            out.append((bar, bar + span - 1, None if u is None else M[u[0]], c is not None))
            bar += span
        if bar != s["start"] + s["B"]:
            report.append(f"  {tag}: !! bars {s['start']}..{bar - 1} but expected {s['B']} bars -- mismatch")
    return out


# ---------------------------------------------------------------- output
def bar_len(spec, bar):
    if bar == 1 and getattr(spec, "FIRST_BAR_BEATS", None):
        return spec.FIRST_BAR_BEATS * DIV
    b, t = spec.meter(bar)
    return b * DIV * 4 // t


def attrs(spec, m, bar, first, prev_meter, prev_key, clef):
    meter, key = spec.meter(bar), spec.key_fifths(bar)
    nat = spec.meter(bar) if bar > 1 else spec.meter(1)
    need = first or meter != prev_meter or key != prev_key or clef is not None
    if not need: return
    at = ET.SubElement(m, "attributes")
    if first: ET.SubElement(at, "divisions").text = str(DIV)
    if first or key != prev_key:
        k = ET.SubElement(at, "key"); ET.SubElement(k, "fifths").text = str(key)
    if first or meter != prev_meter:
        t = ET.SubElement(at, "time"); ET.SubElement(t, "beats").text = str(meter[0]); ET.SubElement(t, "beat-type").text = str(meter[1])
    if clef is not None:
        c = ET.SubElement(at, "clef"); ET.SubElement(c, "sign").text = clef[0]; ET.SubElement(c, "line").text = clef[1]


def direction(kind, *a):
    d = ET.Element("direction", placement="above")
    dt = ET.SubElement(d, "direction-type")
    if kind == "rehearsal":
        ET.SubElement(dt, "rehearsal").text = a[0]
    else:
        ET.SubElement(dt, "words").text = a[0]
    if kind == "tempo": ET.SubElement(d, "sound", tempo=str(a[1]))
    return d


def rest_measure(spec, bar):
    m = ET.Element("measure", number=str(bar))
    return m


def build_part(spec, pid, report):
    P = spec.PARTS[pid]
    plan = plan_part(spec, pid, None, report)
    default_clef = ("G", "2") if P["clef"] == "treble" else ("F", "4")
    cur_clef, prev_meter, prev_key, out = None, None, None, []
    for a, b, srcd, is_mr in plan:
        src, sdiv = srcd if srcd is not None else (None, 1)
        for bar in range(a, b + 1):
            m = ET.Element("measure", number=str(bar))
            rest = is_mr or src is None or is_rest_only(src)
            clef_el = src.find("attributes/clef") if src is not None else None
            clef = (clef_el.findtext("sign"), clef_el.findtext("line")) if clef_el is not None else None
            if cur_clef is None: want = clef or default_clef
            else: want = clef if (clef and clef != cur_clef) else None
            attrs(spec, m, bar, not out, prev_meter, prev_key, want)
            if want: cur_clef = want
            L = bar_len(spec, bar)
            if rest:
                n = ET.SubElement(m, "note")
                full = L == (spec.meter(bar)[0] * DIV * 4 // spec.meter(bar)[1])
                if full: ET.SubElement(n, "rest", measure="yes")
                else: ET.SubElement(n, "rest")
                ET.SubElement(n, "duration").text = str(L)
                if not full: ET.SubElement(n, "type").text = "quarter"
                if src is not None and not is_mr and not is_rest_only(src):
                    pass
            else:
                for ch in src:
                    if ch.tag == "note":
                        nn = copy.deepcopy(ch)
                        for e in nn.findall("stem"): nn.remove(e)
                        du = nn.find("duration")
                        if du is not None: du.text = str(round(int(du.text) * DIV / sdiv))
                        m.append(nn)
                tot = measure_total(m)
                if tot != L:
                    report.append(f"  bar {bar}: LENGTH {tot / DIV:g} beats, expected {L / DIV:g}")
            prev_meter, prev_key = spec.meter(bar), spec.key_fifths(bar)
            out.append(m)
    return out


def main(argv):
    if len(argv) != 2: print(__doc__); return 2
    spec = load_spec(argv[0])
    report, parts = [], {}
    score = ET.Element("score-partwise", version="3.1")
    ET.SubElement(ET.SubElement(score, "work"), "work-title").text = spec.TITLE
    if spec.COMPOSER:
        ET.SubElement(ET.SubElement(score, "identification"), "creator", type="composer").text = spec.COMPOSER
    pl = ET.SubElement(score, "part-list")
    for pid, P in spec.PARTS.items():
        sp = ET.SubElement(pl, "score-part", id=f"P{pid}")
        ET.SubElement(sp, "part-name").text = P["name"]
        ET.SubElement(sp, "part-abbreviation").text = P["abbr"]
        mi = ET.SubElement(sp, "midi-instrument", id=f"P{pid}-I1")
        ET.SubElement(mi, "midi-channel").text = str(pid)
        ET.SubElement(mi, "midi-program").text = str(P["program"])
        report.append(f"part {pid} ({P['name']}):")
        parts[pid] = build_part(spec, pid, report)
    for pid, ms in parts.items():
        pe = ET.SubElement(score, "part", id=f"P{pid}")
        marks = spec.MARKS.get(pid, {})
        for m in ms:
            idx = 1 if m.find("attributes") is not None else 0
            for kind, *a in marks.get(int(m.get("number")), []):
                m.insert(idx, direction(kind, *a)); idx += 1
            pe.append(m)
        bl = ET.SubElement(ms[-1], "barline", location="right")
        ET.SubElement(bl, "bar-style").text = "light-heavy"
    ET.indent(score, space="  ")
    out = Path(argv[1]); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
                   '"http://www.musicxml.org/dtds/partwise.dtd">\n' + ET.tostring(score, encoding="unicode") + "\n")
    rp = out.with_suffix(".report.txt"); rp.write_text("\n".join(report) + "\n")
    print("\n".join(report)); print(f"\nwrote {out} ({len(parts)} parts x {spec.TOTAL_BARS} bars); report {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
