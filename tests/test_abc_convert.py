import pytest

from musicocr.abc_convert import AbcConversionError, abc_to_musicxml


def test_abc_roundtrip(tmp_path):
    abc = tmp_path / "p001.abc"
    abc.write_text("M:4/4\nL:1/4\nK:C\nCDEF|GABc|\n")   # no X: header -> added
    xml = tmp_path / "p001.musicxml"
    abc_to_musicxml(abc, xml)
    text = xml.read_text()
    assert "<score-partwise" in text and text.count("<measure") >= 2


def test_empty_abc_is_an_error(tmp_path):
    abc = tmp_path / "e.abc"
    abc.write_text("  \n")
    with pytest.raises(AbcConversionError):
        abc_to_musicxml(abc, tmp_path / "e.musicxml")
