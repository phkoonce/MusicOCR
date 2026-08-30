"""Stage 3: manual-correction round-trip (disabled by default).

v1 is fully automated, so this stage is a no-op unless ``[stages.correct]
enabled = true`` in config.toml.

When enabled it stops the pipeline after OMR with instructions to correct the
saved ``.omr`` project in the Audiveris GUI, then resume with::

    python -m musicocr run <pdf> --from extract

Future work: instead of just stopping, this stage could launch the GUI, wait for
the project's mtime to change, and trigger a corrected re-export automatically.
"""
from __future__ import annotations

from musicocr.pipeline import PipelineContext, StageResult


GUI_HELP = """\
  Manual correction step (stages.correct.enabled = true)
  -----------------------------------------------------
  1. Open the Audiveris GUI:  open -a Audiveris
  2. File > Open Book...  ->  {project}
  3. Fix recognition errors (see docs: https://audiveris.github.io/audiveris/).
     Book > Export exports MusicXML next to the project.
  4. Resume the pipeline:
         python -m musicocr run "{pdf}" --from extract
"""


def run(ctx: PipelineContext) -> StageResult:
    if not ctx.config.correct_enabled:
        return StageResult("correct", "skipped", "disabled (v1 runs fully automated)")

    project = ctx.artifacts.get("omr_project")
    ctx.log(GUI_HELP.format(project=project, pdf=ctx.input_pdf))
    return StageResult(
        "correct", "paused",
        "paused for manual correction — resume with --from extract",
    )
