"""Stage 6 (between normalize and validate, optional): transpose written pitch
to concert pitch (or any shift) via ``musicocr.transpose``.

Off by default (``[stages.transpose] enabled``). Shift comes from, in order:
``semitones`` (all parts), ``instrument`` (looked up in
``musicocr/instruments.py``), or neither -- each part is identified by its
``<part-name>``. Typical OMR'd single parts have a generic name, so for those
set ``instrument``.

Re-runnable: the untransposed file is kept under ``<workdir>/written/`` and every
run transposes from that copy, so ``--from transpose`` never stacks shifts. A
fresh extract/normalize rewrite of the master is detected (mtime stamp) and
becomes the new "written" copy.

Note the qa stage's render will be at the new pitch, so it will not visually
match the written-pitch scan.
"""
from __future__ import annotations

import shutil

from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext
from musicocr.transpose import transpose_file


def run(ctx: PipelineContext) -> StageResult:
    cfg = ctx.config.stages.transpose
    if not cfg.enabled:
        return StageResult("transpose", "skipped", "disabled ([stages.transpose] enabled = false)")

    xml = ctx.artifacts.musicxml or (ctx.workdir / f"{ctx.book_name}.musicxml")
    if not xml.exists():
        raise StageError(
            f"no .musicxml at {xml.name}; run the extract stage first (e.g. --from extract)"
        )
    if ctx.artifacts.musicxml is None:
        ctx.artifacts.musicxml = xml

    targets = [xml]
    pages = ctx.artifacts.musicxml_pages or []
    if not ctx.artifacts.extract_merged and len(pages) > 1:
        targets = list(pages)          # convert/qa use the per-page files in that case

    stamps = ctx.artifacts.transpose_stamps
    backup_dir = ctx.workdir / "written"
    backup_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    skipped = []
    for path in targets:
        backup = backup_dir / path.name
        already = stamps.get(str(path)) == path.stat().st_mtime_ns
        if not (already and backup.exists()):
            shutil.copy2(path, backup)
        res = transpose_file(
            backup, path,
            semitones=cfg.semitones,
            instrument=cfg.instrument or None,
            clef=cfg.clef or None,
            log=lambda s, n=path.name: ctx.log(f"  {n}: {s}"),
        )
        stamps[str(path)] = path.stat().st_mtime_ns
        total += len(res.parts)
        skipped += res.skipped

    if not total:
        raise StageError(
            "transpose is enabled but no part could be transposed"
            + (": " + "; ".join(skipped) if skipped else "")
            + ". Set [stages.transpose] instrument or semitones (or --transpose-instrument)."
        )
    detail = f"{total} part(s) transposed"
    if skipped:
        detail += f"; {len(skipped)} skipped (unrecognised instrument)"
    return StageResult("transpose", "ok", detail)
