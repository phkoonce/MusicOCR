import xml.etree.ElementTree as ET

from musicocr.fixes.slurs import fix_tree


def note(step="F", octave="4", slur=None, rest=False, chord=False, grace=False, tie=None):
    n = ET.Element("note")
    if grace:
        ET.SubElement(n, "grace")
    if chord:
        ET.SubElement(n, "chord")
    if rest:
        ET.SubElement(n, "rest")
    else:
        p = ET.SubElement(n, "pitch")
        ET.SubElement(p, "step").text = step
        ET.SubElement(p, "octave").text = octave
    ET.SubElement(n, "duration").text = "4"
    ET.SubElement(n, "voice").text = "1"
    if slur:
        ET.SubElement(ET.SubElement(n, "notations"), "slur", type=slur, number="1")
    return n


def score(*notes_per_bar):
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part")
    for notes in notes_per_bar:
        m = ET.SubElement(part, "measure")
        for n in notes:
            m.append(n)
    return root


def test_same_pitch_adjacent_slur_becomes_a_tie_across_a_barline():
    a, b = note(slur="start"), note(slur="stop")
    root = score([note("G"), a], [b, note("A")])
    assert fix_tree(root) == 1
    assert a.find("notations/slur") is None and b.find("notations/slur") is None
    assert a.find("tie").get("type") == "start" and a.find("notations/tied").get("type") == "start"
    assert b.find("tie").get("type") == "stop" and b.find("notations/tied").get("type") == "stop"
    assert [e.tag for e in a][:3] == ["pitch", "duration", "tie"]


def test_different_pitch_stays_a_slur():
    a, b = note("F", slur="start"), note("G", slur="stop")
    assert fix_tree(score([a, b])) == 0
    assert a.find("notations/slur") is not None


def test_slur_over_a_note_or_rest_in_between_stays():
    a, b = note(slur="start"), note(slur="stop")
    assert fix_tree(score([a, note("G"), b])) == 0
    a, b = note(slur="start"), note(slur="stop")
    assert fix_tree(score([a, note(rest=True), b])) == 0


def test_chords_and_graces_are_left_alone():
    a, b = note(slur="start"), note(slur="stop")
    assert fix_tree(score([a, note("A", chord=True), b])) == 0
    a, b = note(slur="start"), note(slur="stop", grace=True)
    assert fix_tree(score([a, b])) == 0
