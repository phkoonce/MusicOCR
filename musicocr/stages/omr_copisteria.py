"""Stage 2 (Copisteria engine): KobiMusic's page-image → MusicXML reader.

Copisteria (https://github.com/kobimusic/copisteria, Apache-2.0) runs a symbol
detector (``copista-28m`` default, ``copista-2m`` smaller) and a learned
evidence model over a whole scanned page, and writes ``<page>.musicxml`` plus
``<page>.reading.json`` (per-symbol detected/final reading and evidence) and a
``<page>.html`` viewer. It needs Python 3.12 and is run from its repo root
(it locates ``models/`` relative to the cwd), so it lives in its own venv/clone
under ``omr-eval/copisteria/`` created by ``omr-eval/setup.sh``.

Output goes next to the page image (``pages/pNNN/``), exactly where homr's does,
so the rest of the pipeline is engine-agnostic. The extra ``.reading.json`` /
``.html`` files are left beside it for inspection.

It was benchmarked by its authors on multi-staff scores (string quartets,
lieder), not monophonic wind/brass parts, and has no documented handling of
transposing instruments or multi-rests -- hence the side-by-side evaluation
(``musicocr compare``) before trusting it.
"""
from __future__ import annotations

from pathlib import Path

from musicocr.copisteria_fixes import fill_empty_measures
from musicocr.errors import StageError, StageResult
from musicocr.multirest import apply_multirest_form
from musicocr.page_text import boost_multirest_dets, write_texts_json
from musicocr.pipeline import PipelineContext
from musicocr.stages._omr_common import ensure_page_png as _ensure_page_png, run_raster_pages
from musicocr.util import run_cmd_lenient


def _adopt_output(img: Path, pdir: Path) -> None:
    """Ensure the export is named ``<img stem>.musicxml``; if the tool chose a
    different name, rename the lone ``*.musicxml`` it wrote."""
    want = pdir / f"{img.stem}.musicxml"
    if want.exists():
        return
    found = sorted(pdir.glob("*.musicxml"))
    if len(found) == 1:
        found[0].rename(want)


def run(ctx: PipelineContext) -> StageResult:
    py = ctx.config.resolve_tool("copisteria_python")
    repo = Path(ctx.config.omr.copisteria.repo).expanduser()
    if not py or not repo.is_dir():
        raise StageError(
            f"Copisteria not found (python={ctx.config.tools.copisteria_python!r}, "
            f"repo={str(repo)!r}); run omr-eval/setup.sh or edit config.toml "
            "[tools]/[omr.copisteria]"
        )
    detector = ctx.config.omr.copisteria.detector

    pdftotext = ctx.config.resolve_tool("pdftotext")
    source_pdf = ctx.artifacts.source_pdf or (ctx.workdir / "source.pdf")

    def _boost_multirests(img: Path) -> None:
        # Detect first (cached beside the page, the pipeline then reuses it), so a
        # multiRest the evidence model would discard can be nudged. Best effort.
        try:
            code = ("import sys,pathlib;from copisteria.pipeline import detections;"
                    "detections(pathlib.Path(sys.argv[1]))")
            run_cmd_lenient([py, "-c", code, str(img)], timeout=ctx.config.omr.page_timeout,
                            cwd=repo, log=ctx.log)
            texts = img.with_name(f"{img.stem}.texts.json")
            for dets in img.parent.glob(f"{img.stem}.dets_*.json"):
                boost_multirest_dets(dets, texts, log=ctx.log)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  {img.stem}: multiRest boost skipped ({type(exc).__name__}: {exc})")

    def build_cmd(img: Path, pdir: Path):
        if ctx.config.omr.copisteria.texts == "pdf-layer" and pdftotext and source_pdf.exists():
            n = write_texts_json(pdftotext, source_pdf, int(img.stem[1:]), img, log=ctx.log)
            if n:
                ctx.log(f"  {img.stem}: {n} digit box(es) from the PDF text layer")
                _boost_multirests(img)
        cmd = [py, "-m", "copisteria.pipeline", str(img), "--out", str(pdir)]
        if detector != "28m":
            cmd += ["--detector", detector]
        return cmd, repo

    def runner(cmd, **kw):
        res = run_cmd_lenient(cmd, **kw)
        # copisteria names output after the input stem; normalise just in case.
        img = Path(cmd[cmd.index("copisteria.pipeline") + 1])
        _adopt_output(img, img.parent)
        return res

    def postprocess(page: int, xml: Path) -> None:
        # Order matters: give bare-<forward> bars their rest first, so the
        # multi-rest fold sees real whole-measure rests.
        if ctx.config.omr.copisteria.fix_empty:
            try:
                fixed = fill_empty_measures(xml, log=ctx.log)
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"  page {page}: empty-bar fix failed ({type(exc).__name__}: {exc})")
            else:
                if fixed:
                    ctx.log(f"  page {page}: gave {fixed} empty bar(s) a whole-measure rest")
        form = ctx.config.omr.copisteria.multirest_form
        try:
            n = apply_multirest_form(xml, form, log=ctx.log)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  page {page}: multi-rest {form} failed ({type(exc).__name__}: {exc})")
            return
        if n:
            ctx.log(f"  page {page}: multi-rest {form}: {n} bar(s) "
                    f"{'removed' if form == 'collapse' else 'added'}")

    return run_raster_pages(ctx, label="Copisteria", ensure_png=_ensure_page_png,
                            runner=runner, build_cmd=build_cmd, postprocess=postprocess)
