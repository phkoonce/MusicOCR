"""Combine every piece of a collection and tabulate how each one went."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

from musicocr.collection.spec import CollectionSpec, piece_range


class Row(NamedTuple):
    piece: int
    bars: str
    lengths: str          # the parts' bar counts before page repair, e.g. "{95: 3, 89: 1}"
    repaired: int         # parts whose page had to be re-aligned to the printed bars
    bad_bars: int         # bars whose length is wrong after combining
    scored: bool          # the .mscz exists


def parse_report(text: str) -> tuple:
    """``(bars, lengths, repaired, bad_bars)`` from a combine report."""
    m = re.search(r"score: \d+ parts, (\d+) bars \(last-page totals ({[^}]*})", text)
    return (m.group(1) if m else "?", m.group(2) if m else "",
            len(re.findall(r"(\d+) bars read for (\d+) printed", text)), len(re.findall(r"LENGTH", text)))


def piece_row(spec: CollectionSpec, num: int, redo: bool = False,
              run: Optional[Callable[[List[str]], "subprocess.CompletedProcess"]] = None) -> Row:
    """Combine piece ``num`` (unless its score exists and ``redo`` is off) and read its report."""
    d = spec.combined_dir / f"p{num:02d}"
    mscz = d / f"{spec.name}_combined.mscz"
    if redo or not mscz.exists():
        only = ",".join(spec.part_id(num, b) for b in spec.books)
        cmd = [sys.executable, "-m", "musicocr", "combine", "--batch", str(spec.batch_path),
               "--only", only, "--output", str(d), "--convert"]
        if not spec.bar_numbers:
            cmd.append("--no-bar-numbers")
        r = (run or (lambda c: subprocess.run(c, capture_output=True, text=True)))(cmd)
        if d.exists():
            (d / "combine.log").write_text(r.stdout + r.stderr)
    report = d / f"{spec.name}_combined.report.txt"
    bars, lengths, repaired, bad = parse_report(report.read_text() if report.exists() else "")
    return Row(num, bars, lengths, repaired, bad, mscz.exists())


def summary_markdown(spec: CollectionSpec, rows: List[Row]) -> str:
    lines = [f"# {spec.title} -- combine summary", "",
             "| piece | bars | part lengths before | parts repaired | bad-length bars | .mscz |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r.piece} | {r.bars} | {r.lengths} | {r.repaired} | {r.bad_bars} | {'yes' if r.scored else 'NO'} |"
              for r in rows]
    return "\n".join(lines) + "\n"


def combine_all(spec: CollectionSpec, pieces: Optional[str] = None, redo: bool = False,
                summary: Optional[Path] = None, echo: Callable[[str], None] = print) -> List[Row]:
    """Combine the chosen pieces (default: all). The summary table goes to ``summary``, by default
    ``summary.md`` next to the spec when every piece was run (a subset would overwrite the full table)."""
    count = len(json.loads(spec.pieces_json.read_text())["entries"])
    rows = []
    for n in piece_range(pieces, count):
        rows.append(piece_row(spec, n, redo))
        echo(str(tuple(rows[-1])))
    target = summary or (None if pieces else spec.path.parent / "summary.md")
    if target:
        target.write_text(summary_markdown(spec, rows))
        echo(f"summary: {target}")
    else:
        echo(summary_markdown(spec, rows))
    return rows
