"""A multi-piece book scanned as a score (all parts on every page): contents reading, titles, headings, part names."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace

from musicocr.collection.batch import split_credit
from musicocr.collection.split import Entry, _heading, fill_number_gaps
from musicocr.scoreops.movements import find_headings
from musicocr.scoreops.transpose import transpose_score
from musicocr.text.layer import is_credit_line


def _word(text, x0, y0, x1, y1):
    return {"text": text, "xyxy": [x0, y0, x1, y1]}


def test_numbers_missing_from_the_contents_are_still_searched():
    got = fill_number_gaps([Entry(2, "Two", ""), Entry(4, "Four", "")])
    assert [(e.num, e.title) for e in got] == [(1, ""), (2, "Two"), (3, ""), (4, "Four")]


def test_heading_found_when_a_margin_word_shares_its_line():
    line = {"text": "Partitur 1. Concerto grosso", "x0": 80, "y0": 60, "y1": 90,
            "words": [_word("Partitur", 80, 60, 180, 90), _word("1.", 420, 60, 450, 90),
                      _word("Concerto grosso", 460, 60, 700, 90)]}
    assert _heading([line], 0, Entry(1, "", ""), 1000, by_title=False) == 0


def test_version_credits_move_from_the_title_to_the_composer():
    assert split_credit("Deck the Halls Version A (Arr. X)", "Version B (Arr. Y)") == \
        ("Deck the Halls", "Version A (Arr. X) Version B (Arr. Y)")
    assert split_credit("Engel des Himmels (Mennyböl az angyal)", "(Arr. X)") == \
        ("Engel des Himmels (Mennyböl az angyal)", "(Arr. X)")


def test_version_headings_are_sections():
    staves = [SimpleNamespace(top=300, bottom=340, sp=10)]
    for words in ([_word("Version B", 440, 200, 560, 220)],
                  [_word("Version", 430, 200, 520, 220), _word("B", 530, 200, 560, 220)]):
        (h,) = find_headings(words, staves, 1000)
        assert (h.number, h.heading, h.staff) == (2, "Version B", 0)


def test_four_horns_of_a_score_are_numbered():
    root = ET.fromstring(
        "<score-partwise><part-list>" + "".join(
            f"<score-part id='P{i}'><part-name>Part {i}</part-name></score-part>" for i in (1, 2))
        + "</part-list>" + "".join(
            f"<part id='P{i}'><measure><attributes><divisions>1</divisions></attributes></measure></part>"
            for i in (1, 2)) + "</score-partwise>")
    transpose_score(root, instrument="horn in f", mode="written")
    names = [sp.findtext("part-name") for sp in root.iter("score-part")]
    assert len(set(names)) == 2 and names[0].endswith(" 1") and names[1].endswith(" 2")


def test_credit_line_is_flush_right():
    assert is_credit_line((2184, 397, 2689, 439), 2895)
    assert not is_credit_line((400, 397, 900, 439), 2895)


def test_marks_pushed_down_by_low_notes_stay_with_the_part_above():
    from musicocr.engines.copisteria.run import reassign_marks
    clear = [(1.0, 6.0, "a"), (1.5, 5.0, "b"), (1.2, 6.0, "c")]
    low = (4.6, 4.0, "hn2")                       # nearer the staff below, but not much
    assert reassign_marks(clear + [low]) == ["a", "b", "c", "hn2"]
    assert (reassign_marks(clear + [(6.0, 1.0, "next")])) == ["a", "b", "c"]
    above = [(6.0, 1.0, "x"), (5.0, 1.5, "y"), (4.6, 4.0, "z")]     # a score that prints marks above: untouched
    assert reassign_marks(above) == []


def test_bar_all_parts_leave_short_at_a_final_barline_is_incomplete_not_a_gap():
    from musicocr.engines.copisteria.fixes import keep_incomplete_bars

    note = "<note><pitch><step>C</step><octave>4</octave></pitch><duration>3</duration></note>"
    gap = "<forward><duration>1</duration></forward>"
    final = '<barline location="right"><bar-style>light-heavy</bar-style></barline>'

    def root(second_part_bar):
        body = "".join(f'<part id="P{k}"><measure number="1">{b}</measure></part>'
                       for k, b in enumerate([note + gap + final, second_part_bar], 1))
        return ET.fromstring(f"<score-partwise>{body}</score-partwise>")

    both = root(note + gap + final)
    assert keep_incomplete_bars(both, log=lambda s: None) == 1 and both.find(".//forward") is None
    one = root(note + "<note><rest/><duration>1</duration></note>" + final)      # the other part is whole: a real gap
    assert keep_incomplete_bars(one, log=lambda s: None) == 0 and one.find(".//forward") is not None
