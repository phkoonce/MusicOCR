"""Stage 2: run Audiveris to transcribe the PDF into MusicXML.

Audiveris on a whole multi-page book is all-or-nothing: if any single page can't
reach the PAGE step (common on real scans), ``processBook`` throws and *nothing*
is exported. So by default we transcribe **one page per Audiveris process**
(``[omr] per_page``); a page that fails then only loses itself.

Artifacts set on ``ctx``:
  * ``mxl_files``       - flat list of every exported ``.mxl`` (page order)
  * ``mxl_by_page``     - ``{page_number: [mxl paths]}``
  * ``omr_failed_pages``- pages Audiveris could not transcribe
  * ``omr_log``         - newest Audiveris log (per-page: the last one)
  * ``omr_project``     - ``.omr`` project, only when ``-save`` was used
"""
from __future__ import annotations

from pathlib import Path

from musicocr.pipeline import PipelineContext, StageError, StageResult
from musicocr.util import run_cmd_lenient


def _expand_pages(spec: str) -> list[int]:
    out: list[int] = []
    for tok in spec.replace(",", " ").split():
        if "-" in tok:
            a, b = tok.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(tok))
    return sorted(set(out))


def _base_cmd(ctx: PipelineContext, tool: str, out_dir: Path) -> list[str]:
    cmd = [tool, "-batch", "-transcribe", "-export", "-output", str(out_dir)]
    if ctx.config.correct_enabled:
        cmd.append("-save")  # GUI round-trip needs the .omr; fragile on big books
    if ctx.force:
        cmd.append("-force")
    for const in ctx.constants():
        cmd += ["-constant", const]
    return cmd


_IGNORE = {"experiments"}


def _mxls_in(d: Path, *, recursive: bool = True) -> list[Path]:
    it = d.rglob("*.mxl") if recursive else d.glob("*.mxl")
    return sorted((p for p in it if not (_IGNORE & set(p.parts))),
                  key=lambda p: p.name)


def _iter_omr(d: Path, *, recursive: bool = True) -> list[Path]:
    it = d.rglob("*.omr") if recursive else d.glob("*.omr")
    return sorted((p for p in it if not (_IGNORE & set(p.parts))),
                  key=lambda p: p.stat().st_mtime)


def _newest_log(d: Path):
    logs = sorted((p for p in d.rglob("*.log") if not (_IGNORE & set(p.parts))),
                  key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _run_whole_book(ctx, tool, source_pdf) -> StageResult:
    cmd = _base_cmd(ctx, tool, ctx.workdir)
    if ctx.pages:
        cmd += ["-sheets", *ctx.pages.split()]
    cmd += ["--", str(source_pdf)]
    res = run_cmd_lenient(cmd, timeout=ctx.config.omr_timeout, log=ctx.log)
    if res is None:
        raise StageError(f"could not launch Audiveris: {tool}")
    mxl = _mxls_in(ctx.workdir, recursive=False)
    ctx.artifacts["omr_log"] = _newest_log(ctx.workdir)
    if not mxl:
        tail = (res.output or "").strip().splitlines()[-15:]
        raise StageError(
            "Audiveris exported no .mxl (whole-book mode). One bad page aborts "
            f"the book — set [omr] per_page = true. Log: {ctx.artifacts['omr_log']}"
            "\n    " + "\n    ".join(tail)
        )
    ctx.artifacts["mxl_files"] = mxl
    ctx.artifacts["mxl_by_page"] = {0: mxl}
    ctx.artifacts["omr_failed_pages"] = []
    omr = _iter_omr(ctx.workdir, recursive=False)
    ctx.artifacts["omr_project"] = omr[-1] if omr else None
    return StageResult("omr", "ok", f"{len(mxl)} .mxl (whole book)")


def _run_per_page(ctx, tool, source_pdf, pages: list[int]) -> StageResult:
    pages_root = ctx.workdir / "pages"
    prepped = ctx.artifacts.get("prepped_pages") or {}
    by_page: dict[int, list[Path]] = {}
    failed: list[int] = []
    logs: list[Path] = []

    for i, page in enumerate(pages, 1):
        pdir = pages_root / f"p{page:03d}"
        done = _mxls_in(pdir)
        if done and not ctx.force:
            ctx.log(f"  page {page} ({i}/{len(pages)}): reuse {len(done)} .mxl")
            by_page[page] = done
            continue

        pdir.mkdir(parents=True, exist_ok=True)
        prep = prepped.get(page) or prepped.get(str(page))
        if prep and Path(prep).exists():
            cmd = _base_cmd(ctx, tool, pdir) + ["--", str(prep)]
        else:
            cmd = _base_cmd(ctx, tool, pdir) + ["-sheets", str(page),
                                               "--", str(source_pdf)]
        res = run_cmd_lenient(cmd, timeout=ctx.config.omr_page_timeout, log=ctx.log)
        lg = _newest_log(pdir)
        if lg:
            logs.append(lg)
        mxls = _mxls_in(pdir)
        if mxls:
            by_page[page] = mxls
            note = "" if (res and res.returncode == 0) else " (Audiveris exited nonzero)"
            ctx.log(f"  page {page} ({i}/{len(pages)}): {len(mxls)} .mxl{note}")
        else:
            failed.append(page)
            why = "timed out" if (res and res.timed_out) else "no .mxl exported"
            ctx.log(f"  page {page} ({i}/{len(pages)}): FAILED — {why}")

    all_mxl = [p for page in sorted(by_page) for p in by_page[page]]
    ctx.artifacts["mxl_files"] = all_mxl
    ctx.artifacts["mxl_by_page"] = by_page
    ctx.artifacts["omr_failed_pages"] = failed
    ctx.artifacts["omr_log"] = logs[-1] if logs else None
    # per-page .omr projects only exist when -save was passed (correct enabled)
    per_page_omr = _iter_omr(pages_root)
    ctx.artifacts["omr_project"] = per_page_omr[-1] if per_page_omr else None

    if not all_mxl:
        raise StageError(
            f"Audiveris transcribed none of {len(pages)} page(s). The scan may be "
            "too low-resolution or not a score. "
            f"Last log: {ctx.artifacts['omr_log']}"
        )

    ok = len(by_page)
    detail = f"{len(all_mxl)} .mxl from {ok}/{len(pages)} page(s)"
    if failed:
        detail += f"; failed: {failed}"
        ctx.log(f"  WARNING: {len(failed)} page(s) not transcribed: {failed}")
    return StageResult("omr", "ok", detail, data={"failed_pages": failed})


def run(ctx: PipelineContext) -> StageResult:
    tool = ctx.config.resolve_tool("audiveris")
    if not tool:
        raise StageError(
            f"Audiveris not found at {ctx.config.audiveris!r} (edit config.toml [tools])"
        )

    source_pdf = ctx.artifacts.get("source_pdf") or (ctx.workdir / "source.pdf")
    if not source_pdf.exists():
        raise StageError("no source PDF in workdir; run the ingest stage first")

    # Reuse a previous *whole-book* export (files directly in the work dir).
    # Per-page reuse is handled inside _run_per_page, page by page.
    existing = _mxls_in(ctx.workdir, recursive=False)
    if existing and not ctx.force:
        ctx.log("  reusing existing whole-book Audiveris output (--force to re-run)")
        ctx.artifacts["mxl_files"] = existing
        ctx.artifacts["mxl_by_page"] = {0: existing}
        ctx.artifacts["omr_failed_pages"] = []
        omr = _iter_omr(ctx.workdir, recursive=False)
        ctx.artifacts["omr_project"] = omr[-1] if omr else None
        ctx.artifacts["omr_log"] = _newest_log(ctx.workdir)
        return StageResult("omr", "skipped", f"{len(existing)} .mxl already present")

    page_count = ctx.artifacts.get("page_count")
    if ctx.pages:
        pages = _expand_pages(ctx.pages)
    elif page_count:
        pages = list(range(1, page_count + 1))
    else:
        pages = []

    if ctx.config.omr_per_page and pages:
        return _run_per_page(ctx, tool, source_pdf, pages)
    return _run_whole_book(ctx, tool, source_pdf)
