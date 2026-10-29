"""Tests for musicocr.report (hand-built context + stage results)."""
from __future__ import annotations

import json
from dataclasses import replace

from musicocr.config import load_config
from musicocr.errors import StageResult
from musicocr.pipeline import PipelineContext
from musicocr.report import _md, build_report, write_reports


def _ctx(tmp_path, **omr):
    cfg = load_config()
    cfg = replace(cfg, omr=replace(cfg.omr, **omr))
    return PipelineContext(input_pdf=tmp_path / "in" / "My Book.pdf", workdir=tmp_path,
                           config=cfg, book_name="My_Book", log=lambda s: None)


def _results():
    return [StageResult("ingest", "ok", "My_Book, 3 page(s)", duration_s=0.123),
            StageResult("omr", "failed", "boom", duration_s=1.5)]


def test_build_report_basic_fields(tmp_path):
    ctx = _ctx(tmp_path, engine="audiveris", profile="default")
    ctx.artifacts.page_count = 3
    ctx.artifacts.musicxml = tmp_path / "My_Book.musicxml"
    ctx.artifacts.outputs = {"pdf": tmp_path / "a.pdf", "mxl": [tmp_path / "x", tmp_path / "y"]}
    ctx.artifacts.merge_skipped = [str(tmp_path / "My_Book.p002.musicxml")]
    r = build_report(ctx, _results())
    assert r["book"] == "My_Book"
    assert r["page_count"] == 3
    assert r["omr_engine"] == "audiveris"
    assert r["stages"][0] == {"name": "ingest", "status": "ok",
                              "detail": "My_Book, 3 page(s)", "duration_s": 0.12}
    assert r["outputs"]["pdf"] == str(tmp_path / "a.pdf")
    assert r["outputs"]["mxl"] == [str(tmp_path / "x"), str(tmp_path / "y")]
    assert r["outputs"]["musicxml"] == str(tmp_path / "My_Book.musicxml")
    assert r["merge_skipped"] == ["My_Book.p002.musicxml"]
    assert r["omr_project"] == "" and r["omr_log"] == ""


def test_ok_report_markdown(tmp_path):
    ctx = _ctx(tmp_path, engine="homr")
    ctx.artifacts.page_count = 2
    ctx.artifacts.validation = {
        "findings": [], "counts": {"error": 0, "warn": 0, "info": 0},
        "stats": {"parts": 1, "measures_per_part": [4], "notes": 8, "rests": 1,
                  "tuplets": 0, "accidentals": 2}}
    ctx.artifacts.musicxml = tmp_path / "My_Book.musicxml"
    md = _md(build_report(ctx, [StageResult("ingest", "ok", "fine")]), tmp_path)
    assert md.startswith("# MusicOCR report — My_Book")
    assert "- **Pages:** 2" in md
    assert "- **OMR engine:** `homr`" in md
    assert "OMR profile" not in md            # audiveris-only
    assert "Partial transcription" not in md
    assert "| ingest | ok | fine | 0.0s |" in md
    assert "- **musicxml:** `My_Book.musicxml`" in md   # relative to workdir
    assert "- Notes: 8, rests: 1" in md
    assert "_no findings_" in md
    assert "QA" not in md


def test_audiveris_profile_and_constants_line(tmp_path):
    ctx = _ctx(tmp_path, engine="audiveris", profile="default", extra_constants=["a=1"])
    md = _md(build_report(ctx, []), tmp_path)
    assert "`default` + constants `['a=1']`" in md


def test_failed_pages_and_merge_off_and_skipped_banner(tmp_path):
    ctx = _ctx(tmp_path, engine="homr", merge_pages=False)
    ctx.artifacts.page_count = 5
    ctx.artifacts.omr_failed_pages = [2, 4]
    ctx.artifacts.merge_skipped = ["/x/My_Book.p003.musicxml"]
    ctx.artifacts.musicxml_pages = [tmp_path / f"My_Book.p00{i}.musicxml" for i in (1, 2, 3)]
    md = _md(build_report(ctx, _results()), tmp_path)
    assert "Partial transcription" in md
    assert "failed on 2 page(s): [2, 4] of 5" in md
    assert "`--pages 2,4 --profile <name> --force`" in md
    assert "Page merge:** off" in md
    assert "1 page file(s) too malformed" in md and "My_Book.p003.musicxml" in md
    assert "per-page MusicXML:** 3 files" in md
    assert "| omr | failed | boom | 1.5s |" in md


def test_no_outputs_message(tmp_path):
    md = _md(build_report(_ctx(tmp_path), []), tmp_path)
    assert "_none produced_" in md


def test_findings_sorted_by_severity_then_location(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifacts.validation = {
        "findings": [
            {"severity": "info", "code": "i1", "message": "m-info", "part": "A", "measure": 1},
            {"severity": "warn", "code": "w2", "message": "m-w2", "part": "A", "measure": 5},
            {"severity": "error", "code": "e1", "message": "m-err", "part": "B", "measure": 2},
            {"severity": "warn", "code": "w1", "message": "m-w1", "part": "A", "measure": 3},
            {"severity": "error", "code": "global", "message": "no notes"},
        ],
        "counts": {"error": 2, "warn": 2, "info": 1}, "stats": {}}
    md = _md(build_report(ctx, []), tmp_path)
    assert "- Findings: 2 error, 2 warn, 1 info" in md
    order = [md.index(f"| {c} |") for c in ("global", "e1", "w1", "w2", "i1")]
    assert order == sorted(order)
    assert "| 🔴 error | e1 | B m.2 | m-err |" in md
    assert "| 🟡 warn | w1 | A m.3 | m-w1 |" in md
    assert "| 🔵 info | i1 | A m.1 | m-info |" in md
    assert "| 🔴 error | global |  | no notes |" in md


def test_qa_section(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifacts.qa_pages = {"src": [str(tmp_path / "qa" / "s1.png")],
                              "omr": [str(tmp_path / "qa" / "o1.png"),
                                      str(tmp_path / "qa" / "o2.png")]}
    md = _md(build_report(ctx, []), tmp_path)
    assert "## QA" in md
    assert "![source page 1](qa/s1.png)" in md
    assert "![rendered page 2](qa/o2.png)" in md
    assert "![source page 2]" not in md


def test_write_reports_writes_json_and_md(tmp_path):
    ctx = _ctx(tmp_path)
    jp, mp = write_reports(ctx, _results())
    assert (jp, mp) == (tmp_path / "report.json", tmp_path / "report.md")
    data = json.loads(jp.read_text())
    assert data["book"] == "My_Book" and len(data["stages"]) == 2
    assert mp.read_text().endswith("\n")
