"""Tests for musicocr.stages.extract (synthetic MusicXML / .mxl, no OMR engines)."""
from __future__ import annotations

import zipfile
from dataclasses import replace

import pytest

from musicocr.config import load_config
from musicocr.errors import StageError
from musicocr.pipeline import PipelineContext
from musicocr.stages import extract
from musicocr.stages.extract import _unzip_mxl

_NOTE = ("<note><pitch><step>C</step><octave>4</octave></pitch>"
         "<duration>4</duration><type>whole</type></note>")


def _xml(measures=1, name="Voice"):
    ms = "".join(
        f'<measure number="{i}">'
        + ('<attributes><divisions>1</divisions><time><beats>4</beats>'
           '<beat-type>4</beat-type></time></attributes>' if i == 1 else "")
        + _NOTE + "</measure>"
        for i in range(1, measures + 1))
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1">'
            f'<part-list><score-part id="P1"><part-name>{name}</part-name></score-part>'
            f'</part-list><part id="P1">{ms}</part></score-partwise>')


def _mxl(path, xml, inner="score.xml"):
    container = ('<?xml version="1.0"?><container><rootfiles>'
                 f'<rootfile full-path="{inner}"/></rootfiles></container>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr(inner, xml)
    return path


def _ctx(tmp_path, merge=True):
    cfg = load_config()
    cfg = replace(cfg, omr=replace(cfg.omr, merge_pages=merge))
    return PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path, config=cfg,
                           book_name="book", log=lambda s: None)


def _measure_count(path):
    from music21 import converter, stream
    sc = converter.parse(str(path))
    return len(list(sc.parts[0].getElementsByClass(stream.Measure)))


def test_unzip_mxl_extracts_rootfile(tmp_path):
    src = _mxl(tmp_path / "a.mxl", _xml(), inner="sub/inner.xml")
    dest = _unzip_mxl(src, tmp_path / "out.musicxml")
    assert dest.read_text() == _xml()


def test_unzip_mxl_without_rootfile_raises(tmp_path):
    p = tmp_path / "bad.mxl"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("META-INF/container.xml", "<container><rootfiles/></container>")
    with pytest.raises(StageError, match="no rootfile"):
        _unzip_mxl(p, tmp_path / "o.musicxml")


def test_no_inputs_raises(tmp_path):
    with pytest.raises(StageError, match="no .mxl files found"):
        extract.run(_ctx(tmp_path))


def test_fallback_scans_workdir_but_skips_experiments(tmp_path):
    _mxl(tmp_path / "found.mxl", _xml())
    (tmp_path / "experiments").mkdir()
    _mxl(tmp_path / "experiments" / "ignored.mxl", _xml(5))
    ctx = _ctx(tmp_path)
    res = extract.run(ctx)
    assert res.status == "ok"
    assert _measure_count(tmp_path / "book.musicxml") == 1


def test_whole_book_single_mxl(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifacts.mxl_files = [_mxl(tmp_path / "w.mxl", _xml(3))]
    res = extract.run(ctx)
    assert res.detail == "book.musicxml"
    assert ctx.artifacts.musicxml == tmp_path / "book.musicxml"
    assert ctx.artifacts.musicxml_pages == []
    assert "<score-partwise" in ctx.artifacts.musicxml.read_text()


def test_whole_book_multiple_movements_merge(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifacts.mxl_files = [_mxl(tmp_path / "a.mxl", _xml(2)),
                               _mxl(tmp_path / "b.mxl", _xml(3))]
    res = extract.run(ctx)
    assert "(merged)" in res.detail
    assert ctx.artifacts.extract_merged is True
    assert [p.name for p in ctx.artifacts.musicxml_pages] == [
        "book.mvt1.musicxml", "book.mvt2.musicxml"]
    assert _measure_count(ctx.artifacts.musicxml) == 5


def test_whole_book_multiple_movements_no_merge(tmp_path):
    ctx = _ctx(tmp_path, merge=False)
    ctx.artifacts.mxl_files = [_mxl(tmp_path / "a.mxl", _xml(2)),
                               _mxl(tmp_path / "b.mxl", _xml(3))]
    res = extract.run(ctx)
    assert res.detail == "2 movements (not merged)"
    assert ctx.artifacts.extract_merged is False
    assert ctx.artifacts.musicxml == tmp_path / "book.mvt1.musicxml"
    assert not (tmp_path / "book.musicxml").exists()


def test_per_page_single_page(tmp_path):
    ctx = _ctx(tmp_path)
    p = tmp_path / "pages" / "p003.musicxml"
    p.parent.mkdir()
    p.write_text(_xml(2))
    ctx.artifacts.mxl_by_page = {3: [p]}
    res = extract.run(ctx)
    assert res.detail == "book.musicxml (1 page)"
    assert [f.name for f in ctx.artifacts.musicxml_pages] == ["book.p003.musicxml"]
    assert (tmp_path / "book.musicxml").read_text() == p.read_text()


def test_per_page_merge_mixed_mxl_and_plain(tmp_path):
    ctx = _ctx(tmp_path)
    plain = tmp_path / "plain.musicxml"
    plain.write_text(_xml(2))
    ctx.artifacts.mxl_by_page = {2: [plain], 1: [_mxl(tmp_path / "z.mxl", _xml(1))]}
    res = extract.run(ctx)
    assert "merged 2/2 page files" in res.detail
    assert [f.name for f in ctx.artifacts.musicxml_pages] == [
        "book.p001.musicxml", "book.p002.musicxml"]
    assert ctx.artifacts.extract_merged is True
    assert ctx.artifacts.merge_skipped == []
    assert _measure_count(tmp_path / "book.musicxml") == 3


def test_per_page_multiple_files_on_one_page_get_suffix(tmp_path):
    ctx = _ctx(tmp_path, merge=False)
    a, b = tmp_path / "a.musicxml", tmp_path / "b.musicxml"
    a.write_text(_xml())
    b.write_text(_xml())
    ctx.artifacts.mxl_by_page = {1: [a, b]}
    extract.run(ctx)
    assert [f.name for f in ctx.artifacts.musicxml_pages] == [
        "book.p001.1.musicxml", "book.p001.2.musicxml"]


def test_per_page_no_merge_keeps_pages_separate(tmp_path):
    ctx = _ctx(tmp_path, merge=False)
    a, b = tmp_path / "a.musicxml", tmp_path / "b.musicxml"
    a.write_text(_xml())
    b.write_text(_xml())
    ctx.artifacts.mxl_by_page = {1: [a], 2: [b]}
    res = extract.run(ctx)
    assert "kept separate" in res.detail
    assert ctx.artifacts.extract_merged is False
    assert ctx.artifacts.musicxml == tmp_path / "book.p001.musicxml"
    assert not (tmp_path / "book.musicxml").exists()


def test_per_page_malformed_page_skipped_in_merge(tmp_path):
    ctx = _ctx(tmp_path)
    good1, bad, good2 = (tmp_path / n for n in ("g1.musicxml", "bad.musicxml", "g2.musicxml"))
    good1.write_text(_xml(1))
    bad.write_text("<not-musicxml")
    good2.write_text(_xml(2))
    ctx.artifacts.mxl_by_page = {1: [good1], 2: [bad], 3: [good2]}
    res = extract.run(ctx)
    assert "merged 2/3" in res.detail and "1 unreadable" in res.detail
    assert [p.rsplit("/", 1)[-1] for p in ctx.artifacts.merge_skipped] == ["book.p002.musicxml"]
    assert _measure_count(tmp_path / "book.musicxml") == 3


def test_per_page_all_unreadable_merge_produces_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    a, b = tmp_path / "a.musicxml", tmp_path / "b.musicxml"
    a.write_text("garbage")
    b.write_text("garbage")
    ctx.artifacts.mxl_by_page = {1: [a], 2: [b]}
    res = extract.run(ctx)
    assert "merge produced nothing" in res.detail
    assert ctx.artifacts.extract_merged is False
    assert ctx.artifacts.musicxml == tmp_path / "book.p001.musicxml"
    assert len(ctx.artifacts.merge_skipped) == 2
