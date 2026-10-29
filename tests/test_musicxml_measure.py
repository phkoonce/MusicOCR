from xml.etree import ElementTree as ET

from musicocr.musicxml import extent_length, note_duration, sounding_length, write_tree


def _m(body: str) -> ET.Element:
    return ET.fromstring(f"<measure number='1'>{body}</measure>")


def _note(d, extra=""):
    return f"<note>{extra}<pitch><step>C</step><octave>4</octave></pitch><duration>{d}</duration></note>"


def test_single_voice_lengths_agree():
    m = _m(_note(4) + _note(4) + _note(8))
    assert sounding_length(m) == extent_length(m) == 16


def test_chord_members_and_grace_notes_do_not_advance():
    m = _m(_note(4) + _note(4, "<chord/>") + "<note><grace/><pitch><step>D</step><octave>4</octave></pitch></note>" + _note(4))
    assert sounding_length(m) == extent_length(m) == 8
    assert note_duration(m.findall("note")[2]) == 0


def test_forward_counts_in_extent_not_in_sounding():
    m = _m(_note(4) + "<forward><duration>12</duration></forward>")
    assert sounding_length(m) == 4
    assert extent_length(m) == 16


def test_two_voices_take_the_longest_voice():
    m = _m(_note(8, "<voice>1</voice>").replace("<note>", "<note>") + "<backup><duration>8</duration></backup>"
           + _note(4, "<voice>2</voice>") + _note(4, "<voice>2</voice>") + _note(4, "<voice>2</voice>"))
    assert sounding_length(m) == 12
    assert extent_length(m) == 12


def test_write_tree_indents_and_declares(tmp_path):
    tree = ET.ElementTree(ET.fromstring("<a><b/></a>"))
    out = tmp_path / "x.xml"
    write_tree(tree, out)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "\n  <b" in text
