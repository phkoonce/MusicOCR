"""Stage 2 (homr engine): run homr to transcribe rasterised page images into MusicXML.

homr (https://github.com/liebharc/homr) is an end-to-end transformer OMR model
that, per local evaluation (see omr-eval/README.md), consistently beats
Audiveris's classical segment-classify-reconstruct pipeline on monophonic
wind/brass parts. It has no whole-book mode, no Audiveris-style ``-constant``
tuning, and no ``-save`` GUI round-trip — it just reads one page image and
writes ``<image>.musicxml`` next to it.

It's invoked by calling ``homr.main:main`` directly inside its own venv's
interpreter (``[tools] homr_python``, a separate Python 3.12/ONNX environment
from this project's own venv — see ``omr-eval/setup.sh``), rather than the
installed ``homr`` console-script: that script's shebang gets baked to an
absolute path at install time and breaks if the venv is ever moved (see
omr-eval/README.md). Calling the function directly sidesteps that.

Artifacts set on ``ctx`` mirror ``engines.audiveris`` so downstream stages
(correct, extract) don't need to know which engine ran:
  * ``mxl_files``        - flat list of every exported ``.musicxml`` (page order)
  * ``mxl_by_page``      - ``{page_number: [musicxml paths]}``
  * ``omr_failed_pages`` - pages homr could not transcribe
  * ``omr_log``          - newest homr stdout/stderr capture (per-page: the last one)
  * ``omr_project``      - always ``None`` (homr has no project file / GUI round-trip)

The per-page loop is shared with the other raster engines
(``musicocr.engines.raster``); this module supplies only the homr command
line and its post-fix.

Each page's raw export is also patched in place for homr's known empty-multirest
generator bug (``[omr.homr] fix_multirest``, default on) -- see
``musicocr.engines.homr.fixes`` and omr-eval/README.md's 2026-09-12 finding. This has to
happen here, immediately after homr writes the file and before anything else
(merging, validation) touches it.
"""
from __future__ import annotations

from pathlib import Path

from musicocr.errors import StageError, StageResult
from musicocr.fixes import HOMR_FIXES, FixContext, run_fixes
from musicocr.pipeline import PipelineContext
from musicocr.engines.raster import ensure_page_png as _ensure_page_png, run_raster_pages
from musicocr.util import run_cmd_lenient

_HOMR_ENTRYPOINT = "import sys; from homr.main import main; sys.exit(main())"


def run(ctx: PipelineContext) -> StageResult:
    homr_python = ctx.config.resolve_tool("homr_python")
    if not homr_python:
        raise StageError(
            f"homr not found at {ctx.config.tools.homr_python!r} (edit config.toml "
            "[tools], or run omr-eval/setup.sh to install it)"
        )

    def build_cmd(img: Path, pdir: Path):
        return [homr_python, "-c", _HOMR_ENTRYPOINT, str(img)], None

    def postprocess(page: int, xml: Path) -> None:
        run_fixes(HOMR_FIXES, FixContext([xml], ctx.config, ctx.log, f"page {page}", page))

    return run_raster_pages(ctx, label="homr", ensure_png=_ensure_page_png,
                            runner=run_cmd_lenient, build_cmd=build_cmd,
                            postprocess=postprocess)
