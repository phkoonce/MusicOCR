"""Per-page loop shared by the raster-image OMR engines (homr, Copisteria, ...).

These engines read one rasterised page image and write MusicXML for it, so the
orchestration is identical: rasterise (reusing ingest's prepped copy), skip
pages already transcribed unless ``--force``, run the engine with a timeout,
record failed pages, and publish the shared artifacts (``mxl_files``,
``mxl_by_page``, ``omr_failed_pages``, ``omr_log``, ``omr_project``) that
downstream stages consume. Engine modules supply only the command line and any
per-page post-fix.

``ensure_png`` and ``runner`` are passed in (not imported here) so each engine
module's own names stay the seam that tests monkeypatch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from musicocr.errors import StageError, StageResult
from musicocr.pages import resolve_pages
from musicocr.pipeline import PipelineContext
from musicocr.preprocess import PageGeometry, prepare_page


def ensure_page_png(ctx: PipelineContext, page: int, dst: Path) -> Path | None:
    """Write a rasterised page image to `dst`, reusing ingest's prepped copy if any."""
    prepped = ctx.artifacts.prepped_pages or {}
    existing = prepped.get(page) or prepped.get(str(page))
    if existing and Path(existing).exists():
        dst.write_bytes(Path(existing).read_bytes())
        geom = PageGeometry.path_for(Path(existing))
        if geom.exists():
            PageGeometry.path_for(dst).write_bytes(geom.read_bytes())
        return dst

    pdftoppm = ctx.config.resolve_tool("pdftoppm")
    if not pdftoppm:
        return None
    source_pdf = ctx.artifacts.source_pdf or (ctx.shared / "source.pdf")
    return prepare_page(source_pdf, page, dst, ctx.config.preprocess, pdftoppm, ctx.log)


def run_raster_pages(
    ctx: PipelineContext,
    *,
    label: str,
    ensure_png: Callable[[PipelineContext, int, Path], Optional[Path]],
    runner: Callable,
    build_cmd: Callable[[Path, Path], tuple[list[str], Optional[Path]]],
    postprocess: Callable[[int, Path], None] | None = None,
) -> StageResult:
    """Transcribe every selected page with a one-image-in, MusicXML-out engine.

    ``build_cmd(img, pdir)`` returns ``(argv, cwd)``; the engine must leave
    ``<pdir>/<stem>.musicxml`` (stem = ``pNNN``). ``postprocess(page, xml)``
    runs on each fresh export before anything else sees it.
    """
    pages = resolve_pages(ctx)
    if not pages:
        raise StageError("no pages to transcribe (unknown page count and no --pages)")

    pages_root = ctx.workdir / "pages"
    by_page: dict[int, list[Path]] = {}
    failed: list[int] = []
    logs: list[Path] = []

    for i, page in enumerate(pages, 1):
        stem = f"p{page:03d}"
        pdir = pages_root / stem
        out_xml = pdir / f"{stem}.musicxml"
        if out_xml.exists() and not ctx.force:
            ctx.log(f"  page {page} ({i}/{len(pages)}): reuse {out_xml.name}")
            by_page[page] = [out_xml]
            continue

        pdir.mkdir(parents=True, exist_ok=True)
        img = pdir / f"{stem}.png"
        if not ensure_png(ctx, page, img):
            failed.append(page)
            ctx.log(f"  page {page} ({i}/{len(pages)}): FAILED — could not rasterise page")
            continue

        if out_xml.exists():
            out_xml.unlink()
        cmd, cwd = build_cmd(img, pdir)
        kw = {"cwd": cwd} if cwd else {}
        res = runner(cmd, timeout=ctx.config.omr.page_timeout, log=ctx.log, **kw)
        log_path = pdir / f"{stem}.log"
        if res is not None:
            log_path.write_text(res.output or "")
            logs.append(log_path)

        if out_xml.exists() and out_xml.stat().st_size > 0:
            if postprocess:
                postprocess(page, out_xml)
            by_page[page] = [out_xml]
            note = "" if (res and res.returncode == 0) else f" ({label} exited nonzero)"
            ctx.log(f"  page {page} ({i}/{len(pages)}): 1 .musicxml{note}")
        else:
            failed.append(page)
            why = "timed out" if (res and res.timed_out) else "no .musicxml written"
            ctx.log(f"  page {page} ({i}/{len(pages)}): FAILED — {why}")

    all_files = [p for page in sorted(by_page) for p in by_page[page]]
    ctx.artifacts.mxl_files = all_files
    ctx.artifacts.mxl_by_page = by_page
    ctx.artifacts.omr_failed_pages = failed
    ctx.artifacts.omr_log = logs[-1] if logs else None
    ctx.artifacts.omr_project = None

    if not all_files:
        raise StageError(
            f"{label} transcribed none of {len(pages)} page(s). "
            f"Last log: {ctx.artifacts.omr_log}"
        )

    detail = f"{len(all_files)} .musicxml from {len(by_page)}/{len(pages)} page(s)"
    if failed:
        detail += f"; failed: {failed}"
        ctx.log(f"  WARNING: {len(failed)} page(s) not transcribed: {failed}")
    return StageResult("omr", "ok", detail, data={"failed_pages": failed})
