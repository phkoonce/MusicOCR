"""Round 5: auto staff rank for score pages inside part books, multi-rests extended to what the other parts agree on."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from musicocr.assemble.combine import _Share, _extended_rests
from musicocr.batch import parse_spec
from musicocr.scoreops.staffpick import is_score_page, part_number


def test_part_number_and_score_page_rule():
    assert part_number("88. Aequale - Horn 3") == 3 and part_number("Timpani") == 0
    assert is_score_page([[0, 1, 2], [3, 4, 5]], 6)
    assert not is_score_page([[0, 1, 2], [3], [4]], 5)          # stray joined group, the rest single staves
    assert not is_score_page([[0, 1], [2, 3, 4]], 5)            # sizes differ
    assert not is_score_page([[0], [1]], 2)                      # no joined staves at all


def test_batch_staff_rank_auto(tmp_path):
    spec = parse_spec({"parts": [
        {"id": "a", "label": "89. Canzonet - Horn 2", "first_page": 1, "last_page": 2, "source_pdf": "x.pdf", "staff_rank": "auto"},
        {"id": "b", "label": "89. Canzonet - Horn 3", "first_page": 1, "last_page": 2, "source_pdf": "x.pdf", "staff_rank": 1}]},
        "t", tmp_path)
    a, b = spec.parts
    assert (a.staff_rank, a.staff_rank_auto) == (2, True)
    assert (b.staff_rank, b.staff_rank_auto) == (1, False)


def _rest_bar():
    return ET.fromstring("<measure><note><rest measure='yes'/><duration>4</duration></note></measure>"), 1


def _note_bar():
    return ET.fromstring("<measure><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure>"), 1


def test_inserted_bars_after_a_rest_where_the_others_agree_are_a_lost_count():
    ref = _Share({b: 1.0 for b in range(1, 12)})
    ref.n = {b: 3 for b in range(1, 12)}
    src = [_rest_bar(), _note_bar()]
    # a multi-rest read short: slots 2..4 come from nothing, the played bar follows
    slots = [0, None, None, None, 1]
    assert _extended_rests(ref, 1, slots, src) == [2, 3, 4]
    # the other parts play there: no vote
    ref2 = _Share({b: 0.5 for b in range(1, 12)})
    ref2.n = {b: 3 for b in range(1, 12)}
    assert _extended_rests(ref2, 1, slots, src) == []
    # a single other part is no majority
    ref3 = _Share({b: 1.0 for b in range(1, 12)})
    ref3.n = {b: 1 for b in range(1, 12)}
    assert _extended_rests(ref3, 1, slots, src) == []
    # after a played bar the lost bar is not "the rest of a multi-rest"
    assert _extended_rests(ref, 1, [1, None, 0], src) == []


def test_tacet_tail_is_extended_not_doubted():
    ref = _Share({})
    src = [_note_bar(), _rest_bar()]
    slots = [0, 1] + [None] * 20          # a part that plays 1 bar, then rests to the end of a 22-bar piece
    assert _extended_rests(ref, 1, slots, src) == []                       # not the final page: no claim
    assert _extended_rests(ref, 1, slots, src, final=True) == list(range(3, 23))
    assert _extended_rests(ref, 1, [0, 1] + [None] * 3, src, final=True) == []        # a short tail is a lost bar
    assert _extended_rests(ref, 1, [1, 0] + [None] * 20, src, final=True) == []       # it ends on notes: not tacet


def test_segments_open_last_system_may_span_any_length():
    from musicocr.assemble import voltas as vt
    # 6 systems read as 8, 4, 5, 7, 5, 1 bars; the last one is a rest to the end of a 296-bar piece
    breaks = [0, 8, 12, 17, 24, 29]
    anchors = {2: 18, 3: 24, 4: 31, 5: 36}
    assert len(vt.segments(30, 1, 296, breaks, anchors)) == 1                              # the tail is "too long": all ignored
    cuts = vt.segments(30, 1, 296, breaks, anchors, open_last=True)
    assert [(c[2], c[3]) for c in cuts] == [(1, 18), (18, 24), (24, 31), (31, 36), (36, 297)]


def _add(tmp_path, dets, texts):
    import json
    from musicocr.text.page_text import add_missing_multirest_dets
    d, t = tmp_path / "p.dets_v7_1600.json", tmp_path / "p.texts.json"
    d.write_text(json.dumps(dets))
    t.write_text(json.dumps(texts))
    n = add_missing_multirest_dets(d, t)
    return n, json.loads(d.read_text())


def test_digit_at_the_start_of_a_system_is_a_bar_number(tmp_path):
    meas = lambda x0, x1: {"cls": "measure", "conf": 0.99, "xyxy": [x0, 100, x1, 160]}  # noqa: E731   (space 15 px)
    # a bar number "65" 3 spaces from the system's left edge over an undetected first bar: not a count
    n, _ = _add(tmp_path, [meas(0, 400), meas(400, 1000)], [{"text": "65", "xyxy": [40, 30, 90, 75]}])
    assert n == 0
    # the same digit centred over a bar that follows the clef is a count
    clef = {"cls": "clef_G2", "conf": 1.0, "xyxy": [5, 90, 40, 190]}
    n, dets = _add(tmp_path, [clef, meas(400, 1000)], [{"text": "6", "xyxy": [680, 30, 720, 75]}])
    assert n == 1 and any(d["cls"] == "multiRest" for d in dets)


def test_block_under_a_digit_gets_one_multirest_not_two(tmp_path):
    meas = lambda x0, x1: {"cls": "measure", "conf": 0.99, "xyxy": [x0, 100, x1, 160]}  # noqa: E731   (space 15 px)
    clef = {"cls": "clef_G2", "conf": 1.0, "xyxy": [5, 90, 40, 190]}
    block = {"cls": "barLine_single", "conf": 0.4, "xyxy": [490, 118, 510, 142]}      # the thick H-bar read as a weak bar line
    n, dets = _add(tmp_path, [clef, meas(0, 1000), block], [{"text": "4", "xyxy": [480, 40, 520, 90]}])
    assert sum(1 for d in dets if d["cls"] == "multiRest") == 1 and n == 1


def test_scorecard_counts_raw_bars_from_the_page_files(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import score_alignment as sa
    key = "K_p1"
    d = tmp_path / key
    d.mkdir()

    def xml(body):
        return ("<score-partwise><part-list><score-part id='P1'><part-name>Horn</part-name></score-part></part-list>"
                f"<part id='P1'>{body}</part></score-partwise>")
    rest = "<note><rest measure='yes'/><duration>4</duration></note>"
    one = ("<measure number='1'><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>"
           "<measure-style><multiple-rest>5</multiple-rest></measure-style></attributes>" + rest + "</measure>")
    two = f"<measure number='1'>{rest}</measure><measure number='2'>{rest}</measure>"
    (d / f"{key}.p001.musicxml").write_text(xml(one))
    (d / f"{key}.p002.musicxml").write_text(xml(two))
    (d / f"{key}.musicxml").write_text(xml(one + two))       # the merged file: the tag is kept here by luck, but not always
    assert len(sa.read_raw_part(tmp_path, key)["strict"]) == 7
