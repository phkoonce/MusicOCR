"""``musicocr combine``: build one aligned score from a batch's per-part results."""
from __future__ import annotations

import re
from pathlib import Path

from musicocr.alignment.build import write_score
from musicocr.batch import BatchSpec
from musicocr.combine import PartInput, combine


def part_inputs(spec: BatchSpec, engine: str | None, only=None) -> list[PartInput]:
    """The scoring parts of ``spec`` with their per-page MusicXML files for ``engine``."""
    out = []
    for part in spec.parts:
        if not part.score or (only and part.id not in only and spec.key(part) not in only):
            continue
        key = spec.key(part)
        d = spec.output_dir / key
        if spec.engines or engine:
            d = d / "engines" / (engine or spec.engines[0])
        pages = sorted((p for p in d.glob(f"{key}.p[0-9][0-9][0-9].musicxml")),
                       key=lambda p: int(re.search(r"\.p(\d{3})\.musicxml$", p.name).group(1)))
        pdf = spec.input_dir / f"{key}.pdf"
        out.append(PartInput(part.id, part.label, part.instrument, pdf if pdf.exists() else None, pages))
    return out


def run(spec: BatchSpec, engine: str | None, out_dir: Path, *, only=None, pdftotext: str = "pdftotext",
        musescore: str | None = None, log=print) -> Path:
    parts = [p for p in part_inputs(spec, engine, only)]
    missing = [p.pid for p in parts if not p.pages]
    if missing:
        raise SystemExit(f"no per-page MusicXML for part(s) {missing} under {spec.output_dir} "
                         f"(engine {engine!r}); run the batch first")
    score, report, summary = combine(parts, spec.title, pdftotext=pdftotext)
    xml = write_score(score, out_dir / f"{spec.name}_combined.musicxml")
    (out_dir / f"{spec.name}_combined.report.txt").write_text("\n".join(report) + "\n")
    log("\n".join(report))
    log(f"\nwrote {xml} ({summary['parts']} parts x {summary['total']} bars; "
        f"{summary['bad_bars']} bar(s) with a wrong length; {summary['repairs']} page repair(s))")
    if musescore:
        from musicocr.util import run_cmd_lenient
        mscz = xml.with_suffix(".mscz")
        run_cmd_lenient([musescore, "-f", "-o", str(mscz), str(xml)], timeout=600, log=log)
        log(f"MuseScore: {mscz} ({'ok' if mscz.exists() else 'FAILED'})")
    return xml
