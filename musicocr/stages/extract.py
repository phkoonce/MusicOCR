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
from musicocr.musicxml_merge import merge_pages
from musicocr.pipeline import PipelineContext


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


def run(ctx: PipelineContext) -> StageResult:
    by_page = ctx.artifacts.mxl_by_page
    if not by_page:
        flat = ctx.artifacts.mxl_files or sorted(
            p for p in ctx.workdir.rglob("*.mxl") if "experiments" not in p.parts)
        if not flat:
            raise StageError("no .mxl files found; run the omr stage first")
        by_page = {0: flat}

    out = ctx.workdir
    master = out / f"{ctx.book_name}.musicxml"

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
    for page in sorted(by_page):
        for k, mxl in enumerate(by_page[page]):
            suffix = f".p{page:03d}" + (f".{k + 1}" if len(by_page[page]) > 1 else "")
            t = out / f"{ctx.book_name}{suffix}.musicxml"
            _page_musicxml(mxl, t)
            page_files.append(t)
            ctx.log(f"  extracted {t.name}")
    ctx.artifacts.musicxml_pages = page_files

    # Before merging, so music21 never sees a page without its governing signature.
    from musicocr.timesig import fix_time_signatures_files

    fix_time_signatures_files(page_files, log=ctx.log)

    from musicocr.barrests import fix_file as fix_bar_rests

    for pf in page_files:
        try:
            n = fix_bar_rests(pf, log=ctx.log)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  {pf.name}: whole-bar rest fix failed ({type(exc).__name__}: {exc})")
        else:
            if n:
                ctx.log(f"  {pf.name}: {n} overlong whole-bar rest(s) cut to the bar length")

    if ctx.config.stages.tuplets_enabled:
        # needs the repaired meters: it only touches bars that do not add up to their time signature
        from musicocr.tuplets import fix_tuplets_file

        for pf in page_files:
            try:
                n = fix_tuplets_file(pf, flag=ctx.config.stages.tuplets_flag, log=ctx.log)
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"  {pf.name}: tuplet clean-up failed ({type(exc).__name__}: {exc})")
            else:
                if n:
                    ctx.log(f"  {pf.name}: rebuilt {n} bar(s) with inconsistent tuplets")

    if len(page_files) == 1:
        master.write_bytes(page_files[0].read_bytes())
        ctx.artifacts.musicxml = master
        ctx.artifacts.extract_merged = True
        return StageResult("extract", "ok", f"{master.name} (1 page)")

    merged, skipped = (merge_pages(page_files, master, ctx.log)
                       if ctx.config.omr.merge_pages else (None, []))
    ctx.artifacts.merge_skipped = [str(p) for p in skipped]
    ctx.artifacts.musicxml = merged or page_files[0]
    ctx.artifacts.extract_merged = bool(merged)
    if merged:
        kept = len(page_files) - len(skipped)
        detail = f"{master.name} (merged {kept}/{len(page_files)} page files)"
        if skipped:
            detail += f", {len(skipped)} unreadable"
        return StageResult("extract", "ok", detail)
    return StageResult("extract", "ok",
                       f"merge produced nothing; {len(page_files)} page files kept separate")
