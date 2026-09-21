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

Artifacts set on ``ctx`` mirror ``omr_audiveris`` so downstream stages
(correct, extract) don't need to know which engine ran:
  * ``mxl_files``        - flat list of every exported ``.musicxml`` (page order)
  * ``mxl_by_page``      - ``{page_number: [musicxml paths]}``
  * ``omr_failed_pages`` - pages homr could not transcribe
  * ``omr_log``          - newest homr stdout/stderr capture (per-page: the last one)
  * ``omr_project``      - always ``None`` (homr has no project file / GUI round-trip)

Each page's raw export is also patched in place for homr's known empty-multirest
generator bug (``[omr.homr] fix_multirest``, default on) -- see
``musicocr.homr_fixes`` and omr-eval/README.md's 2026-09-12 finding. This has to
happen here, immediately after homr writes the file and before anything else
(merging, validation) touches it.
"""
from __future__ import annotations

from pathlib import Path

from musicocr.homr_fixes import fix_multirest_file
from musicocr.pipeline import PipelineContext, StageError, StageResult
from musicocr.preprocess import PreprocessOpts, prepare_page
from musicocr.stages._omr_common import resolve_pages
from musicocr.util import run_cmd_lenient

_HOMR_ENTRYPOINT = "import sys; from homr.main import main; sys.exit(main())"


def _ensure_page_png(ctx: PipelineContext, page: int, dst: Path) -> Path | None:
    """Write a rasterised page image to `dst`, reusing ingest's prepped copy if any."""
    prepped = ctx.artifacts.get("prepped_pages") or {}
    existing = prepped.get(page) or prepped.get(str(page))
    if existing and Path(existing).exists():
        dst.write_bytes(Path(existing).read_bytes())
        return dst

    pdftoppm = ctx.config.resolve_tool("pdftoppm")
    if not pdftoppm:
        return None
    source_pdf = ctx.artifacts.get("source_pdf") or (ctx.workdir / "source.pdf")
    opts = PreprocessOpts.from_dict(ctx.config.preprocess)
    return prepare_page(source_pdf, page, dst, opts, pdftoppm, ctx.log)


def run(ctx: PipelineContext) -> StageResult:
    homr_python = ctx.config.resolve_tool("homr_python")
    if not homr_python:
        raise StageError(
            f"homr not found at {ctx.config.homr_python!r} (edit config.toml "
            "[tools], or run omr-eval/setup.sh to install it)"
        )

    pages = resolve_pages(ctx)
    if not pages:
        raise StageError("no pages to transcribe (unknown page count and no --pages)")

    pages_root = ctx.workdir / "pages"
    by_page: dict[int, list[Path]] = {}
    failed: list[int] = []
    logs: list[Path] = []

    for i, page in enumerate(pages, 1):
        stem = f"p{page:03d}"
        pdir = pages_root / stem
        out_xml = pdir / f"{stem}.musicxml"
        if out_xml.exists() and not ctx.force:
            ctx.log(f"  page {page} ({i}/{len(pages)}): reuse {out_xml.name}")
            by_page[page] = [out_xml]
            continue

        pdir.mkdir(parents=True, exist_ok=True)
        img = pdir / f"{stem}.png"
        if not _ensure_page_png(ctx, page, img):
            failed.append(page)
            ctx.log(f"  page {page} ({i}/{len(pages)}): FAILED — could not rasterise page")
            continue

        if out_xml.exists():
            out_xml.unlink()
        cmd = [homr_python, "-c", _HOMR_ENTRYPOINT, str(img)]
        res = run_cmd_lenient(cmd, timeout=ctx.config.omr_page_timeout, log=ctx.log)
        log_path = pdir / f"{stem}.log"
        if res is not None:
            log_path.write_text(res.output or "")
            logs.append(log_path)

        if out_xml.exists() and out_xml.stat().st_size > 0:
            if ctx.config.homr_fix_multirest:
                try:
                    fixed = fix_multirest_file(out_xml, log=ctx.log)
                except Exception as exc:  # noqa: BLE001
                    fixed = 0
                    ctx.log(f"  page {page}: multirest fix failed ({type(exc).__name__}: {exc})")
                if fixed:
                    ctx.log(f"  page {page}: fixed {fixed} empty multi-rest measure(s)")
            by_page[page] = [out_xml]
            note = "" if (res and res.returncode == 0) else " (homr exited nonzero)"
            ctx.log(f"  page {page} ({i}/{len(pages)}): 1 .musicxml{note}")
        else:
            failed.append(page)
            why = "timed out" if (res and res.timed_out) else "no .musicxml written"
            ctx.log(f"  page {page} ({i}/{len(pages)}): FAILED — {why}")

    all_files = [p for page in sorted(by_page) for p in by_page[page]]
    ctx.artifacts["mxl_files"] = all_files
    ctx.artifacts["mxl_by_page"] = by_page
    ctx.artifacts["omr_failed_pages"] = failed
    ctx.artifacts["omr_log"] = logs[-1] if logs else None
    ctx.artifacts["omr_project"] = None

    if not all_files:
        raise StageError(
            f"homr transcribed none of {len(pages)} page(s). "
            f"Last log: {ctx.artifacts['omr_log']}"
        )

    detail = f"{len(all_files)} .musicxml from {len(by_page)}/{len(pages)} page(s)"
    if failed:
        detail += f"; failed: {failed}"
        ctx.log(f"  WARNING: {len(failed)} page(s) not transcribed: {failed}")
    return StageResult("omr", "ok", detail, data={"failed_pages": failed})
