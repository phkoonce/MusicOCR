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


def _concat(page_files: list[Path], dest: Path, log) -> tuple[Path | None, list[Path]]:
    """Concatenate the page/movement files into one continuous score.

    Each file is parsed on its own; a file music21 can't read (OMR sometimes
    emits malformed MusicXML — e.g. ``divisions=0``) is skipped rather than
    sinking the whole merge. Measures are appended onto part 0 and renumbered.

    Returns ``(merged_path_or_None, skipped_files)``.
    """
    try:
        from music21 import converter, stream
    except Exception as exc:  # noqa: BLE001
        log(f"  music21 unavailable, cannot merge pages ({exc})")
        return None, []

    def _safe_parse(p: Path):
        try:
            return converter.parse(str(p))
        except Exception as exc:  # noqa: BLE001
            log(f"  merge: skipping unreadable {p.name} ({type(exc).__name__})")
            return None

    skipped: list[Path] = []
    master = None
    m_parts: list = []
    for pf in page_files:
        sc = _safe_parse(pf)
        if sc is None:
            skipped.append(pf)
            continue
        parts = list(sc.parts) or [sc]
        if master is None:
            master = sc
            m_parts = list(master.parts) or [master]
            continue
        for idx, part in enumerate(parts):
            measures = list(part.getElementsByClass(stream.Measure))
            if idx < len(m_parts):
                for m in measures:
                    m_parts[idx].append(m)
            else:
                master.insert(0, part)
                m_parts.append(part)

    if master is None:
        return None, skipped
    try:
        for part in master.parts:
            for i, m in enumerate(part.getElementsByClass(stream.Measure), start=1):
                m.number = i
        master.write("musicxml", fp=str(dest))
    except Exception as exc:  # noqa: BLE001
        log(f"  merge: write failed ({exc})")
        return None, skipped
    kept = len(page_files) - len(skipped)
    log(f"  merged {kept}/{len(page_files)} page file(s) -> {dest.name}"
        + (f"; skipped {[p.name for p in skipped]}" if skipped else ""))
    return dest, skipped


def run(ctx: PipelineContext) -> StageResult:
    by_page = ctx.artifacts.get("mxl_by_page")
    if not by_page:
        flat = ctx.artifacts.get("mxl_files") or sorted(
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
            _unzip_mxl(files[0], master)
            ctx.artifacts["musicxml"] = master
            ctx.artifacts["musicxml_pages"] = []
            return StageResult("extract", "ok", master.name)
        parts = []
        for i, mxl in enumerate(files, 1):
            t = out / f"{ctx.book_name}.mvt{i}.musicxml"
            _unzip_mxl(mxl, t)
            parts.append(t)
        merged, skipped = (_concat(parts, master, ctx.log)
                           if ctx.config.omr_merge_pages else (None, []))
        ctx.artifacts["musicxml"] = merged or parts[0]
        ctx.artifacts["musicxml_pages"] = parts
        ctx.artifacts["merge_skipped"] = [str(p) for p in skipped]
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

    merged, skipped = (_concat(page_files, master, ctx.log)
                       if ctx.config.omr_merge_pages else (None, []))
    ctx.artifacts["merge_skipped"] = [str(p) for p in skipped]
    ctx.artifacts["musicxml"] = merged or page_files[0]
    if merged:
        kept = len(page_files) - len(skipped)
        detail = f"{master.name} (merged {kept}/{len(page_files)} page files)"
        if skipped:
            detail += f", {len(skipped)} unreadable"
        return StageResult("extract", "ok", detail)
    return StageResult("extract", "ok",
                       f"merge produced nothing; {len(page_files)} page files kept separate")
