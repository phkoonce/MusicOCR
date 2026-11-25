"""Stage 4: turn the OMR engine's per-page export(s) into plain .musicxml.

Audiveris exports ``.mxl`` (a zip whose ``META-INF/container.xml`` names the
score file); homr writes plain ``.musicxml`` directly. Either way, per-page OMR
gives one (sometimes several) file per page. We keep each as
``<book>.pNNN[.k].musicxml`` and, when ``[omr] merge_pages`` is on, stitch them
into a single ``<book>.musicxml`` by appending each page's measures onto the
part(s) of the first page (correct for one continuous instrumental part;
MuseScore re-numbers measures on import).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from musicocr.errors import StageError, StageResult
from musicocr.fixes import PAGE_FIXES, FixContext, run_fixes
from musicocr.musicxml.merge import merge_pages
from musicocr.pipeline import PipelineContext
from musicocr.scoreops.movements import detect


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


def _page_musicxml(src: Path, dest: Path) -> Path:
    """Normalize one page's OMR export (``.mxl`` or plain ``.musicxml``) to `dest`."""
    if src.suffix == ".mxl":
        return _unzip_mxl(src, dest)
    dest.write_bytes(src.read_bytes())
    return dest


def _find_movements(ctx: PipelineContext, page_files: list[Path], images: dict[Path, Path]) -> None:
    """Note where movements start; the retouch and convert stages write them into the score."""
    if not ctx.config.stages.movements_enabled:
        return
    try:
        found = detect([(images.get(p, p.with_suffix(".png")), p) for p in page_files], log=ctx.log,
                       python=ctx.config.resolve_tool("homr_python"))
    except Exception as exc:  # noqa: BLE001 - a heading is never worth losing the score
        ctx.log(f"  movements: detection failed ({type(exc).__name__}: {exc})")
        return
    ctx.artifacts.movements = found
    for mv in found:
        ctx.log(f"  movement {mv['heading']} starts at bar {mv['bar'] + 1}")


def run(ctx: PipelineContext) -> StageResult:
    by_page = ctx.artifacts.mxl_by_page
    if not by_page:
        flat = ctx.artifacts.mxl_files or sorted(
            p for p in ctx.scratch.rglob("*.mxl") if "experiments" not in p.parts)
        if not flat:
            raise StageError("no .mxl files found; run the omr stage first")
        by_page = {0: flat}

    out = ctx.scratch                       # per-page / movement files: intermediates
    out.mkdir(parents=True, exist_ok=True)
    master = ctx.workdir / f"{ctx.book_name}.musicxml"   # the deliverable

    # Whole-book mode (single pseudo-page 0): behave like a plain extract.
    if set(by_page) == {0}:
        files = by_page[0]
        if len(files) == 1:
            _page_musicxml(files[0], master)
            ctx.artifacts.musicxml = master
            ctx.artifacts.musicxml_pages = []
            return StageResult("extract", "ok", master.name)
        parts = []
        for i, mxl in enumerate(files, 1):
            t = out / f"{ctx.book_name}.mvt{i}.musicxml"
            _page_musicxml(mxl, t)
            parts.append(t)
        merged, skipped = (merge_pages(parts, master, ctx.log)
                           if ctx.config.omr.merge_pages else (None, []))
        ctx.artifacts.musicxml = merged or parts[0]
        ctx.artifacts.musicxml_pages = parts
        ctx.artifacts.merge_skipped = [str(p) for p in skipped]
        ctx.artifacts.extract_merged = bool(merged)
        return StageResult("extract", "ok",
                           f"{master.name} (merged)" if merged
                           else f"{len(parts)} movements (not merged)")

    # Per-page mode.
    page_files: list[Path] = []
    images: dict[Path, Path] = {}           # page file -> the page image the engine read
    ctx.artifacts.movements = []
    for page in sorted(by_page):
        for k, mxl in enumerate(by_page[page]):
            suffix = f".p{page:03d}" + (f".{k + 1}" if len(by_page[page]) > 1 else "")
            t = out / f"{ctx.book_name}{suffix}.musicxml"
            _page_musicxml(mxl, t)
            page_files.append(t)
            if len(by_page[page]) == 1:
                images[t] = mxl.with_suffix(".png")
            ctx.log(f"  extracted {t.name}")
    ctx.artifacts.musicxml_pages = page_files

    # Before merging: the repairs need each page's own bars (and music21 must never see a page
    # without its governing signature).
    run_fixes(PAGE_FIXES, FixContext(page_files, ctx.config, ctx.log))

    if len(page_files) == 1:
        master.write_bytes(page_files[0].read_bytes())
        ctx.artifacts.musicxml = master
        ctx.artifacts.extract_merged = True
        _find_movements(ctx, page_files, images)
        return StageResult("extract", "ok", f"{master.name} (1 page)")

    merged, skipped = (merge_pages(page_files, master, ctx.log)
                       if ctx.config.omr.merge_pages else (None, []))
    ctx.artifacts.merge_skipped = [str(p) for p in skipped]
    ctx.artifacts.musicxml = merged or page_files[0]
    ctx.artifacts.extract_merged = bool(merged)
    if merged:
        _find_movements(ctx, [p for p in page_files if p not in skipped], images)
        kept = len(page_files) - len(skipped)
        detail = f"{master.name} (merged {kept}/{len(page_files)} page files)"
        if skipped:
            detail += f", {len(skipped)} unreadable"
        return StageResult("extract", "ok", detail)
    return StageResult("extract", "ok",
                       f"merge produced nothing; {len(page_files)} page files kept separate")
