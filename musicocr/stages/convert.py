"""Stage 6: convert the MusicXML master to the requested deliverables via MuseScore.

Formats (from config ``output.formats``):
  * ``mscz``   -> ``<book>.mscz``   (hand-edit in MuseScore)
  * ``midi``   -> ``<book>.mid``    (quick aural check)
  * ``qa-pdf`` -> ``render.pdf``    (rendered score for QA against the scan)
  * ``musicxml`` is already produced by the extract stage.
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageError, StageResult
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


def run(ctx: PipelineContext) -> StageResult:
    xml = ctx.artifacts.get("musicxml") or (ctx.workdir / f"{ctx.book_name}.musicxml")
    if not xml.exists():
        raise StageError(
            f"no .musicxml at {xml.name}; run the extract stage first "
            "(e.g. --from extract)"
        )

    wanted = [f for f in ctx.config.formats if f in _EXT]
    if not wanted:
        return StageResult("convert", "skipped", "no MuseScore formats requested")

    tool = ctx.config.resolve_tool("musescore")
    if not tool:
        raise StageError(
            f"MuseScore not found at {ctx.config.musescore!r} (edit config.toml [tools])"
        )

    produced = []
    for fmt in wanted:
        name = "render.pdf" if fmt == "qa-pdf" else f"{ctx.book_name}{_EXT[fmt]}"
        dst = _convert(tool, xml, ctx.workdir / name, ctx.log)
        ctx.artifacts.setdefault("outputs", {})[fmt] = dst
        produced.append(dst.name)

    if "qa-pdf" in wanted:
        ctx.artifacts["render_pdf"] = ctx.workdir / "render.pdf"
    return StageResult("convert", "ok", ", ".join(produced))
