from pathlib import Path

import pytest

from musicocr.collection import combine as ccombine
from musicocr.collection.batch import batch_toml, clean_title
from musicocr.collection.spec import ConfigError, parse_spec, piece_range

DATA = {
    "name": "demo", "title": "Demo pieces", "key_prefix": "D_", "instrument": "horn in f",
    "part_name": "Horn", "part_id_prefix": "h", "bar_numbers": False, "toc_pages": "2-4", "first_page": 5,
    "books": [{"id": "1", "pdf": "/b/H1.pdf"}, {"id": "2", "pdf": "/b/H2.pdf", "old_bass_clef": True}],
    "overrides": {"2": {"title": "Fixed", "composer": "Real Composer"}},
}
TABLE = {"entries": [{"num": 1, "title": "Wor- ter und Töne. ", "composer": "Komponist: Name"},
                     {"num": 2, "title": "garbled", "composer": "ocr junk"}],
         "pdfs": {"1": {"1": "/p/h1_1.pdf", "2": "/p/h1_2.pdf"}, "2": {"1": "/p/h2_1.pdf", "2": "/p/h2_2.pdf"}}}


def _spec():
    return parse_spec(DATA, Path("demo/collection.toml"))


def test_spec_parsing_and_names():
    spec = _spec()
    assert spec.toc_pages == (2, 4) and spec.first_page == 5 and not spec.bar_numbers
    assert spec.part_id(3, spec.books[1]) == "p03_h2" and spec.book_prefix(spec.books[0]) == "D_H1"
    assert spec.books[1].options == {"old_bass_clef": True} and spec.overrides[2]["title"] == "Fixed"
    assert spec.batch_path.name == "demo.toml" and spec.pieces_json.name == "pieces.json"


def test_spec_requires_core_keys():
    with pytest.raises(ConfigError):
        parse_spec({"name": "x"}, Path("c.toml"))
    with pytest.raises(ConfigError):
        parse_spec({"name": "x", "title": "t", "books": [{"id": "1"}]}, Path("c.toml"))


def test_piece_range():
    assert piece_range(None, 3) == [1, 2, 3] and piece_range("2", 9) == [2] and piece_range("2-4", 9) == [2, 3, 4]


def test_clean_title_rejoins_hyphenation_and_trims():
    assert clean_title("Wor- ter und Töne. ") == "Worter und Töne"


def test_batch_toml_one_part_per_piece_and_book():
    text = batch_toml(_spec(), TABLE, page_count=lambda pdf: 2)
    assert text.startswith('title = "Demo pieces"\nkey_prefix = "D_"\nbar_numbers = false\n')
    assert text.count("[[parts]]") == 4
    assert 'id = "p01_h1"' in text and 'label = "1. Worter und Töne - Horn 1"' in text
    assert 'composer = ""' in text                              # a "Komponist:" column heading is dropped
    assert 'title = "Fixed"' in text and 'composer = "Real Composer"' in text      # the override wins
    assert text.count("old_bass_clef = true") == 2             # only book 2's parts
    assert 'last_page = 2' in text and 'instrument = "horn in f"' in text


def test_parse_report():
    text = ("score: 4 parts, 95 bars (last-page totals {89: 1, 95: 3})\n"
            "  89 bars read for 95 printed\n  bar 3: LENGTH 2.5 beats, expected 3\n  bar 9: LENGTH 1 beats, expected 3\n")
    assert ccombine.parse_report(text) == ("95", "{89: 1, 95: 3}", 1, 2)
    assert ccombine.parse_report("")[0] == "?"


def test_summary_markdown():
    md = ccombine.summary_markdown(_spec(), [ccombine.Row(1, "95", "{95: 4}", 0, 2, True),
                                             ccombine.Row(2, "?", "", 0, 0, False)])
    assert "| 1 | 95 | {95: 4} | 0 | 2 | yes |" in md and "| 2 | ? |  | 0 | 0 | NO |" in md
    assert md.startswith("# Demo pieces -- combine summary")
