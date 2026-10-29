"""Scene (cue) handling: upbeats, the until-box text, multi-rests written as one long measure."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.assemble.combine import read_page
from musicocr.assemble.scenes import Scene, concat_scenes, drop_pickup, pickup_of, prepend_pickup, read_until_box

ATTR = "<attributes><divisions>2</divisions><key><fifths>1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time></attributes>"


def _m(body: str, number: int = 1) -> ET.Element:
    return ET.fromstring(f"<measure number='{number}'>{body}</measure>")


def _page(tmp_path, *measures: str):
    p = tmp_path / "p.musicxml"
    p.write_text(f"<score-partwise><part id='P1'>{''.join(measures)}</part></score-partwise>")
    return p


def test_read_until_box_repairs_lookalikes():
    assert read_until_box("junk 0:16 until 1M4 more") == "0:16 until 1M4"
    assert read_until_box("0 : 09 until ЗМ17-18") == "0:09 until 3M17-18"
    assert read_until_box("no box here") == ""


def test_pickup_of_needs_most_parts_and_a_short_bar():
    pick = (_m(ATTR + "<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration></note>"), 2)
    full = (_m(ATTR + "<note><pitch><step>C</step><octave>4</octave></pitch><duration>8</duration></note>"), 2)
    assert pickup_of([pick, pick, full]) == [pick, pick, None]
    assert pickup_of([pick, full, full]) == []
    rest = (_m(ATTR + "<note><rest measure='yes'/><duration>2</duration></note>"), 2)
    assert pickup_of([rest, rest]) == []            # a whole-bar rest is not an upbeat


def test_drop_pickup_hands_its_declarations_to_the_next_bar():
    pk = (_m(ATTR + "<note><rest/><duration>2</duration></note>"), 2)
    nxt = (_m("<note><rest measure='yes'/><duration>8</duration></note>", 2), 2)
    left = drop_pickup([pk, nxt])
    assert left == [nxt] and nxt[0].find("attributes/key/fifths").text == "1"


def test_prepend_pickup_moves_attributes_and_marks_it_implicit():
    score = ET.fromstring("<score-partwise><part id='P1'><measure number='1'>" + ATTR
                          + "<note><rest measure='yes'/><duration>10080</duration></note></measure></part></score-partwise>")
    pk = (_m("<note><rest/><duration>2</duration></note>"), 2)
    prepend_pickup(score, [pk])
    first, second = score.find("part").findall("measure")
    assert first.get("implicit") == "yes" and first.find("attributes") is not None and second.find("attributes") is None


def test_concat_scenes_labels_and_numbers(tmp_path):
    def sc(n):
        return ET.fromstring("<score-partwise><part-list/><part id='P1'>" + "".join(
            f"<measure number='{i}'><note><rest measure='yes'/><duration>1</duration></note></measure>" for i in range(1, n + 1))
            + "</part></score-partwise>")
    out = concat_scenes([sc(2), sc(1)], [Scene("1M0", "Logo", 1, 1, "0:09 until 1M1"), Scene("1M1", "Racing", 2, 3)], "T")
    ms = out.find("part").findall("measure")
    assert [m.get("number") for m in ms] == ["1", "2", "1"]                  # bar numbers restart in each scene
    assert [r.text for r in out.iter("rehearsal")] == ["1M0  Logo", "1M1  Racing"]
    assert [w.text for w in out.iter("words")] == ["0:09 until 1M1"]
    assert ms[2].find("print").get("new-system") == "yes"


def test_multirest_written_as_one_long_measure_is_one_bar_again(tmp_path):
    first = _m(ATTR.replace("<fifths>1", "<fifths>0") + "<note><rest measure='yes'/><duration>8</duration></note>")
    run = _m("<attributes><measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>"
             "<note><rest measure='yes'/><duration>24</duration></note>", 2)
    ms = read_page(_page(tmp_path, ET.tostring(first, encoding="unicode"), ET.tostring(run, encoding="unicode")))
    assert [sum(int(d.text) for d in m.findall("note/duration")) for m, _ in ms] == [8, 8, 8, 8]
