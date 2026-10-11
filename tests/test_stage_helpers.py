import pytest

from musicocr.config import load_config
from musicocr.errors import StageError
from musicocr.pipeline import PipelineContext
from musicocr.stages._common import disabled, load_score, require_musicxml

_MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
  <part id="P1"><measure number="1">
    <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note>
  </measure></part>
</score-partwise>
"""


def _ctx(tmp_path):
    return PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path,
                           config=load_config(), book_name="book", log=lambda s: None)


def test_require_musicxml_missing_raises(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(StageError, match=r"no \.musicxml at book\.musicxml.*--from extract"):
        require_musicxml(ctx)
    assert ctx.artifacts.musicxml is None


def test_require_musicxml_falls_back_and_sets_artifact(tmp_path):
    ctx = _ctx(tmp_path)
    fallback = tmp_path / "book.musicxml"
    fallback.write_text(_MINIMAL)
    assert require_musicxml(ctx) == fallback
    assert ctx.artifacts.musicxml == fallback


def test_require_musicxml_artifact_takes_precedence(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "book.musicxml").write_text(_MINIMAL)
    other = tmp_path / "other.musicxml"
    other.write_text(_MINIMAL)
    ctx.artifacts.musicxml = other
    assert require_musicxml(ctx) == other
    assert ctx.artifacts.musicxml == other


def test_require_musicxml_missing_artifact_path_raises(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifacts.musicxml = tmp_path / "gone.musicxml"
    with pytest.raises(StageError, match="gone.musicxml"):
        require_musicxml(ctx)


def test_load_score_success(tmp_path):
    p = tmp_path / "ok.musicxml"
    p.write_text(_MINIMAL)
    score = load_score(p)
    assert len(score.flatten().notes) == 1


def test_load_score_parse_failure_is_wrapped(tmp_path):
    p = tmp_path / "bad.musicxml"
    p.write_text("this is not musicxml \x00\x01 garbage")
    with pytest.raises(StageError, match=r"music21 could not parse bad\.musicxml") as ei:
        load_score(p)
    assert "malformed" not in str(ei.value)
    with pytest.raises(StageError, match="OMR output is likely malformed"):
        load_score(p, parse_hint="The OMR output is likely malformed.")


def test_disabled_result():
    res = disabled("normalize", "normalize")
    assert res.status == "skipped"
    assert "[stages.normalize] enabled = false" in res.detail
