"""Bands, phrases and the union of OCR passes (musicocr/text_words.py)."""
from __future__ import annotations

from musicocr.text_words import (Staff, Word, bands, phrases, stack_tempo_lines, union_words)

SP = 26.0


def W(text, x0, y0, x1=None, y1=None):
    return Word(text, (x0, y0, x1 if x1 is not None else x0 + 10 * len(text), y1 if y1 is not None else y0 + 30))


def test_words_on_one_baseline_join_and_far_ones_split():
    ps = phrases([W("poco", 100, 500), W("accel.", 160, 500), W("sim.", 900, 500)], SP)
    assert [p.text for p in ps] == ["poco accel.", "sim."]


def test_other_lines_stay_apart():
    ps = phrases([W("Sl.", 100, 500), W("Slower", 140, 505), W("122", 100, 560)], SP)
    assert [p.text for p in ps] == ["Sl. Slower", "122"]


def test_words_chain_along_a_slightly_sloped_line():
    ps = phrases([W("Much", 100, 500), W("Faster", 150, 516), W("J=210", 235, 534)], SP)
    assert [p.text for p in ps] == ["Much Faster J=210"]


def test_hyphenated_break_is_joined():
    assert phrases([W("ri-", 100, 500), W("tard.", 140, 500)], SP)[0].text == "ritard."


def test_a_metronome_mark_ends_its_phrase():
    ps = phrases([W("(J=", 100, 500), W("102)", 150, 500), W("Choir,", 200, 505), W("Cel", 260, 505)], SP)
    assert [p.text for p in ps] == ["(J= 102)", "Choir, Cel"]


def test_metronome_line_under_a_tempo_word_is_stacked():
    ps = phrases([W("Sl. Slower", 100, 500, 330, 530), W("J = 122", 105, 540, 240, 570)], SP)
    out = stack_tempo_lines(ps, SP)
    assert [p.text for p in out] == ["Sl. Slower J = 122"]


def test_union_prefers_the_reading_the_lexicon_understands():
    a = [W("(EZI=P)", 100, 100, 220, 130)]
    b = [W("(d=123)", 102, 101, 218, 131)]
    assert union_words([a, b])[0].text == "(d=123)"
    assert union_words([b, a])[0].text == "(d=123)"
    far = [W("marc.", 900, 100)]
    assert len(union_words([a, b, far])) == 2


def test_union_merges_boxes_that_mostly_overlap():
    out = union_words([[W("d", 1934, 193, 1982, 299)], [W("d=210", 1944, 185, 2128, 298)]])
    assert [w.text for w in out] == ["d=210"]               # the fragment is inside the fuller reading
    out = union_words([[W("Faster", 152, 2164, 341, 2236)], [W("Faster", 152, 2174, 337, 2241)]])
    assert len(out) == 1


def test_bands_cover_over_under_and_whole_gap():
    staves = [Staff(300, 404, 30), Staff(654, 758, 30), Staff(2000, 2104, 30)]
    bs = bands(staves, 4000)
    assert any(y0 < 300 for y0, _ in bs)                          # over the first staff
    assert any(y0 >= 404 and y1 <= 654 and y1 - y0 > 200 for y0, y1 in bs)    # the tight gap, whole
    assert not any(y0 <= 760 and y1 >= 1900 for y0, y1 in bs)    # the wide gap is not one band
    assert all(y1 <= 4000 for _, y1 in bs)
