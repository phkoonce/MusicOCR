"""Stage 1: validate the input PDF and prepare the work directory."""
from __future__ import annotations

import re
import shutil

from musicocr.pipeline import PipelineContext, StageError, StageResult
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
    return StageResult("ingest", "ok", detail, data={"page_count": pages})
