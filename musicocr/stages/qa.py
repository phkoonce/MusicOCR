"""Stage 8: rasterize the source scan and the rendered score for side-by-side QA.

Writes ``qa/src-NNN.png`` (original) and ``qa/omr-NNN.png`` (MuseScore render).
The final report links matching pairs.
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageResult
from musicocr.util import run_cmd


def _rasterize(tool, pdf, out_prefix, dpi, log):
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    run_cmd([tool, "-png", "-r", str(dpi), str(pdf), str(out_prefix)],
            timeout=300, log=log)
    return sorted(out_prefix.parent.glob(out_prefix.name + "-*.png"))


def run(ctx: PipelineContext) -> StageResult:
    if "qa-pdf" not in ctx.config.formats:
        return StageResult("qa", "skipped", "qa-pdf not in output.formats")

    tool = ctx.config.resolve_tool("pdftoppm")
    if not tool:
        return StageResult("qa", "skipped", "pdftoppm not found (brew install poppler)")

    qa_dir = ctx.workdir / "qa"
    dpi = ctx.config.qa_dpi
    source_pdf = ctx.artifacts.get("source_pdf") or (ctx.workdir / "source.pdf")
    render_pdfs = ctx.artifacts.get("render_pdfs") or [
        ctx.artifacts.get("render_pdf") or (ctx.workdir / "render.pdf")
    ]

    src_pages, omr_pages = [], []
    if source_pdf.exists():
        src_pages = _rasterize(tool, source_pdf, qa_dir / "src", dpi, ctx.log)
    for render_pdf in render_pdfs:
        if render_pdf.exists():
            prefix = qa_dir / f"omr-{render_pdf.stem}" if len(render_pdfs) > 1 else qa_dir / "omr"
            omr_pages.extend(_rasterize(tool, render_pdf, prefix, dpi, ctx.log))

    ctx.artifacts["qa_pages"] = {
        "src": [str(p) for p in src_pages],
        "omr": [str(p) for p in omr_pages],
    }
    return StageResult("qa", "ok", f"{len(src_pages)} source / {len(omr_pages)} rendered page(s)")
