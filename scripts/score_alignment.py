#!/usr/bin/env python
"""Cache-only alignment scorecard for combined scores (no OMR is ever run).

    .venv/bin/python scripts/score_alignment.py batches/gb-all.toml [--pieces 1-3] [--name gb-all]
    .venv/bin/python scripts/score_alignment.py output/zerberus            # an output dir
    .venv/bin/python scripts/score_alignment.py batches/gb-rerun2.toml --ref-dir output/refs
    .venv/bin/python scripts/score_alignment.py batches/zerberus.toml --recombine /tmp/scratch/zerberus
    .venv/bin/python scripts/score_alignment.py --compare output/scorecards/a.json output/scorecards/b.json

What it reads (per piece, or per book for single-score batches):
  * each part's own MusicXML (`output/<batch>/<key>/<key>.musicxml`): bars read (all-rest measures that span
    several bars count as that many);
  * the combined score (`<output>/_combined/**/*_combined.musicxml`) and its `*.report.txt`, parsed into numbers:
    inserted / dropped bars, "-- check" flags, wrong-length bars, parts aligned by rhythm / rest pattern / systems,
    padded/truncated parts, rehearsal marks agreed (and the stretches they cut), upbeat/lower-bound notes;
  * "parts differing" = parts whose own bar count differs from the combined total.
Reference (hand-corrected MusicXML, see output/refs/): bar-level alignment of each part's own bars and of the
combined part against the reference part: bars are matched (LCS) by rhythm + within-bar pitch-interval signature
(transposition/octave-invariant); reports matched / extra (candidate only) / missing (reference only) bars.
Refs are found by rule (RULES below) or given with --ref FILE (single score) / --ref-dir DIR.

Output: a compact table on stdout and JSON in output/scorecards/<name>.json (`--compare` diffs two such files).
`--recombine DIR` first re-runs `musicocr combine` (cheap, no OMR, no MuseScore) into DIR and scores that.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# batch-stem regex -> reference file for a piece number (None = the whole book); relative to --ref-dir
RULES = [
    (r"^(stage-)?gb-", lambda piece: f"gb_p{piece:02d}.ref.musicxml" if piece else None),
    (r"^(stage-)?zerberus$", lambda piece: "zerberus_new_ref.musicxml"),
    (r"^(stage-)?szeged-frozen$", lambda piece: "szeged_frozen_misc.ref.musicxml"),
]


# ---------------------------------------------------------------- MusicXML -> bar signatures
def read_score(path: Path, barlen0: float | None = None) -> list[dict]:
    """[{name, rhythm: [tuple], strict: [tuple], rest: [bool], barlen}] per part; multi-bar rest measures expanded.
    ``barlen0``: the bar length (quarters) in force before the file starts (a page file that declares no meter)."""
    root = ET.parse(path).getroot()
    names = {p.get("id"): (p.findtext("part-name") or "") for p in root.iter("score-part")}
    out = []
    for part in root.findall("part"):
        divisions, barlen = 1, barlen0
        rhythm, strict, rest = [], [], []
        for m in part.findall("measure"):
            voice, events, prev, mrest = None, [], None, 0
            for el in m:
                if el.tag == "attributes":
                    d = el.findtext("divisions")
                    if d:
                        divisions = float(d)
                    t = el.find("time")
                    if t is not None and t.findtext("beats", "").isdigit() and t.findtext("beat-type", "").isdigit():
                        barlen = int(t.findtext("beats")) * 4 / int(t.findtext("beat-type"))
                    mr = el.find("measure-style/multiple-rest")
                    if mr is not None and (mr.text or "").strip().isdigit():
                        mrest = int(mr.text)
                elif el.tag == "note":
                    if el.find("grace") is not None or el.find("chord") is not None:
                        continue
                    v = el.findtext("voice", "1")
                    voice = voice or v
                    if v != voice:
                        continue
                    dur = round(float(el.findtext("duration", "0")) / divisions, 3)
                    if el.find("rest") is not None:
                        events.append((dur, "r"))
                        continue
                    p = el.find("pitch")
                    if p is None:       # unpitched percussion: rhythm only
                        events.append((dur, "u"))
                        continue
                    midi = 12 * int(p.findtext("octave", "4")) + STEP.get(p.findtext("step", "C"), 0) + int(float(p.findtext("alter", "0")))
                    events.append((dur, "S" if prev is None else (midi - prev) % 12))
                    prev = midi
            total = round(sum(e[0] for e in events), 3)
            allrest = all(e[1] == "r" for e in events)
            n = 1
            if allrest and mrest > 1:
                n = mrest
            elif allrest and barlen and total > barlen * 1.5 and abs(total / barlen - round(total / barlen)) < 0.05:
                n = int(round(total / barlen))
            for _ in range(n):
                dur_t = tuple(e[0] for e in events) if n == 1 else (barlen,)
                rhythm.append(dur_t)
                strict.append((dur_t, tuple(e[1] for e in events) if n == 1 else ("r",)))
                rest.append(allrest)
        out.append({"name": names.get(part.get("id"), ""), "rhythm": rhythm, "strict": strict, "rest": rest, "barlen": barlen})
    return out


def read_raw_part(out: Path, key: str) -> dict | None:
    """A part's own bars. The merged `<key>.musicxml` keeps only some `multiple-rest` tags, so the per-page
    files (`<key>.pNNN.musicxml`, which `combine` reads) are used when there are any: multi-rest bars count as
    their printed length."""
    pages = sorted((out / key).glob(f"{key}.p[0-9][0-9][0-9].musicxml"))
    if pages:
        acc, barlen = None, None
        for f in pages:
            try:
                sc = read_score(f, barlen)
            except ET.ParseError:
                continue
            if not sc:
                continue
            barlen = sc[0]["barlen"] or barlen
            if acc is None:
                acc = {k: list(sc[0][k]) if isinstance(sc[0][k], list) else sc[0][k] for k in sc[0]}
            else:
                for k in ("rhythm", "strict", "rest"):
                    acc[k] += sc[0][k]
        if acc:
            return acc
    merged = out / key / f"{key}.musicxml"
    return (read_score(merged) or [None])[0] if merged.exists() else None


def lcs(a: list, b: list) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence."""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        Li, Ln = L[i], L[i + 1]
        for j in range(m - 1, -1, -1):
            Li[j] = Ln[j + 1] + 1 if a[i] == b[j] else max(Ln[j], Li[j + 1])
    i = j = 0
    pairs = []
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j)); i += 1; j += 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def bar_match(cand: dict, ref: dict) -> dict:
    """Bar-level alignment of a candidate part against a reference part."""
    pairs = lcs(cand["strict"], ref["strict"])
    rp = lcs(cand["rhythm"], ref["rhythm"])
    nr = sum(1 for i, _ in pairs if not cand["rest"][i])
    return {"cand": len(cand["strict"]), "ref": len(ref["strict"]), "matched": len(pairs),
            "extra": len(cand["strict"]) - len(pairs), "missing": len(ref["strict"]) - len(pairs),
            "matched_notes": nr, "rhythm_matched": len(rp)}


def part_key(name: str):
    w = re.findall(r"[a-zA-Z]+", name.lower())
    fam = next((x for x in w if x not in ("in", "f", "es", "b", "bb")), "")
    fam = {"pauken": "timpani"}.get(fam, fam)
    num = re.findall(r"\d+", name)
    return fam, num[-1] if num else ""


# ---------------------------------------------------------------- report parsing
def _count_list(s: str) -> int:
    s = s.strip()
    return len([x for x in s.split(",") if x.strip()]) if s else 0


def parse_report(text: str) -> dict:
    r = dict(parts=None, bars=None, inserted=0, dropped=0, flagged_check=0, check_text=len(re.findall(r"check:", text)),
             wrong_length=len(re.findall(r"LENGTH", text)), by_rhythm=0, by_rest_pattern=0, within_systems=0,
             by_marks=0, padded_end=0, padded_bars=0, split_multibar=0, read_total=0, printed_total=0,
             marks_agreed=0, mark_stretches=0, no_lower_bound=0, last_page_totals={})
    ms = re.findall(r"score: (\d+) parts, (\d+) bars \(last-page totals ({[^}]*})", text)   # scene books: one per scene
    if ms:
        r["parts"], r["bars"] = int(ms[0][0]), sum(int(m[1]) for m in ms)
        for m in ms:
            for k, v in re.findall(r"(\d+): (\d+)", m[2]):
                r["last_page_totals"][int(k)] = r["last_page_totals"].get(int(k), 0) + int(v)
        r["scenes"] = len(ms)
    for line in text.splitlines():
        if "bars read for" in line:
            a = re.search(r"(\d+) bars read for (\d+)", line)
            r["read_total"] += int(a.group(1)); r["printed_total"] += int(a.group(2))
            r["inserted"] += _count_list(next(iter(re.findall(r"inserted rest bar\(s\) at \[([^\]]*)\]", line)), ""))
            r["dropped"] += _count_list(next(iter(re.findall(r"dropped source bar\(s\) \[([^\]]*)\]", line)), ""))
            if line.rstrip().lower().endswith("-- check"):
                r["flagged_check"] += 1
            if "padded/truncated" in line:
                r["padded_end"] += 1
            elif "rest pattern" in line:
                r["by_rest_pattern"] += 1
            elif "rhythm" in line and "between the rehearsal" not in line:
                r["by_rhythm"] += 1
            elif "within its systems" in line:
                r["within_systems"] += 1
            elif "between the rehearsal marks" in line:
                r["by_marks"] += 1
        a = re.search(r"ends (\d+) bar\(s\) early -> padded", line)
        if a:
            r["padded_bars"] += int(a.group(1))
        if "-- check" not in line and "a" in line and re.search(r"\d-bar-long measure read as one", line):
            r["split_multibar"] += 1
        a = re.search(r"score: (\d+) rehearsal marks .*cut the piece into (\d+) stretches", line)
        if a:
            r["marks_agreed"], r["mark_stretches"] = int(a.group(1)), int(a.group(2))
        if "no part reaches the printed lower bound" in line:
            r["no_lower_bound"] += 1
    r["net_read_minus_printed"] = r["read_total"] - r["printed_total"]
    return r


# ---------------------------------------------------------------- locating things
def load_batch(path: Path) -> dict:
    data = tomllib.loads(path.read_text())
    name = path.stem
    out = ROOT / data.get("output_dir", f"output/{name}")
    parts = [{"id": p["id"], "key": data.get("key_prefix", "") + p["id"], "label": p.get("label", p["id"])} for p in data["parts"]]
    return {"name": name, "out": out, "parts": parts, "bar_numbers": data.get("bar_numbers", True)}


def load_outdir(out: Path) -> dict:
    keys = sorted(d.name for d in out.iterdir() if d.is_dir() and not d.name.startswith("_") and (d / f"{d.name}.musicxml").exists())
    parts = [{"id": re.sub(r"^[A-Za-z0-9]+_", "", k, count=1), "key": k, "label": k} for k in keys]
    return {"name": out.name, "out": out, "parts": parts, "bar_numbers": True}


def find_combined(base: Path) -> dict[str, Path]:
    """{group: dir} where group is pNN for collection pieces or 'book'."""
    res = {}
    for x in sorted(base.rglob("*_combined.musicxml")) + sorted(base.rglob("*_scenes.musicxml")):
        if ".pre" in x.name:
            continue
        g = next((p for p in x.parent.relative_to(base).parts if re.fullmatch(r"p\d+", p)), None)
        if g is None:
            g = "book" if x.parent == base or len(res) == 0 else x.parent.name
        res.setdefault(g, x)
    return res


def find_ref(stem: str, group: str, ref_dir: Path, ref_file: Path | None) -> Path | None:
    if ref_file:
        return ref_file
    piece = int(group[1:]) if re.fullmatch(r"p\d+", group) else None
    for pat, fn in RULES:
        if re.search(pat, stem):
            f = fn(piece)
            if f and (ref_dir / f).exists():
                return ref_dir / f
    return None


def piece_range(s: str | None) -> set[int] | None:
    if not s:
        return None
    a, _, b = s.partition("-")
    return set(range(int(a), int(b or a) + 1))


# ---------------------------------------------------------------- scoring
def score_group(group, cpath, spec, ref_path, cache):
    out = spec["out"]
    members = [p for p in spec["parts"] if group == "book" or p["id"].startswith(group + "_")]
    comb = read_score(cpath)
    rep = cpath.parent / (cpath.stem + ".report.txt")
    rep = rep if rep.exists() else next(iter(cpath.parent.glob("*.report.txt")), None)
    log = parse_report(rep.read_text()) if rep else parse_report("")
    ref = None
    if ref_path:
        ref = cache.setdefault(ref_path, read_score(ref_path))
    ref_by = {part_key(p["name"]): p for p in ref} if ref else {}
    if len(members) != len(comb):   # combine dropped/added parts: match by name
        by_name = {part_key(c["name"]): c for c in comb}
        members = [m for m in members if part_key(m["label"].split(" - ")[-1]) in by_name]
    rows = []
    for i, mem in enumerate(members):
        c = comb[i] if len(members) == len(comb) else by_name[part_key(mem["label"].split(" - ")[-1])]
        raw = read_raw_part(out, mem["key"])
        row = {"id": mem["id"], "raw_bars": len(raw["strict"]) if raw else None, "combined_bars": len(c["strict"])}
        rp = ref_by.get(part_key(c["name"])) if ref else None
        if ref and rp is None and len(ref) == len(comb):
            rp = ref[i]
        if rp is not None:
            row["ref_bars"] = len(rp["strict"])
            row["combined_vs_ref"] = bar_match(c, rp)
            if raw:
                row["raw_vs_ref"] = bar_match(raw, rp)
        rows.append(row)
    nb = len(comb[0]["strict"]) if comb else 0
    d = {"group": group, "combined_bars": nb, "n_parts": len(rows), "parts": rows,
         "parts_differing": sum(1 for r in rows if r["raw_bars"] is not None and r["raw_bars"] != nb),
         "parts_missing_output": sum(1 for r in rows if r["raw_bars"] is None),
         "log": log, "ref": str(ref_path) if ref_path else None}
    if ref_path:
        refn = [len(p["strict"]) for p in ref]
        d["ref_bars"] = max(refn) if refn else 0
        d["combined_vs_ref"] = {k: sum(r["combined_vs_ref"][k] for r in rows if "combined_vs_ref" in r) for k in ("matched", "extra", "missing", "matched_notes", "rhythm_matched")}
        d["raw_vs_ref"] = {k: sum(r["raw_vs_ref"][k] for r in rows if "raw_vs_ref" in r) for k in ("matched", "extra", "missing", "matched_notes", "rhythm_matched")}
    return d


def recombine(spec, batch_path: Path, scratch: Path, pieces: set[int] | None):
    grouped = {}
    for p in spec["parts"]:
        m = re.match(r"(p\d+)_", p["id"])
        grouped.setdefault(m.group(1) if m else "book", []).append(p["id"])
    for g, ids in grouped.items():
        if g != "book" and pieces and int(g[1:]) not in pieces:
            continue
        d = scratch / g if g != "book" else scratch
        d.mkdir(parents=True, exist_ok=True)
        cmd = [str(ROOT / "run.sh"), "combine", "--batch", str(batch_path), "--output", str(d)]
        if g != "book":
            cmd += ["--only", ",".join(ids)]
        cmd += ["--normalize-marks", "dedupe,reach,placement,lines"]
        if not spec["bar_numbers"]:
            cmd.append("--no-bar-numbers")
        r = subprocess.run(cmd, capture_output=True, text=True)
        (d / "combine.log").write_text(r.stdout + r.stderr)
        print(f"recombined {g}: rc={r.returncode}", file=sys.stderr)


# ---------------------------------------------------------------- output
TOT = ["combined_bars", "parts_differing", "inserted", "dropped", "wrong_length", "flagged_check", "by_rhythm", "by_rest_pattern",
       "within_systems", "by_marks", "padded_end", "padded_bars", "split_multibar", "marks_agreed", "no_lower_bound"]


def totals(groups: list[dict]) -> dict:
    t = {k: 0 for k in TOT}
    for g in groups:
        t["combined_bars"] += g["combined_bars"]
        t["parts_differing"] += g["parts_differing"]
        for k in TOT[2:]:
            t[k] += g["log"].get(k, 0)
    t["pieces"] = len(groups)
    t["parts"] = sum(g["n_parts"] for g in groups)
    t["pieces_with_differing_parts"] = sum(1 for g in groups if g["parts_differing"])
    t["abs_raw_vs_combined"] = sum(abs((p["raw_bars"] or g["combined_bars"]) - g["combined_bars"]) for g in groups for p in g["parts"])
    refs = [g for g in groups if "combined_vs_ref" in g]
    if refs:
        t["ref_pieces"] = len(refs)
        t["ref_bars"] = sum(g["ref_bars"] for g in refs)
        t["ref_combined_bars"] = sum(g["combined_bars"] for g in refs)
        for k in ("matched", "extra", "missing"):
            t[f"combined_vs_ref_{k}"] = sum(g["combined_vs_ref"][k] for g in refs)
            t[f"raw_vs_ref_{k}"] = sum(g["raw_vs_ref"][k] for g in refs)
    return t


def print_table(name, groups, rows_limit):
    hdr = f"{'grp':5}{'comb':>5} {'raw bars per part':22}{'diff':>5}{'ins':>5}{'drop':>5}{'chk':>5}{'len':>5}{'rhy':>4}{'rest':>5}{'pad':>4}{'marks':>6}  vs ref (match/extra/miss; raw->)"
    print(f"== {name}\n{hdr}")

    def key(g):
        return -(g["parts_differing"] * 3 + g["log"]["inserted"] + g["log"]["dropped"] + g["log"]["wrong_length"])
    show = groups if len(groups) <= rows_limit else sorted(groups, key=key)[:rows_limit]
    for g in sorted(show, key=lambda g: g["group"]):
        raws = ",".join(str(p["raw_bars"] if p["raw_bars"] is not None else "-") for p in g["parts"])
        raws = raws if len(raws) <= 22 else raws[:19] + "..."
        L = g["log"]
        ref = ""
        if "combined_vs_ref" in g:
            c, w = g["combined_vs_ref"], g["raw_vs_ref"]
            ref = f"ref {g['ref_bars']}b: {c['matched']}/{c['extra']}/{c['missing']}  raw {w['matched']}/{w['extra']}/{w['missing']}"
        print(f"{g['group']:5}{g['combined_bars']:>5} {raws:22}{g['parts_differing']:>5}{L['inserted']:>5}{L['dropped']:>5}"
              f"{L['flagged_check']:>5}{L['wrong_length']:>5}{L['by_rhythm']:>4}{L['by_rest_pattern']:>5}{L['padded_end'] + (1 if L['padded_bars'] else 0):>4}{L['marks_agreed']:>6}  {ref}")
    if len(show) < len(groups):
        print(f"... {len(groups) - len(show)} more pieces (worst {len(show)} shown; all in the JSON)")
    t = totals(groups)
    print("TOTAL " + " ".join(f"{k}={v}" for k, v in t.items()))


def compare(a_path, b_path):
    a, b = json.loads(Path(a_path).read_text()), json.loads(Path(b_path).read_text())
    print(f"compare {a_path} -> {b_path}")
    for k in sorted(set(a["totals"]) | set(b["totals"])):
        x, y = a["totals"].get(k), b["totals"].get(k)
        if x != y:
            print(f"  {k}: {x} -> {y}" + (f" ({y - x:+})" if isinstance(x, int) and isinstance(y, int) else ""))
    ga = {g["group"]: g for g in a["groups"]}
    gb = {g["group"]: g for g in b["groups"]}

    def metrics(g):
        L = g["log"]
        m = {"bars": g["combined_bars"], "diff": g["parts_differing"], "ins": L["inserted"], "drop": L["dropped"], "len": L["wrong_length"]}
        if "combined_vs_ref" in g:
            m.update(extra=g["combined_vs_ref"]["extra"], miss=g["combined_vs_ref"]["missing"])
        return m
    n = 0
    for grp in sorted(set(ga) | set(gb)):
        if grp not in ga or grp not in gb:
            print(f"  {grp}: only in {'new' if grp in gb else 'old'}")
            continue
        x, y = metrics(ga[grp]), metrics(gb[grp])
        if x != y:
            n += 1
            print(f"  {grp}: " + ", ".join(f"{k} {x.get(k)}->{y.get(k)}" for k in y if x.get(k) != y.get(k)))
    print(f"  {n} group(s) changed of {len(set(ga) & set(gb))} common")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="batches/NAME.toml or an output dir")
    ap.add_argument("--name", help="scorecard name (default: batch stem or dir name)")
    ap.add_argument("--combined-dir", help="where the combined scores are (default <output>/_combined)")
    ap.add_argument("--recombine", metavar="DIR", help="run `musicocr combine` into DIR first, then score DIR")
    ap.add_argument("--pieces", help="N[-M]: only these pieces of a collection")
    ap.add_argument("--ref", help="reference MusicXML for a single-score batch")
    ap.add_argument("--ref-dir", default=str(ROOT / "output" / "refs"))
    ap.add_argument("--no-ref", action="store_true")
    ap.add_argument("--batch", help="batch toml when target is an output dir (for part ids)")
    ap.add_argument("--rows", type=int, default=25, help="max rows in the table (worst first)")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"))
    a = ap.parse_args()
    if a.compare:
        return compare(*a.compare)
    t = Path(a.target)
    bpath = Path(a.batch) if a.batch else (t if t.suffix == ".toml" else None)
    spec = load_batch(bpath) if bpath else load_outdir(t)
    if t.suffix != ".toml":
        spec["out"] = t
    name = a.name or spec["name"]
    base = Path(a.recombine) if a.recombine else Path(a.combined_dir) if a.combined_dir else spec["out"] / "_combined"
    pieces = piece_range(a.pieces)
    if a.recombine:
        if not bpath:
            sys.exit("--recombine needs a batch toml")
        recombine(spec, bpath, base, pieces)
    combined = find_combined(base)
    cache, groups = {}, []
    for grp, cpath in combined.items():
        if pieces and grp.startswith("p") and int(grp[1:]) not in pieces:
            continue
        ref = None if a.no_ref else find_ref(spec["name"], grp, Path(a.ref_dir), Path(a.ref) if a.ref else None)
        groups.append(score_group(grp, cpath, spec, ref, cache))
    if not groups:
        sys.exit(f"no combined scores under {base}")
    print_table(name, groups, a.rows)
    out = ROOT / "output" / "scorecards" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"name": name, "source": str(base), "totals": totals(groups), "groups": groups}, indent=1))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
