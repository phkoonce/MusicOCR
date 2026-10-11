"""Boilerplate shared by the stages that work on the merged ``.musicxml``."""
from __future__ import annotations

from pathlib import Path

from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext


def disabled(name: str, section: str) -> StageResult:
    """Skipped result for a stage turned off via ``[stages.<section>] enabled``."""
    return StageResult(name, "skipped", f"disabled ([stages.{section}] enabled = false)")


def require_musicxml(ctx: PipelineContext) -> Path:
    """Return the score to work on: ``ctx.artifacts.musicxml`` if set, else
    ``<workdir>/<book>.musicxml``. Raises StageError if it doesn't exist; records
    the fallback path on the artifacts when none was set."""
    xml = ctx.artifacts.musicxml or (ctx.workdir / f"{ctx.book_name}.musicxml")
    if not xml.exists():
        raise StageError(
            f"no .musicxml at {xml.name}; run the extract stage first (e.g. --from extract)"
        )
    if ctx.artifacts.musicxml is None:
        ctx.artifacts.musicxml = xml
    return xml


def load_score(path: Path, parse_hint: str = ""):
    """Parse `path` with music21, wrapping import/parse failures in StageError."""
    try:
        from music21 import converter
    except Exception as exc:  # noqa: BLE001
        raise StageError(
            f"music21 not importable ({exc}). Activate the venv / run scripts/setup.sh."
        ) from exc
    try:
        return converter.parse(str(path))
    except Exception as exc:  # noqa: BLE001
        msg = f"music21 could not parse {path.name} ({type(exc).__name__}: {exc})."
        if parse_hint:
            msg += " " + parse_hint
        raise StageError(msg) from exc
