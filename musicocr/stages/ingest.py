"""Stage 1: validate the input PDF, prepare the work dir, optionally clean up scans."""
from __future__ import annotations

import re
import shutil

from musicocr.pipeline import PipelineContext, StageError, StageResult
from musicocr.preprocess import PreprocessOpts, prepare_page
from musicocr.util import run_cmd


def _page_count(ctx: PipelineContext) -> int | None:
    tool = ctx.config.resolve_tool("pdfinfo")
    if not tool:
        ctx.log("  pdfinfo not found; skipping page count")
        return None
    proc = run_cmd([tool, str(ctx.input_pdf)], timeout=60, log=ctx.log)
    m = re.search(r"^Pages:\s+(\d+)", proc.stdout, re.MULTILINE)
    return int(m.group(1)) if m else None


def run(ctx: PipelineContext) -> StageResult:
    if not ctx.input_pdf.exists():
        raise StageError(f"input not found: {ctx.input_pdf}")
    if ctx.input_pdf.suffix.lower() != ".pdf":
        raise StageError(f"expected a .pdf, got {ctx.input_pdf.suffix!r}")

    ctx.workdir.mkdir(parents=True, exist_ok=True)
    local_pdf = ctx.workdir / "source.pdf"
    if not local_pdf.exists() or local_pdf.stat().st_mtime < ctx.input_pdf.stat().st_mtime:
        shutil.copy2(ctx.input_pdf, local_pdf)
    ctx.artifacts["source_pdf"] = local_pdf

    pages = _page_count(ctx)
    ctx.artifacts["page_count"] = pages

    detail = f"{ctx.book_name}, {pages if pages is not None else '?'} page(s)"

    opts = PreprocessOpts.from_dict(ctx.config.preprocess)
    if opts.enabled:
        detail += _preprocess(ctx, opts, pages)

    return StageResult("ingest", "ok", detail, data={"page_count": pages})


def _preprocess(ctx: PipelineContext, opts: PreprocessOpts, pages: int | None) -> str:
    pdftoppm = ctx.config.resolve_tool("pdftoppm")
    if not pdftoppm:
        ctx.log("  preprocess: pdftoppm not found, skipping")
        return "  [preprocess skipped: no pdftoppm]"
    if not pages:
        ctx.log("  preprocess: page count unknown, skipping")
        return "  [preprocess skipped: page count unknown]"

    prepped: dict[int, str] = {}
    for p in range(1, pages + 1):
        dst = ctx.workdir / "prepped" / f"p{p:03d}.png"
        if dst.exists() and not ctx.force:
            prepped[p] = str(dst)
            continue
        out = prepare_page(ctx.artifacts["source_pdf"], p, dst, opts, pdftoppm, ctx.log)
        if out:
            prepped[p] = str(out)
        else:
            ctx.log(f"  preprocess: page {p} rasterise failed")
    ctx.artifacts["prepped_pages"] = {int(k): v for k, v in prepped.items()}
    ctx.log(f"  preprocess: {len(prepped)} page(s) at {opts.dpi} DPI "
            f"(autocontrast={opts.autocontrast}, unsharp={opts.unsharp})")
    return f"  [preprocessed {len(prepped)} pages @ {opts.dpi}dpi]"
