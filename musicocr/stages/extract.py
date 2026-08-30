"""Stage 4: turn Audiveris .mxl export(s) into plain .musicxml.

An ``.mxl`` is a zip whose ``META-INF/container.xml`` names the score file.

Per-page OMR gives one (sometimes several) ``.mxl`` per page. We keep each as
``<book>.pNNN[.k].musicxml`` and, when ``[omr] merge_pages`` is on, stitch them
into a single ``<book>.musicxml`` by appending each page's measures onto the
part(s) of the first page (correct for one continuous instrumental part;
MuseScore re-numbers measures on import).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from musicocr.pipeline import PipelineContext, StageError, StageResult


def _unzip_mxl(mxl: Path, dest: Path) -> Path:
    with zipfile.ZipFile(mxl) as zf:
        root = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = root.find(".//{*}rootfile")
        inner = rootfile.get("full-path") if rootfile is not None else None
        if not inner:
            raise StageError(f"{mxl.name}: no rootfile in container.xml")
        data = zf.read(inner)
    dest.write_bytes(data)
    return dest


def _concat(page_files: list[Path], dest: Path, log) -> Path | None:
    """Append each page's measures onto the first page's part(s)."""
    try:
        from music21 import converter, stream
    except Exception as exc:  # noqa: BLE001
        log(f"  music21 unavailable, cannot merge pages ({exc})")
        return None
    try:
        master = converter.parse(str(page_files[0]))
        m_parts = list(master.parts)
        for pf in page_files[1:]:
            sc = converter.parse(str(pf))
            for idx, part in enumerate(sc.parts):
                measures = list(part.getElementsByClass(stream.Measure))
                if idx < len(m_parts):
                    for m in measures:
                        m_parts[idx].append(m)
                else:
                    master.append(part)
                    m_parts.append(part)
        # Each page's MusicXML restarts measure numbers; renumber continuously so
        # the merged score and the validation report line up.
        for part in master.parts:
            for i, m in enumerate(part.getElementsByClass(stream.Measure), start=1):
                m.number = i
        master.write("musicxml", fp=str(dest))
        log(f"  merged {len(page_files)} pages -> {dest.name}")
        return dest
    except Exception as exc:  # noqa: BLE001
        log(f"  page merge failed, keeping pages separate ({exc})")
        return None


def run(ctx: PipelineContext) -> StageResult:
    by_page = ctx.artifacts.get("mxl_by_page")
    if not by_page:
        flat = ctx.artifacts.get("mxl_files") or sorted(ctx.workdir.rglob("*.mxl"))
        if not flat:
            raise StageError("no .mxl files found; run the omr stage first")
        by_page = {0: flat}

    out = ctx.workdir
    master = out / f"{ctx.book_name}.musicxml"

    # Whole-book mode (single pseudo-page 0): behave like a plain extract.
    if set(by_page) == {0}:
        files = by_page[0]
        if len(files) == 1:
            _unzip_mxl(files[0], master)
            ctx.artifacts["musicxml"] = master
            ctx.artifacts["musicxml_pages"] = []
            return StageResult("extract", "ok", master.name)
        parts = []
        for i, mxl in enumerate(files, 1):
            t = out / f"{ctx.book_name}.mvt{i}.musicxml"
            _unzip_mxl(mxl, t)
            parts.append(t)
        merged = _concat(parts, master, ctx.log) if ctx.config.omr_merge_pages else None
        ctx.artifacts["musicxml"] = merged or parts[0]
        ctx.artifacts["musicxml_pages"] = parts
        return StageResult("extract", "ok",
                           f"{master.name} (merged)" if merged
                           else f"{len(parts)} movements (not merged)")

    # Per-page mode.
    page_files: list[Path] = []
    for page in sorted(by_page):
        for k, mxl in enumerate(by_page[page]):
            suffix = f".p{page:03d}" + (f".{k + 1}" if len(by_page[page]) > 1 else "")
            t = out / f"{ctx.book_name}{suffix}.musicxml"
            _unzip_mxl(mxl, t)
            page_files.append(t)
            ctx.log(f"  extracted {t.name}")
    ctx.artifacts["musicxml_pages"] = page_files

    if len(page_files) == 1:
        master.write_bytes(page_files[0].read_bytes())
        ctx.artifacts["musicxml"] = master
        return StageResult("extract", "ok", f"{master.name} (1 page)")

    merged = _concat(page_files, master, ctx.log) if ctx.config.omr_merge_pages else None
    ctx.artifacts["musicxml"] = merged or page_files[0]
    if merged:
        return StageResult("extract", "ok",
                           f"{master.name} (merged from {len(page_files)} pages)")
    return StageResult("extract", "ok",
                       f"{len(page_files)} pages kept separate (merge off/failed)")
