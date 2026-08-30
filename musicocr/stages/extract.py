"""Stage 4: turn Audiveris .mxl export(s) into a plain .musicxml master.

An .mxl file is a zip: ``META-INF/container.xml`` points at the score xml.
If Audiveris emitted several movements we keep each as its own .musicxml and
also try to stitch them into one ``<book>.musicxml`` with music21.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from musicocr.pipeline import PipelineContext, StageError, StageResult


def _unzip_mxl(mxl: Path, dest: Path) -> Path:
    with zipfile.ZipFile(mxl) as zf:
        container = zf.read("META-INF/container.xml")
        root = ET.fromstring(container)
        rootfile = root.find(".//{*}rootfile")
        inner = rootfile.get("full-path") if rootfile is not None else None
        if not inner:
            raise StageError(f"{mxl.name}: no rootfile in container.xml")
        data = zf.read(inner)
    dest.write_bytes(data)
    return dest


def _merge(parts: list[Path], dest: Path, log) -> Path | None:
    try:
        from music21 import converter, stream
    except Exception as exc:  # noqa: BLE001
        log(f"  music21 unavailable, skipping merge ({exc})")
        return None
    try:
        merged = stream.Score()
        for i, p in enumerate(parts, 1):
            sc = converter.parse(str(p))
            for el in sc.elements:
                merged.append(el)
            log(f"  merged movement {i}: {p.name}")
        merged.write("musicxml", fp=str(dest))
        return dest
    except Exception as exc:  # noqa: BLE001
        log(f"  merge failed, keeping movements separate ({exc})")
        return None


def run(ctx: PipelineContext) -> StageResult:
    mxl_files = ctx.artifacts.get("mxl_files")
    if not mxl_files:
        mxl_files = sorted(ctx.workdir.rglob("*.mxl"))
    if not mxl_files:
        raise StageError("no .mxl files found; run the omr stage first")

    out_dir = ctx.workdir
    master = out_dir / f"{ctx.book_name}.musicxml"

    if len(mxl_files) == 1:
        _unzip_mxl(mxl_files[0], master)
        ctx.log(f"  extracted {master.name}")
        ctx.artifacts["musicxml"] = master
        ctx.artifacts["musicxml_movements"] = []
        return StageResult("extract", "ok", master.name)

    produced: list[Path] = []
    for i, mxl in enumerate(mxl_files, 1):
        target = out_dir / f"{ctx.book_name}.mvt{i}.musicxml"
        _unzip_mxl(mxl, target)
        produced.append(target)
        ctx.log(f"  extracted {target.name}")

    merged = _merge(produced, master, ctx.log)
    ctx.artifacts["musicxml"] = merged or produced[0]
    ctx.artifacts["musicxml_movements"] = produced
    if merged:
        detail = f"{merged.name} (merged from {len(produced)} movements)"
    else:
        detail = f"{len(produced)} separate movements (merge failed)"
    return StageResult("extract", "ok", detail)
