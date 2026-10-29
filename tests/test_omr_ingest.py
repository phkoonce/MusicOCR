"""Tests for omr engine dispatch and ingest stage handling (subprocess calls faked)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from musicocr.config import load_config
from musicocr.errors import StageError
from musicocr.pages import expand_pages, resolve_pages
from musicocr.pipeline import PipelineContext
from musicocr.engines.homr import stage as omr_homr
from musicocr.stages import ingest, omr
from musicocr.util import LenientResult


def _ctx(tmp_path, pages=None, **cfg_changes):
    cfg = load_config()
    cfg = replace(cfg, preprocess=replace(cfg.preprocess, enabled=False))
    if "omr" in cfg_changes:
        cfg = replace(cfg, omr=replace(cfg.omr, **cfg_changes.pop("omr")))
    if "tools" in cfg_changes:
        cfg = replace(cfg, tools=replace(cfg.tools, **cfg_changes.pop("tools")))
    if "preprocess" in cfg_changes:
        cfg = replace(cfg, preprocess=replace(cfg.preprocess, **cfg_changes.pop("preprocess")))
    return PipelineContext(input_pdf=tmp_path / "in.pdf", workdir=tmp_path / "work",
                           config=cfg, book_name="in", pages=pages, log=lambda s: None)


# ---- engine dispatch -------------------------------------------------------

def test_unknown_engine_raises(tmp_path):
    ctx = _ctx(tmp_path)
    object.__setattr__(ctx.config.omr, "engine", "nope")
    with pytest.raises(StageError, match="unknown .*engine 'nope'"):
        omr.run(ctx)


@pytest.mark.parametrize("engine", ["homr", "audiveris"])
def test_dispatch_calls_matching_module(tmp_path, monkeypatch, engine):
    sentinel = object()
    monkeypatch.setitem(omr._ENGINES, engine, type("M", (), {"run": staticmethod(lambda c: sentinel)}))
    ctx = _ctx(tmp_path, omr={"engine": engine})
    assert omr.run(ctx) is sentinel


# ---- page specs ------------------------------------------------------------

def test_expand_pages_forms():
    assert expand_pages("1 4-5") == [1, 4, 5]
    assert expand_pages("1,4-5") == [1, 4, 5]
    assert expand_pages("3 1 3 2-3") == [1, 2, 3]
    assert expand_pages("") == []


def test_resolve_pages(tmp_path):
    ctx = _ctx(tmp_path, pages="2-3")
    ctx.artifacts.page_count = 10
    assert resolve_pages(ctx) == [2, 3]
    ctx.pages = None
    assert resolve_pages(ctx) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ctx.artifacts.page_count = None
    assert resolve_pages(ctx) == []


# ---- homr stage ------------------------------------------------------------

def test_homr_missing_tool_raises(tmp_path):
    ctx = _ctx(tmp_path, tools={"homr_python": str(tmp_path / "nope")})
    with pytest.raises(StageError, match="homr not found"):
        omr_homr.run(ctx)


def test_homr_no_pages_raises(tmp_path):
    fake = tmp_path / "py"
    fake.write_text("")
    ctx = _ctx(tmp_path, tools={"homr_python": str(fake)})
    with pytest.raises(StageError, match="no pages to transcribe"):
        omr_homr.run(ctx)


def _fake_homr(monkeypatch, ok_pages, writes_empty=()):
    """Fake run_cmd_lenient: writes <img>.musicxml for pages in ok_pages."""
    calls = []

    def fake(cmd, **kw):
        from pathlib import Path
        img = Path(cmd[-1])
        calls.append(img.name)
        page = int(img.stem[1:])
        out = img.with_suffix(".musicxml")
        if page in ok_pages:
            out.write_text("<score-partwise/>")
        elif page in writes_empty:
            out.write_text("")
        return LenientResult(0, "log text", False)

    monkeypatch.setattr(omr_homr, "run_cmd_lenient", fake)
    monkeypatch.setattr(omr_homr, "_ensure_page_png",
                        lambda ctx, page, dst: (dst.write_bytes(b"png"), dst)[1])
    return calls


def test_homr_partial_failure_records_failed_pages(tmp_path, monkeypatch):
    fake = tmp_path / "py"
    fake.write_text("")
    ctx = _ctx(tmp_path, pages="1-3", tools={"homr_python": str(fake)})
    ctx.artifacts.page_count = 3
    calls = _fake_homr(monkeypatch, ok_pages={1, 3}, writes_empty={2})
    res = omr_homr.run(ctx)
    assert calls == ["p001.png", "p002.png", "p003.png"]
    assert res.status == "ok"
    assert ctx.artifacts.omr_failed_pages == [2]
    assert sorted(ctx.artifacts.mxl_by_page) == [1, 3]
    assert [p.name for p in ctx.artifacts.mxl_files] == ["p001.musicxml", "p003.musicxml"]
    assert "failed: [2]" in res.detail
    assert ctx.artifacts.omr_project is None
    assert ctx.artifacts.omr_log.name == "p003.log"


def test_homr_all_pages_fail_raises(tmp_path, monkeypatch):
    fake = tmp_path / "py"
    fake.write_text("")
    ctx = _ctx(tmp_path, pages="1", tools={"homr_python": str(fake)})
    _fake_homr(monkeypatch, ok_pages=set())
    with pytest.raises(StageError, match="transcribed none of 1"):
        omr_homr.run(ctx)


def test_homr_reuses_existing_output_unless_forced(tmp_path, monkeypatch):
    fake = tmp_path / "py"
    fake.write_text("")
    ctx = _ctx(tmp_path, pages="1", tools={"homr_python": str(fake)})
    existing = ctx.workdir / "pages" / "p001" / "p001.musicxml"
    existing.parent.mkdir(parents=True)
    existing.write_text("<score-partwise/>")
    calls = _fake_homr(monkeypatch, ok_pages={1})
    omr_homr.run(ctx)
    assert calls == []
    ctx.force = True
    omr_homr.run(ctx)
    assert calls == ["p001.png"]


def test_homr_rasterise_failure_marks_page_failed(tmp_path, monkeypatch):
    fake = tmp_path / "py"
    fake.write_text("")
    ctx = _ctx(tmp_path, pages="1-2", tools={"homr_python": str(fake)})
    monkeypatch.setattr(omr_homr, "_ensure_page_png",
                        lambda c, page, dst: (dst.write_bytes(b"x"), dst)[1] if page == 2 else None)
    calls = []
    monkeypatch.setattr(omr_homr, "run_cmd_lenient", lambda cmd, **kw: (
        calls.append(cmd[-1]),
        (__import__("pathlib").Path(cmd[-1]).with_suffix(".musicxml").write_text("<s/>"),
         LenientResult(0, "", False))[1])[1])
    res = omr_homr.run(ctx)
    assert ctx.artifacts.omr_failed_pages == [1]
    assert list(ctx.artifacts.mxl_by_page) == [2]
    assert res.data == {"failed_pages": [1]}


# ---- ingest ----------------------------------------------------------------

def test_ingest_missing_input(tmp_path):
    with pytest.raises(StageError, match="input not found"):
        ingest.run(_ctx(tmp_path))


def test_ingest_rejects_non_pdf(tmp_path):
    (tmp_path / "in.pdf").unlink() if (tmp_path / "in.pdf").exists() else None
    ctx = _ctx(tmp_path)
    ctx.input_pdf = tmp_path / "score.png"
    ctx.input_pdf.write_bytes(b"x")
    with pytest.raises(StageError, match="expected a .pdf"):
        ingest.run(ctx)


def test_ingest_copies_pdf_and_parses_page_count(tmp_path, monkeypatch):
    (tmp_path / "in.pdf").write_bytes(b"%PDF fake")
    ctx = _ctx(tmp_path, tools={"pdfinfo": "/bin/echo"})
    seen = []

    class P:
        stdout = "Title: x\nPages:          7\n"

    monkeypatch.setattr(ingest, "run_cmd", lambda cmd, **kw: (seen.append(cmd), P)[1])
    res = ingest.run(ctx)
    assert (ctx.workdir / "source.pdf").read_bytes() == b"%PDF fake"
    assert ctx.artifacts.source_pdf == ctx.workdir / "source.pdf"
    assert ctx.artifacts.page_count == 7
    assert res.detail == "in, 7 page(s)"
    assert res.data == {"page_count": 7}
    assert seen == [["/bin/echo", str(tmp_path / "in.pdf")]]


def test_ingest_without_pdfinfo(tmp_path):
    (tmp_path / "in.pdf").write_bytes(b"%PDF")
    ctx = _ctx(tmp_path, tools={"pdfinfo": "definitely-not-a-real-binary-xyz"})
    res = ingest.run(ctx)
    assert ctx.artifacts.page_count is None
    assert res.detail == "in, ? page(s)"


def test_ingest_preprocess_skipped_without_pdftoppm(tmp_path):
    (tmp_path / "in.pdf").write_bytes(b"%PDF")
    ctx = _ctx(tmp_path, tools={"pdfinfo": "definitely-not-real",
                                "pdftoppm": "definitely-not-real"},
               preprocess={"enabled": True})
    res = ingest.run(ctx)
    assert "preprocess skipped: no pdftoppm" in res.detail


def test_ingest_preprocess_runs_per_page(tmp_path, monkeypatch):
    (tmp_path / "in.pdf").write_bytes(b"%PDF")
    ctx = _ctx(tmp_path, tools={"pdfinfo": "/bin/echo", "pdftoppm": "/bin/echo"},
               preprocess={"enabled": True, "deskew": True})

    class P:
        stdout = "Pages: 2\n"

    monkeypatch.setattr(ingest, "run_cmd", lambda cmd, **kw: P)
    got = []

    def fake_prepare(pdf, page, dst, opts, tool, log):
        got.append(page)
        if page == 2:
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"png")
        return dst

    monkeypatch.setattr(ingest, "prepare_page", fake_prepare)
    res = ingest.run(ctx)
    assert got == [1, 2]
    assert sorted(ctx.artifacts.prepped_pages) == [1]
    assert "preprocess: 1 page(s)" in res.detail and "deskew" in res.detail


# ---- engine registry / copisteria / legato2 ---------------------------------

def test_registry_matches_config_validation():
    from musicocr.config import ConfigError, OmrConfig
    from musicocr.engines import ENGINES, VALID_ENGINES
    assert VALID_ENGINES == set(ENGINES) >= {"homr", "audiveris", "copisteria", "legato2"}
    OmrConfig.from_dict({"engine": "legato2"})
    with pytest.raises(ConfigError):
        OmrConfig.from_dict({"engine": "nope"})


def test_legato2_missing_tool_raises(tmp_path):
    ctx = _ctx(tmp_path, omr={"engine": "legato2"}, tools={"legato2_python": str(tmp_path / "nope")})
    with pytest.raises(StageError, match="LEGATO 2 python not found"):
        omr.run(ctx)


def test_copisteria_missing_tool_raises(tmp_path):
    ctx = _ctx(tmp_path, tools={"copisteria_python": str(tmp_path / "nope")})
    with pytest.raises(StageError, match="Copisteria not found"):
        omr.run(replace_engine(ctx, "copisteria"))


def replace_engine(ctx, engine):
    object.__setattr__(ctx.config.omr, "engine", engine)
    return ctx


def test_copisteria_runs_from_repo_root_and_adopts_output(tmp_path, monkeypatch):
    from musicocr.engines.copisteria import stage as omr_copisteria
    py = tmp_path / "py"
    py.write_text("")
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = _ctx(tmp_path, pages="1-2", tools={"copisteria_python": str(py)})
    object.__setattr__(ctx.config.omr, "copisteria",
                       type(ctx.config.omr.copisteria)(detector="2m", repo=str(repo)))
    seen = []

    def fake(cmd, **kw):
        from pathlib import Path
        if "-c" in cmd:          # the detection pre-step, not the page read
            return LenientResult(0, "", False)
        seen.append((cmd, kw.get("cwd")))
        img = Path(cmd[2])       # [python, copisteria_run.py, <page.png>, --out, <dir>, ...]
        # page 1 uses the expected name; page 2 a differently-named export
        name = "p001.musicxml" if img.stem == "p001" else "weird.musicxml"
        (img.parent / name).write_text("<score-partwise/>")
        return LenientResult(0, "", False)

    monkeypatch.setattr(omr_copisteria, "run_cmd_lenient", fake)
    monkeypatch.setattr(omr_copisteria, "_ensure_page_png",
                        lambda c, page, dst: (dst.write_bytes(b"png"), dst)[1])
    res = omr_copisteria.run(ctx)
    assert res.status == "ok" and ctx.artifacts.omr_failed_pages == []
    assert [p.name for p in ctx.artifacts.mxl_files] == ["p001.musicxml", "p002.musicxml"]
    assert all(cwd == repo for _, cwd in seen)
    assert "--detector" in seen[0][0] and "2m" in seen[0][0]
