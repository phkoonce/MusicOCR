"""Tests for musicocr.compare: pure metrics/diff functions, layout, CLI."""
from __future__ import annotations

from pathlib import Path

import pytest

from musicocr import cli, compare, pipeline
from musicocr.config import Config
from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext

HEAD = ('<?xml version="1.0"?><score-partwise><part-list><score-part id="P1"/></part-list>'
        '<part id="P1">')
TAIL = "</part></score-partwise>"


def _note(step, octave, dur, extra=""):
    return (f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
            f"<duration>{dur}</duration>{extra}</note>")


def _m(n, body, attrs=""):
    return f'<measure number="{n}">{attrs}{body}</measure>'


ATTR = ("<attributes><divisions>1</divisions><key><fifths>-1</fifths></key>"
        "<time><beats>4</beats><beat-type>4</beat-type></time></attributes>")
WHOLE = '<note><rest measure="yes"/><duration>4</duration></note>'
MR3 = ('<attributes><measure-style><multiple-rest>3</multiple-rest></measure-style>'
       '</attributes>')


def _write(tmp_path, name, measures):
    p = tmp_path / name
    p.write_text(HEAD + "".join(measures) + TAIL)
    return p


def _bar(step):
    return _m(0, _note(step, 4, 2) + _note(step, 4, 2))


def test_units_signature_ignores_ties_and_counts(tmp_path):
    tie = _note("C", 4, 4, '<tie type="start"/><notations><tied type="start"/></notations>')
    f = _write(tmp_path, "b.p001.musicxml", [
        _m(1, tie, ATTR),
        _m(2, _note("D", 4, 3, "<time-modification/>") + _note("F", 4, 1)
           + _note("A", 4, 1, "<chord/>")),
    ])
    u = compare.read_units(f, 1)
    assert u[0].sig == (("C4", "4"),)
    assert u[0].times == ["4/4"] and u[0].keys == [-1]
    assert u[1].tuplets == 1 and u[1].notes == 3
    assert u[1].sig == (("D4", "3"), ("A4+F4", "1"))


def test_collapsed_and_expanded_multirest_compare_equal(tmp_path):
    collapsed = _write(tmp_path, "a.p001.musicxml", [
        _m(1, _note("C", 4, 4), ATTR), _m(2, WHOLE, MR3), _m(3, _note("D", 4, 4))])
    expanded = _write(tmp_path, "b.p001.musicxml", [
        _m(1, _note("C", 4, 4), ATTR), _m(2, WHOLE, MR3), _m(3, WHOLE), _m(4, WHOLE),
        _m(5, _note("D", 4, 4))])
    a, b = compare.load_sequence([collapsed]), compare.load_sequence([expanded])
    assert [u.sig for u in a] == [u.sig for u in b]
    assert [u.bar for u in a] == [1, 2, 5]
    assert compare.page_metrics(a)["multirests"] == [3]
    assert compare.page_metrics(a)["bars"] == 5
    assert compare.diff_sequences(a, b)["ratio"] == 1.0


def test_diff_finds_extra_bar_and_substitution(tmp_path):
    a = _write(tmp_path, "a.p001.musicxml",
               [_m(1, _note("C", 4, 4), ATTR), _m(2, _note("D", 4, 4)),
                _m(3, _note("E", 4, 4)), _m(4, _note("F", 4, 4))])
    b = _write(tmp_path, "b.p001.musicxml",
               [_m(1, _note("C", 4, 4), ATTR), _m(2, _note("D", 4, 4)),
                _m(3, WHOLE), _m(4, _note("E", 4, 4)), _m(5, _note("G", 4, 4))])
    d = compare.diff_sequences(compare.load_sequence([a]), compare.load_sequence([b]))
    assert 0 < d["ratio"] < 1
    kinds = [(h["kind"], h["a_count"], h["b_count"]) for h in d["hunks"]]
    assert ("insert", 0, 1) in kinds and ("replace", 1, 1) in kinds
    ins = next(h for h in d["hunks"] if h["kind"] == "insert")
    assert ins["b_content"] == ["r/M"] and "p1 m.3" in ins["b_where"]


def test_sequence_orders_pages_numerically(tmp_path):
    p2 = _write(tmp_path, "b.p002.musicxml", [_m(1, _note("D", 4, 4))])
    p1 = _write(tmp_path, "b.p001.musicxml", [_m(1, _note("C", 4, 4), ATTR)])
    seq = compare.load_sequence([p2, p1])
    assert [(u.page, u.bar) for u in seq] == [(1, 1), (2, 2)]


def test_collect_metrics_and_render(tmp_path):
    f = _write(tmp_path, "b.p001.musicxml", [_m(1, _note("C", 4, 4), ATTR)])
    report = {
        "page_files": [str(f)], "omr_failed_pages": [2],
        "stages": [{"name": "omr", "status": "ok", "detail": "", "duration_s": 3.5},
                   {"name": "normalize", "status": "ok",
                    "detail": "2 measure(s) normalized to their time signature",
                    "duration_s": 0.5}],
        "validation": {"findings": [{"code": "bar-count", "severity": "warn"},
                                    {"code": "bar-count", "severity": "warn"}],
                       "counts": {"error": 0, "warn": 2, "info": 0}},
    }
    m = compare.collect_metrics("homr", report)
    assert m["total_s"] == 4.0 and m["normalize_fixed"] == 2
    assert m["findings_by_code"] == {"bar-count": 2} and m["failed_pages"] == [2]
    assert m["totals"]["notes"] == 1
    skipped = {"engine": "legato2", "status": "skipped", "reason": "not released"}
    md = compare.render_markdown("b", [m, skipped], [], ["note"])
    assert "legato2" in md and "not released" in md and "bar-count" in md
    assert "## Measure-level diff" in md


# ---- runner / layout ---------------------------------------------------------

def test_engine_unavailable(tmp_path):
    cfg = Config(path=tmp_path / "c.toml")
    assert "released" in compare.engine_unavailable(cfg, "legato2")
    assert compare.engine_unavailable(cfg, "homr") is None or \
        "not found" in compare.engine_unavailable(cfg, "homr")


def _fake_stages(monkeypatch, tmp_path, calls):
    def loader(name):
        def run(ctx):
            calls.append((name, ctx.workdir, ctx.shared))
            if name == "ingest":
                ctx.shared.mkdir(parents=True, exist_ok=True)
                ctx.artifacts.source_pdf = ctx.shared / "source.pdf"
                ctx.artifacts.page_count = 1
                ctx.artifacts.prepped_pages = {1: ctx.shared / "prepped" / "p001.png"}
            elif name == "omr":
                assert ctx.artifacts.prepped_pages  # shared ingest products visible
                eng = ctx.config.omr.engine
                step = "C" if eng == "homr" else "D"
                ctx.workdir.joinpath("x.p001.musicxml").write_text(
                    HEAD + _m(1, _note(step, 4, 4), ATTR) + TAIL)
            elif name == "extract":
                ctx.artifacts.musicxml_pages = [ctx.workdir / "x.p001.musicxml"]
            return StageResult(name, "ok")
        return run
    monkeypatch.setattr(pipeline, "load_stage", loader)
    monkeypatch.setattr(compare, "engine_unavailable",
                        lambda cfg, n: "nope" if n == "audiveris" else None)


def test_run_compare_layout_ingest_once_and_skip(monkeypatch, tmp_path):
    calls = []
    _fake_stages(monkeypatch, tmp_path, calls)
    book = tmp_path / "out" / "x"
    ctx = PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=book,
                          config=Config(path=tmp_path / "c.toml"), book_name="x",
                          log=lambda m: None)
    out = compare.run_compare(ctx, ["homr", "audiveris", "copisteria"], log=lambda m: None)
    assert out["ok"]
    assert [c for c in calls if c[0] == "ingest"] == [("ingest", book, book)]
    omr_dirs = [c[1] for c in calls if c[0] == "omr"]
    assert omr_dirs == [book / "engines" / "homr", book / "engines" / "copisteria"]
    assert all(c[2] == book for c in calls if c[0] != "ingest")
    assert (book / "engines" / "homr" / "report.md").exists()
    assert not (book / "engines" / "audiveris").exists()
    status = {m["engine"]: m["status"] for m in out["metrics"]}
    assert status == {"homr": "ran", "audiveris": "skipped", "copisteria": "ran"}
    assert len(out["diffs"]) == 1 and out["diffs"][0]["ratio"] < 1
    md = (book / "compare.md").read_text()
    assert "audiveris** not run: nope" in md and "homr vs copisteria" in md
    assert (book / "compare.json").exists()
    # engine stages stop at validate by default
    assert "convert" not in {c[0] for c in calls}


def test_default_context_shared_is_workdir(tmp_path):
    ctx = PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path / "w",
                          config=Config(path=tmp_path / "c.toml"), book_name="x")
    assert ctx.shared == ctx.workdir


# ---- CLI ---------------------------------------------------------------------

def test_compare_parser():
    a = cli.build_parser().parse_args([
        "compare", "in.pdf", "--engines", "homr,copisteria", "--output", "o",
        "--pages", "1-2", "--force", "--keep-going", "--convert",
        "--transpose-instrument", "horn in f", "--no-deskew"])
    assert a.func is cli.cmd_compare and a.engines == "homr,copisteria"
    assert a.convert and a.force and a.keep_going and a.deskew is False
    assert a.transpose_instrument == "horn in f"
    cfg = cli.apply_run_overrides(Config(path=Path("c.toml")), a)
    assert cfg.stages.transpose.enabled and cfg.stages.transpose.instrument == "horn in f"


def test_compare_requires_engines():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["compare", "in.pdf"])


def test_compare_unknown_engine(capsys):
    rc = cli.main(["compare", "in.pdf", "--engines", "homr,bogus"])
    assert rc == 2 and "bogus" in capsys.readouterr().err
