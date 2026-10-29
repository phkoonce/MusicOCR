"""musicocr.assemble.alignment shared building blocks on tiny synthetic MusicXML."""
from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from musicocr.assemble.alignment import AlignmentError
from musicocr.assemble.alignment.build import (add_attributes, apply_marks, check_length, copy_notes,
                                      direction, is_rest_only, measure_total, rest_measure)
from musicocr.assemble.alignment.spec import DIV, bar_len, load_spec
from musicocr.assemble.alignment.units import units


def _m(xml: str) -> ET.Element:
    return ET.fromstring(f"<measure number='1'>{xml}</measure>")


NOTE = "<note><pitch><step>C</step><octave>4</octave></pitch><duration>{d}</duration></note>"
REST = "<note><rest/><duration>{d}</duration></note>"


def test_measure_total_ignores_chords_and_grace():
    m = _m(NOTE.format(d=4) + "<note><chord/><pitch><step>E</step><octave>4</octave></pitch>"
           "<duration>4</duration></note><note><grace/><pitch><step>D</step><octave>4</octave>"
           "</pitch></note>" + REST.format(d=2))
    assert measure_total(m) == 6


def test_is_rest_only():
    assert is_rest_only(_m(REST.format(d=4)))
    assert is_rest_only(_m(""))                       # empty measure
    assert not is_rest_only(_m(REST.format(d=2) + NOTE.format(d=2)))


def test_bar_len_meter_and_pickup():
    spec = SimpleNamespace(meter=lambda b: (6, 8) if b > 1 else (4, 4))
    assert bar_len(spec, 2) == 6 * DIV * 4 // 8
    spec.FIRST_BAR_BEATS = 1
    assert bar_len(spec, 1) == DIV
    assert bar_len(spec, 2) == 3 * DIV


def test_rest_measure_full_and_partial():
    full = rest_measure(5, 4 * DIV)
    assert full.get("number") == "5"
    assert full.find("note/rest").get("measure") == "yes"
    assert full.findtext("note/duration") == str(4 * DIV)
    assert full.find("note/type") is None
    part = rest_measure(1, DIV, full=False)
    assert part.find("note/rest").get("measure") is None
    assert part.findtext("note/type") == "quarter"
    assert is_rest_only(full) and measure_total(part) == DIV


def test_add_attributes_selective_and_ordered():
    m = ET.Element("measure")
    assert add_attributes(m) is None and len(m) == 0
    add_attributes(m, divisions=DIV, key=2, meter=(3, 4), clef=("F", "4"), transpose=(-4, -7))
    assert [c.tag for c in m.find("attributes")] == ["divisions", "key", "time", "clef", "transpose"]
    assert m.findtext("attributes/key/fifths") == "2"
    assert m.findtext("attributes/time/beats") == "3"
    assert m.findtext("attributes/clef/sign") == "F"
    assert m.findtext("attributes/transpose/chromatic") == "-7"
    m2 = ET.Element("measure")
    add_attributes(m2, meter=(6, 8))
    assert [c.tag for c in m2.find("attributes")] == ["time"]


def test_copy_notes_rescales_and_drops_stems():
    src = _m("<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration>"
             "<stem>up</stem></note><barline/>")
    dst = ET.Element("measure")
    copy_notes(src, dst, src_divisions=2)             # 2 of 2 divisions = 1 quarter
    assert dst.findtext("note/duration") == str(DIV)
    assert dst.find("note/stem") is None and dst.find("barline") is None


def test_check_length():
    spec = SimpleNamespace(meter=lambda b: (4, 4))
    assert check_length(_m(NOTE.format(d=4 * DIV)), spec, 2) is None
    assert check_length(_m(NOTE.format(d=3 * DIV)), spec, 2) == (3.0, 4.0)


def test_apply_marks_goes_after_attributes():
    m = _m("<attributes/>" + REST.format(d=4))
    apply_marks([m], {1: {1: [("tempo", "Allegro", 120), ("rehearsal", "A")]},
                      "all": {1: [("words", "rit.")]}}, 1)
    assert [c.tag for c in m] == ["attributes", "direction", "direction", "direction", "note"]
    assert m.find("direction/sound").get("tempo") == "120"
    assert direction("rehearsal", "B").findtext("direction-type/rehearsal") == "B"


def test_units_collapses_multirests():
    spec = SimpleNamespace(TOTAL_BARS=6, MULTIREST=lambda p: [(2, 4)])
    assert units(spec, 1) == [(1, 1), (2, 4), (5, 5), (6, 6)]


def test_load_spec(tmp_path):
    f = tmp_path / "s.py"
    f.write_text(textwrap.dedent("TITLE = 'T'\n"))
    assert load_spec(f).TITLE == "T"
    with pytest.raises(AlignmentError):
        load_spec(tmp_path / "missing.py")
