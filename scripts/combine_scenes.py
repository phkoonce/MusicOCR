#!/usr/bin/env python
"""Combine a batch of scene-structured parts (film-score cues) into one score; see musicocr/scenes.py.

  .venv/bin/python scripts/combine_scenes.py --batch batches/httyd2.toml [--cues batches/httyd2.cues.json]
         [--engine copisteria] [--only 1M1,4M30] [--detect] [--convert]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicocr.alignment.build import write_score          # noqa: E402
from musicocr.batch import load_spec                       # noqa: E402
from musicocr.combine import (PartInput, combine, printed_last_system_start, printed_max_bar,   # noqa: E402
                              printed_multirest_spans, printed_page_starts, read_page)
from musicocr.combine_cli import part_inputs               # noqa: E402
from musicocr.scenes import (concat_scenes, detect_scenes, drop_pickup, load_scenes, pickup_of, prepend_pickup,   # noqa: E402
                             save_scenes, slice_pdf)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True, type=Path)
    ap.add_argument("--cues", type=Path, help="JSON [[label, title], ...] in print order (canonical names)")
    ap.add_argument("--engine")
    ap.add_argument("--only", help="comma-separated scene labels to combine")
    ap.add_argument("--detect", action="store_true", help="redo the scene detection")
    ap.add_argument("--convert", action="store_true", help="also write .mscz via MuseScore")
    args = ap.parse_args()
    spec = load_spec(args.batch)
    out_dir = spec.output_dir / "_combined"
    table_path = spec.output_dir / "scenes.json"
    cues = json.loads(args.cues.read_text()) if args.cues else None
    parts = part_inputs(spec, args.engine)
    if table_path.exists() and not args.detect:
        table = load_scenes(table_path)
    else:
        table = {}
        for p in parts:
            print(f"detecting scenes in {p.pid} ...", flush=True)
            table[p.pid] = detect_scenes(p.pdf, cues)
        save_scenes(table_path, table)
    n = {len(v) for v in table.values()}
    if len(n) != 1:
        sys.exit(f"parts disagree on the scene count: { {k: len(v) for k, v in table.items()} }")
    only = set(args.only.split(",")) if args.only else None
    scene_scores, kept, report = [], [], []
    for i in range(n.pop()):
        label = table[parts[0].pid][i].label
        if only and label not in only:
            continue
        scene_parts = []
        for p in parts:
            sc = table[p.pid][i]
            pages = [f for f in p.pages
                     if sc.first_page <= int(re.search(r"\.p(\d{3})\.musicxml$", f.name).group(1)) <= sc.last_page]
            pdf = slice_pdf(p.pdf, sc.first_page, sc.last_page, out_dir / "scenes" / f"{i + 1:02d}_{label}_{p.pid}.pdf")
            scene_parts.append(PartInput(p.pid, p.label, p.instrument, pdf, pages))
        report.append(f"=== scene {i + 1}: {label} ===")
        # an upbeat before printed bar 1 is set aside while the parts are aligned, then put back
        firsts = [read_page(sp.pages[0])[0] if sp.pages else None for sp in scene_parts]
        pickups = pickup_of(firsts)
        if pickups:
            first_files = {sp.pages[0]: k for k, sp in enumerate(scene_parts) if sp.pages}
            report.append(f"scene {label}: {sum(x is not None for x in pickups)} of {len(pickups)} parts open with an upbeat "
                          f"-> set aside from the bar alignment")
            read = lambda f, _ff=first_files: drop_pickup(read_page(f)) if f in _ff and pickups[_ff[f]] is not None else read_page(f)
        else:
            read = read_page
        try:
            score, rep, summary = combine(
                scene_parts, f"{spec.title} - {label}", read_pages=read,
                starts_of=lambda pdf: [1] + printed_page_starts(pdf)[1:],      # a scene's first page opens at bar 1
                last_start_of=printed_last_system_start, spans_of=printed_multirest_spans,
                max_bar_of=printed_max_bar)
        except Exception as e:      # noqa: BLE001
            report.append(f"scene {label}: combine FAILED ({type(e).__name__}: {e})")
            print(report[-1], flush=True)
            continue
        if pickups:
            prepend_pickup(score, pickups)
        report += rep
        # The parts print the same box, but each OCR read misses or garbles some: vote on the time, and
        # take the target from the cue list (the next scene) rather than from the OCR'd label.
        times = [m.group(1) for p in parts if (m := re.match(r"(\d{1,2}:\d\d) until", table[p.pid][i].until))]
        scene = table[parts[0].pid][i]
        nxt = table[parts[0].pid][i + 1].label if i + 1 < len(table[parts[0].pid]) else None
        scene.until = f"{max(set(times), key=times.count)} until {nxt}" if times and nxt else ""
        scene_scores.append(score)
        kept.append(scene)
        print(f"{label}: {summary['total']} bars, {summary['bad_bars']} bad, {summary['repairs']} repairs"
              f"{'; ' + scene.until if scene.until else ''}", flush=True)
    score = concat_scenes(scene_scores, kept, spec.title)
    xml = write_score(score, out_dir / f"{spec.name}_scenes.musicxml")
    (out_dir / f"{spec.name}_scenes.report.txt").write_text("\n".join(report) + "\n")
    print(f"wrote {xml}")
    if args.convert:
        import tomllib
        from musicocr.util import run_cmd_lenient
        cfg = tomllib.loads((ROOT / "config.toml").read_text())
        mscz = xml.with_suffix(".mscz")
        run_cmd_lenient([cfg["tools"]["musescore"], "-f", "-o", str(mscz), str(xml)], timeout=900, log=print)
        print(f"MuseScore: {mscz} ({'ok' if mscz.exists() else 'FAILED'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
