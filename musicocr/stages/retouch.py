"""Stage 5b (between normalize and transpose, optional): edition-specific touch-ups via
``musicocr.retouch`` -- old-notation bass clefs, and the piece's title and composer.

Off unless ``--old-bass-clef``, ``--title`` or ``--composer`` is given (or ``[stages.retouch]`` sets
them). Re-runnable like transpose: the untouched file is kept under ``<workdir>/pre-retouch/`` and
every run starts from that copy.
"""
from __future__ import annotations

import shutil

from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext
from musicocr.retouch import RetouchOptions, retouch_file
from musicocr.stages._common import disabled, require_musicxml


def run(ctx: PipelineContext) -> StageResult:
    cfg = ctx.config.stages.retouch
    if not cfg.active:
        return disabled("retouch", "retouch")

    xml = require_musicxml(ctx)
    targets = [xml]
    pages = ctx.artifacts.musicxml_pages or []
    if not ctx.artifacts.extract_merged and len(pages) > 1:
        targets = list(pages)

    stamps = ctx.artifacts.retouch_stamps
    backup_dir = ctx.workdir / "pre-retouch"
    backup_dir.mkdir(parents=True, exist_ok=True)
    moved = dropped = 0
    for path in targets:
        backup = backup_dir / path.name
        if not (stamps.get(str(path)) == path.stat().st_mtime_ns and backup.exists()):
            shutil.copy2(path, backup)
        res = retouch_file(backup, path, cfg, log=lambda s, n=path.name: ctx.log(f"  {n}: {s}"))
        stamps[str(path)] = path.stat().st_mtime_ns
        moved += res["octavized"]
        dropped += res["dropped"]
    bits = []
    if cfg.old_bass_clef:
        bits.append(f"{moved} note(s) under old-notation bass clefs raised an octave")
    if cfg.title or cfg.composer:
        bits.append(f"title/composer set, {dropped} misread direction(s) dropped")
    return StageResult("retouch", "ok", "; ".join(bits))
