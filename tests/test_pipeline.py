import pytest

from musicocr import pipeline
from musicocr.config import Config
from musicocr.errors import StageError, StageResult
from musicocr.pages import expand_pages, resolve_pages
from musicocr.pipeline import PipelineContext, STAGE_ORDER, resolve_slice, run_pipeline


def test_resolve_slice_full_and_bounds():
    assert resolve_slice(None, None) == STAGE_ORDER
    assert resolve_slice("extract", None) == STAGE_ORDER[STAGE_ORDER.index("extract"):]
    assert resolve_slice(None, "omr") == ["ingest", "omr"]
    assert resolve_slice("omr", "omr") == ["omr"]


@pytest.mark.parametrize("frm,to", [("bogus", None), (None, "bogus"), ("qa", "ingest")])
def test_resolve_slice_invalid(frm, to):
    with pytest.raises(ValueError):
        resolve_slice(frm, to)


def _ctx(tmp_path, **kw):
    return PipelineContext(
        input_pdf=tmp_path / "x.pdf", workdir=tmp_path / "work",  # workdir absent: no state I/O
        config=Config(path=tmp_path / "c.toml"), book_name="x", log=lambda m: None, **kw)


def _fake(monkeypatch, behaviors):
    calls = []

    def loader(name):
        def run(ctx):
            calls.append(name)
            b = behaviors.get(name, "ok")
            if isinstance(b, Exception):
                raise b
            return StageResult(name, b)
        return run

    monkeypatch.setattr(pipeline, "load_stage", loader)
    return calls


def test_runs_all_stages_in_order(monkeypatch, tmp_path):
    calls = _fake(monkeypatch, {})
    results = run_pipeline(_ctx(tmp_path))
    assert calls == STAGE_ORDER and all(r.ok for r in results)


def test_stops_on_failure(monkeypatch, tmp_path):
    calls = _fake(monkeypatch, {"omr": "failed"})
    results = run_pipeline(_ctx(tmp_path))
    assert calls == ["ingest", "omr"]
    assert [r.status for r in results] == ["ok", "failed"]


def test_keep_going_continues(monkeypatch, tmp_path):
    calls = _fake(monkeypatch, {"omr": "failed"})
    results = run_pipeline(_ctx(tmp_path), keep_going=True)
    assert calls == STAGE_ORDER
    assert [r.status for r in results].count("failed") == 1


def test_stage_error_and_generic_exception(monkeypatch, tmp_path):
    _fake(monkeypatch, {"ingest": StageError("bad pdf")})
    (r,) = run_pipeline(_ctx(tmp_path))
    assert r.status == "failed" and r.detail == "bad pdf"

    _fake(monkeypatch, {"ingest": ValueError("boom")})
    (r,) = run_pipeline(_ctx(tmp_path))
    assert r.status == "failed" and r.detail == "ValueError: boom"


def test_paused_stops_pipeline(monkeypatch, tmp_path):
    calls = _fake(monkeypatch, {"correct": "paused"})
    results = run_pipeline(_ctx(tmp_path), from_stage="omr")
    assert calls == ["omr", "correct"] and results[-1].paused


def test_expand_pages():
    assert expand_pages("1 4-5,7") == [1, 4, 5, 7]


def test_resolve_pages(tmp_path):
    assert resolve_pages(_ctx(tmp_path, pages="2-3")) == [2, 3]
    ctx = _ctx(tmp_path)
    assert resolve_pages(ctx) == []
    ctx.artifacts.page_count = 3
    assert resolve_pages(ctx) == [1, 2, 3]
