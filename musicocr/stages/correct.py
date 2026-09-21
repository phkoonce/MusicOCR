"""Stage 3: manual-correction round-trip (disabled by default).

The automated pipeline never opens the GUI. Enable this stage with
``[stages.correct] enabled = true`` in config.toml when you want to hand-fix
pages in Audiveris. It then:

  * makes the omr stage pass ``-save`` so a ``.omr`` project is written per page
    (``output/<book>/pages/pNNN/source.omr``), and
  * pauses the pipeline after OMR, pointing you at the projects to open.

Resume with ``python -m musicocr run <pdf> --from extract`` once you've exported
the corrected pages from the GUI.

Future work: watch the ``.omr`` mtimes and resume automatically.
"""
from __future__ import annotations

from pathlib import Path

from musicocr.pipeline import PipelineContext, StageResult


def _projects(ctx: PipelineContext) -> list[Path]:
    single = ctx.artifacts.get("omr_project")
    if single:
        return [Path(single)]
    return sorted(ctx.workdir.glob("pages/p*/*.omr"))


def run(ctx: PipelineContext) -> StageResult:
    if not ctx.config.correct_enabled:
        return StageResult("correct", "skipped", "disabled (v1 runs fully automated)")
    if ctx.config.omr_engine != "audiveris":
        return StageResult(
            "correct", "skipped",
            f"GUI correction round-trip is Audiveris-only ([omr] engine = "
            f"{ctx.config.omr_engine!r})",
        )

    projects = _projects(ctx)
    failed = ctx.artifacts.get("omr_failed_pages", []) or []

    lines = [
        "",
        "  ┌─ Manual correction (stages.correct.enabled = true) ─────────────",
        "  │ 1. open -a Audiveris",
        "  │ 2. File ▸ Open Book…  and pick a project below",
        "  │ 3. Book ▸ Set Book Parameters…  — tune binarization / switches,",
        "  │    then Sheet ▸ Transcribe Sheet to re-run with your settings",
        "  │ 4. Fix symbols by hand (select ▸ shape palette / Delete)",
        "  │ 5. Book ▸ Export Book   (writes .mxl next to the project)",
        "  │ 6. python -m musicocr run \"%s\" --from extract" % ctx.input_pdf,
        "  └────────────────────────────────────────────────────────────────",
    ]
    if failed:
        lines.append(f"  pages that produced NO output and most need attention: {failed}")
    for p in projects:
        try:
            tag = p.parent.name
        except Exception:  # noqa: BLE001
            tag = ""
        lines.append(f"    {tag:>6}  {p}")
    ctx.log("\n".join(lines))

    return StageResult(
        "correct", "paused",
        f"{len(projects)} project(s) to correct; resume with --from extract",
    )
