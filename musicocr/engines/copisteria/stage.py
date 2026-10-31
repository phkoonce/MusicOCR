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

import json
from pathlib import Path

from musicocr.engines.copisteria.second_pass import gap_bars, merge_second_pass
from musicocr.errors import StageError, StageResult
from musicocr.fixes import COPISTERIA_FIXES, FixContext, run_fixes
from musicocr.text.page_text import (add_missing_multirest_dets, boost_multirest_dets, drop_digit_repeat_marks,
                                mark_small_graces, pdf_has_text, write_ocr_texts_json, write_texts_json)
from musicocr.engines.copisteria.detect import empty_measures
from musicocr.pipeline import PipelineContext
from musicocr.engines.raster import ensure_page_png as _ensure_page_png, run_raster_pages
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
    source_pdf = ctx.artifacts.source_pdf or (ctx.shared / "source.pdf")

    run_script = Path(__file__).resolve().parent / "run.py"
    script = Path(__file__).resolve().parent / "detect.py"

    def _big_cache(img: Path) -> Path:
        return img.with_name(f"{img.stem}.big_dets.json")

    def _prepare_dets(img: Path, texts_found: bool) -> None:
        # Detect first (cached beside the page, the pipeline then reuses it), then adjust the
        # cached detections before the evidence model reads them: bars the detector found
        # empty are filled from the larger input size, a multiRest the model would discard
        # is added/nudged (page text), and small "not grace" notes become grace notes.
        # Best effort.
        cfg = ctx.config.omr.copisteria
        try:
            code = ("import sys,pathlib;from copisteria.pipeline import detections;"
                    "detections(pathlib.Path(sys.argv[1]))")
            run_cmd_lenient([py, "-c", code, str(img)], timeout=ctx.config.omr.page_timeout,
                            cwd=repo, log=ctx.log)
            texts = img.with_name(f"{img.stem}.texts.json")
            for dets in img.parent.glob(f"{img.stem}.dets_*.json"):
                if cfg.fill_empty_bars and empty_measures(json.loads(dets.read_text())):
                    res = run_cmd_lenient(
                        [py, str(script), str(img), str(dets), str(dets), str(cfg.second_pass_scale),
                         "--empty-bars", str(_big_cache(img))],
                        timeout=ctx.config.omr.page_timeout, cwd=repo, log=ctx.log)
                    if res is None or res.returncode != 0:
                        ctx.log(f"  {img.stem}: empty-bar fill skipped (larger-scale detection failed)")
                    else:
                        ctx.log(f"  {img.stem}: empty-bar fill: {(res.output.strip().splitlines() or [''])[-1]}")
                if texts_found:
                    drop_digit_repeat_marks(dets, texts, log=ctx.log)
                    if cfg.synth_multirest:
                        add_missing_multirest_dets(dets, texts, log=ctx.log)
                    boost_multirest_dets(dets, texts, log=ctx.log)
                if cfg.small_graces:        # after the fill, whose added notes may be small too
                    n = mark_small_graces(dets)
                    if n:
                        ctx.log(f"  {img.stem}: {n} small note(s) marked as grace notes")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  {img.stem}: detection prep skipped ({type(exc).__name__}: {exc})")

    def build_cmd(img: Path, pdir: Path):
        mode = ctx.config.omr.copisteria.texts
        page = int(img.stem[1:])
        have_pdf = bool(pdftotext and source_pdf.exists())
        if mode == "auto":
            mode = "pdf-layer" if have_pdf and pdf_has_text(pdftotext, source_pdf, page,
                                                            log=ctx.log) else "ocr"
        n = 0
        if mode == "pdf-layer" and have_pdf:
            n = write_texts_json(pdftotext, source_pdf, page, img, log=ctx.log)
            src = "the PDF text layer"
        elif mode == "ocr":
            n = write_ocr_texts_json(img, ctx.config.omr.copisteria.ocr_backend, log=ctx.log,
                                     python=ctx.config.resolve_tool("homr_python"))
            src = "OCR"
        if n:
            ctx.log(f"  {img.stem}: {n} digit box(es) from {src}")
        _prepare_dets(img, texts_found=bool(n))
        _write_text_layer(img, page, have_pdf)
        cmd = _page_cmd(img, pdir)
        if detector != "28m":
            cmd += ["--detector", detector]
        return cmd, repo

    def _write_text_layer(img: Path, page: int, have_pdf: bool) -> None:
        """Tempo / expression / technique words -> <stem>.textlayer.json (copisteria_run.py adds them
        as <direction>s). Best effort: a failure leaves the page without text marks."""
        cfg = ctx.config.omr.copisteria.text_layer
        dst = img.with_name(f"{img.stem}.textlayer.json")
        if cfg.mode == "off":
            dst.unlink(missing_ok=True)
            return
        try:
            from musicocr.preprocess import PageGeometry
            from musicocr.text.layer import write_textlayer_json
            n = write_textlayer_json(
                img, cfg, log=ctx.log, pdftotext=pdftotext if have_pdf else None,
                pdf=source_pdf if have_pdf else None, page=page, geom=PageGeometry.load(img),
                ocr_python=ctx.config.resolve_tool("homr_python"))
            if n:
                ctx.log(f"  {img.stem}: {n} text mark(s) (tempo/expression/technique)")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"  {img.stem}: text layer skipped ({type(exc).__name__}: {exc})")

    def _page_cmd(img: Path, pdir: Path) -> list:
        cmd = [py, str(run_script), str(img), "--out", str(pdir)]
        if not ctx.config.omr.copisteria.text_tuplets:
            cmd.append("--no-text-tuplets")
        single = ctx.config.omr.copisteria.single_staff
        if single == "yes" or (single == "auto" and ctx.config.stages.transpose.instrument):
            cmd.append("--single-staff")      # a part: its staves are separate systems, however they look
        return cmd

    def _second_pass_at(cmd, img: Path, scale: float, first: bool, **kw) -> bool:
        """One re-detection at ``scale`` x the input size; True when it filled a gap bar."""
        pdir = img.parent
        out_xml = pdir / f"{img.stem}.musicxml"
        base = sorted(pdir.glob(f"{img.stem}.dets_*.json"))
        if len(base) != 1:
            return False
        work = pdir / ("second_pass" if first else f"second_pass_{scale:g}")
        work.mkdir(exist_ok=True)
        for f in (img, *pdir.glob(f"{img.stem}.texts.json"), *pdir.glob(f"{img.stem}.geom.json"),
                  *pdir.glob(f"{img.stem}.textlayer.json")):
            (work / f.name).write_bytes(f.read_bytes())
        res = run_cmd_lenient(
            [py, str(script), str(work / img.name), str(base[0]), str(work / base[0].name),
             (",".join(str(x) for x in (ctx.config.omr.copisteria.second_pass_scale,
                                         *ctx.config.omr.copisteria.second_pass_extra_scales))
              if first else str(scale)), "union", str(_big_cache(img)) if first else ""],
            cwd=repo, **kw)
        if res is None or res.returncode != 0 or not (work / base[0].name).exists():
            ctx.log(f"  {img.stem}: second pass x{scale:g} skipped (larger-scale detection failed)")
            return False
        if ctx.config.omr.copisteria.small_graces:
            mark_small_graces(work / base[0].name)
        cmd2 = _page_cmd(work / img.name, work)
        cmd2 += cmd[cmd.index("--out") + 2:]          # e.g. --detector
        run_cmd_lenient(cmd2, cwd=repo, **kw)
        second = work / f"{img.stem}.musicxml"
        if not second.exists():
            ctx.log(f"  {img.stem}: second pass x{scale:g} skipped (no output)")
            return False
        merged = work / f"{img.stem}.merged.musicxml"
        n, why = merge_second_pass(out_xml, second, merged)
        ctx.log(f"  {img.stem}: second pass x{scale:g} {'used' if n else 'not used'}: {why}")
        if n:
            pass1 = pdir / f"{img.stem}.pass1.musicxml"
            if not pass1.exists():
                out_xml.rename(pass1)
            merged.replace(out_xml)
        return bool(n)

    def _second_pass(cmd, img: Path, **kw) -> None:
        """Re-read a page that has gaps with extra detections from a larger scale; gaps a
        scale cannot fill are retried at the other scales (the detector's blind spots
        differ per input size)."""
        out_xml = img.parent / f"{img.stem}.musicxml"
        if not out_xml.exists() or not gap_bars(out_xml):
            return
        cfg = ctx.config.omr.copisteria
        scales = [cfg.second_pass_scale, *cfg.second_pass_retry_scales]
        for k, scale in enumerate(scales):
            if k and not gap_bars(out_xml):
                break
            _second_pass_at(cmd, img, scale, first=(k == 0), **kw)

    def runner(cmd, **kw):
        res = run_cmd_lenient(cmd, **kw)
        # copisteria names output after the input stem; normalise just in case.
        img = Path(cmd[2])
        _adopt_output(img, img.parent)
        if ctx.config.omr.copisteria.second_pass:
            try:
                _second_pass(cmd, img, timeout=kw.get("timeout", 900), log=kw.get("log"))
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"  {img.stem}: second pass failed ({type(exc).__name__}: {exc})")
        return res

    def postprocess(page: int, xml: Path) -> None:
        prev = xml.parent.parent / f"p{page - 1:03d}" / f"p{page - 1:03d}.musicxml" if page > 1 else None
        run_fixes(COPISTERIA_FIXES, FixContext([xml], ctx.config, ctx.log, f"page {page}", page, prev))

    return run_raster_pages(ctx, label="Copisteria", ensure_png=_ensure_page_png,
                            runner=runner, build_cmd=build_cmd, postprocess=postprocess)
