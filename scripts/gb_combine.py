#!/usr/bin/env python3
"""Combine the four horns of every GB piece: .venv/bin/python scripts/gb_combine.py [--pieces 1-3] [--redo]

Runs ``musicocr combine --no-bar-numbers`` per piece into output/gb-all/_combined/pNN/ and writes a
summary table (docs/gb-combine-summary.md) from the combine reports.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCH = ROOT / "batches/gb-all.toml"
OUT = ROOT / "output/gb-all/_combined"


def pieces(arg: str | None) -> list[int]:
    if not arg:
        return list(range(1, 89))
    a, _, b = arg.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pieces")
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()
    rows = []
    for n in pieces(args.pieces):
        d = OUT / f"p{n:02d}"
        mscz = d / "gb-all_combined.mscz"
        if args.redo or not mscz.exists():
            only = ",".join(f"p{n:02d}_h{h}" for h in (1, 2, 3, 4))
            r = subprocess.run([str(ROOT / "run.sh"), "combine", "--batch", str(BATCH), "--no-bar-numbers",
                                "--only", only, "--output", str(d), "--convert"], capture_output=True, text=True)
            (d / "combine.log").write_text(r.stdout + r.stderr) if d.exists() else None
        rep = d / "gb-all_combined.report.txt"
        text = rep.read_text() if rep.exists() else ""
        m = re.search(r"score: \d+ parts, (\d+) bars \(last-page totals ({[^}]*})", text)
        lens = re.findall(r"(\d+) bars read for (\d+) printed", text)
        rows.append((n, m.group(1) if m else "?", m.group(2) if m else "", len(lens),
                     len(re.findall(r"LENGTH", text)), mscz.exists()))
        print(rows[-1], flush=True)
    lines = ["# GB combine summary", "", "| piece | bars | horn lengths before | horns repaired | bad-length bars | .mscz |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {n} | {b} | {t} | {rep} | {bad} | {'yes' if ok else 'NO'} |" for n, b, t, rep, bad, ok in rows]
    (ROOT / "docs/gb-combine-summary.md").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
