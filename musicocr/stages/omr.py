"""Stage 2: run Audiveris in batch to transcribe the PDF.

Produces, under ``ctx.workdir``:
  * ``<book>.omr``  - Audiveris project file (kept for later GUI correction)
  * ``<book>*.mxl`` - one or more compressed MusicXML exports
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageError, StageResult
from musicocr.util import run_cmd


def _find_outputs(ctx: PipelineContext):
    omr = sorted(ctx.workdir.rglob("*.omr"), key=lambda p: p.stat().st_mtime)
    mxl = sorted(ctx.workdir.rglob("*.mxl"), key=lambda p: p.stat().st_mtime)
    return omr, mxl


def run(ctx: PipelineContext) -> StageResult:
    tool = ctx.config.resolve_tool("audiveris")
    if not tool:
        raise StageError(
            f"Audiveris not found at {ctx.config.audiveris!r} (edit config.toml [tools])"
        )

    source_pdf = ctx.artifacts.get("source_pdf") or (ctx.workdir / "source.pdf")
    if not source_pdf.exists():
        raise StageError(f"no source PDF in workdir; run the ingest stage first")

    existing_omr, existing_mxl = _find_outputs(ctx)
    if existing_mxl and not ctx.force:
        ctx.log("  reusing existing Audiveris output (use --force to re-run)")
        ctx.artifacts["omr_project"] = existing_omr[-1] if existing_omr else None
        ctx.artifacts["mxl_files"] = existing_mxl
        return StageResult(
            "omr", "skipped", f"{len(existing_mxl)} .mxl already present"
        )

    cmd = [tool, "-batch", "-transcribe", "-export", "-save",
           "-output", str(ctx.workdir)]
    if ctx.force:
        cmd.append("-force")
    for const in ctx.constants():
        cmd += ["-constant", const]
    if ctx.pages:
        cmd += ["-sheets", *ctx.pages.split()]
    cmd += ["--", str(source_pdf)]

    run_cmd(cmd, timeout=ctx.config.omr_timeout, log=ctx.log)

    omr, mxl = _find_outputs(ctx)
    if not mxl:
        raise StageError(
            "Audiveris finished but exported no .mxl — the scan may be too "
            "low-resolution or not recognised as a score. Check the Audiveris "
            "log in the work directory."
        )
    ctx.artifacts["omr_project"] = omr[-1] if omr else None
    ctx.artifacts["mxl_files"] = mxl
    return StageResult(
        "omr", "ok",
        f"{len(mxl)} .mxl, project: {omr[-1].name if omr else 'none'}",
        data={"mxl": [str(p) for p in mxl]},
    )
