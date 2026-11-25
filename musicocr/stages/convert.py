"""Stage 7: convert the MusicXML master to the requested deliverables via MuseScore.

Formats (from config ``output.formats``):
  * ``mscz``   -> ``<book>.mscz``   (hand-edit in MuseScore)
  * ``midi``   -> ``<book>.mid``    (quick aural check)
  * ``qa-pdf`` -> ``render.pdf``    (rendered score for QA against the scan)
  * ``musicxml`` is already produced by the extract stage.
"""
from __future__ import annotations

import shutil

from musicocr.assemble.mscz_sections import add_section_breaks
from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext
from musicocr.scoreops.layout import apply_layout
from musicocr.stages._common import require_musicxml
from musicocr.util import run_cmd_lenient

_EXT = {"mscz": ".mscz", "midi": ".mid", "qa-pdf": ".pdf"}


def _convert(tool, src, dst, log):
    """Convert via MuseScore. MuseScore 4 on macOS often does the work and then
    aborts during shutdown, so success is judged by the output file, not exit
    code."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    res = run_cmd_lenient([tool, "-f", "-o", str(dst), str(src)], timeout=300, log=log)
    if res is None:
        raise StageError(f"could not run MuseScore for {dst.name}")

    if not (dst.exists() and dst.stat().st_size > 0):
        tail = (res.output or "").strip().splitlines()[-10:]
        raise StageError(
            f"MuseScore did not produce {dst.name} (exit {res.returncode}"
            f"{', timed out' if res.timed_out else ''}):\n    "
            + "\n    ".join(tail)
        )
    if res.returncode != 0:
        log(f"  note: MuseScore wrote {dst.name} then exited {res.returncode} "
            f"(known macOS shutdown crash — output is fine)")
    return dst


def _lay_out(ctx: PipelineContext, mscz, tool: str) -> None:
    """Give the merged score the original's lines and pages; a failure keeps MuseScore's own layout."""
    source = ctx.shared_scratch / "source.pdf"
    try:
        apply_layout(mscz, mscz, ctx.artifacts.musicxml_pages or [],
                     source if source.exists() else ctx.input_pdf, tool,
                     ctx.config.resolve_tool("pdftoppm") or "pdftoppm", ctx.log)
    except Exception as exc:  # noqa: BLE001 - a layout is never worth losing the score
        ctx.log(f"  layout: left as MuseScore made it ({exc})")


def run(ctx: PipelineContext) -> StageResult:
    xml = require_musicxml(ctx)

    wanted = [f for f in ctx.config.output.formats if f in _EXT]
    if not wanted:
        return StageResult("convert", "skipped", "no MuseScore formats requested")

    tool = ctx.config.resolve_tool("musescore")
    if not tool:
        raise StageError(
            f"MuseScore not found at {ctx.config.tools.musescore!r} (edit config.toml [tools])"
        )

    # When the extract stage couldn't merge multi-page output into one
    # continuous score, `musicxml` is only the first page — converting just
    # that file would silently drop every later page from the deliverable.
    # Convert every page's MusicXML instead, one file per page.
    pages = ctx.artifacts.musicxml_pages or []
    merged = ctx.artifacts.extract_merged
    sources = (
        [(p.stem.rsplit(".", 1)[-1], p) for p in pages]
        if not merged and len(pages) > 1
        else [(None, xml)]
    )

    # section breaks go before each movement but the first; only the .mscz can hold them
    ends = [mv["bar"] - 1 for mv in ctx.artifacts.movements if mv["bar"] > 0] if merged else []
    lay_out = merged and ctx.config.stages.layout_enabled
    if ends or lay_out:
        wanted = sorted(wanted, key=lambda f: f != "mscz")

    outputs = ctx.artifacts.outputs
    produced = []
    for suffix, src in sources:
        from_mscz = None
        for fmt in wanted:
            if suffix:
                name = f"render.{suffix}.pdf" if fmt == "qa-pdf" else f"{ctx.book_name}.{suffix}{_EXT[fmt]}"
            else:
                name = "render.pdf" if fmt == "qa-pdf" else f"{ctx.book_name}{_EXT[fmt]}"
            # Only the .mscz is a deliverable; MIDI and the QA render are throwaway checks.
            out_dir = ctx.workdir if fmt == "mscz" else ctx.scratch
            dst = _convert(tool, from_mscz or src, out_dir / name, ctx.log)
            if fmt == "mscz" and ends:
                ctx.log(f"  {add_section_breaks(dst, ends)} section break(s) between movements")
                from_mscz = dst                    # so the MIDI and QA render show the breaks too
            if fmt == "mscz" and lay_out:
                _lay_out(ctx, dst, tool)
                from_mscz = dst
            if suffix:
                outputs.setdefault(fmt, []).append(dst)
            else:
                outputs[fmt] = dst
            produced.append(dst.name)

    if not merged and len(pages) > 1:
        # per-page MusicXML is then the deliverable: publish it beside the .mscz files
        for suffix, src in sources:
            if src.parent != ctx.workdir:
                shutil.copy2(src, ctx.workdir / f"{ctx.book_name}.{suffix}.musicxml")

    if "qa-pdf" in wanted:
        qa_out = outputs["qa-pdf"]
        render_pdfs = qa_out if isinstance(qa_out, list) else [qa_out]
        ctx.artifacts.render_pdfs = render_pdfs
        ctx.artifacts.render_pdf = render_pdfs[0]
    if not merged and len(pages) > 1:
        ctx.log(f"  note: pages did not merge into one score — "
                 f"converted {len(pages)} pages separately instead of dropping the rest")
    return StageResult("convert", "ok", ", ".join(produced))
