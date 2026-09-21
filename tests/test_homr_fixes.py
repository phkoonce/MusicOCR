"""Tests for musicocr.homr_fixes against the two homr failure modes documented
in omr-eval/README.md: the empty-multirest generator bug (fixable) and a
dropped multi-rest (only flaggable, not fixable)."""
from __future__ import annotations

from xml.etree import ElementTree as ET

from musicocr.homr_fixes import fix_multirest_file, fix_multirest_part, suspicious_whole_rests


def _attrs(measure, *, divisions=None, beats=None, beat_type=None, multi_rest=None):
    attrs = ET.SubElement(measure, "attributes")
    if divisions is not None:
        ET.SubElement(attrs, "divisions").text = str(divisions)
    if beats is not None:
        time = ET.SubElement(attrs, "time")
        ET.SubElement(time, "beats").text = str(beats)
        ET.SubElement(time, "beat-type").text = str(beat_type)
    if multi_rest is not None:
        style = ET.SubElement(attrs, "measure-style")
        ET.SubElement(style, "multiple-rest").text = str(multi_rest)
    return attrs


def _note(rest=True, duration=4, staff=None):
    note = ET.Element("note")
    if rest:
        ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(duration)
    if staff:
        ET.SubElement(note, "staff").text = staff
    return note


def test_fix_multirest_part_injects_duration():
    part = ET.Element("part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=1, beats=4, beat_type=4, multi_rest=8)

    fixed = fix_multirest_part(part, log=lambda s: None)

    assert fixed == 1
    notes = m.findall("note")
    assert len(notes) == 1
    assert notes[0].find("rest").get("measure") == "yes"
    assert notes[0].find("duration").text == "4"


def test_fix_multirest_part_skips_measure_with_content():
    part = ET.Element("part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=1, beats=4, beat_type=4, multi_rest=8)
    m.append(_note())

    fixed = fix_multirest_part(part, log=lambda s: None)

    assert fixed == 0
    assert len(m.findall("note")) == 1


def test_fix_multirest_part_skips_multistaff():
    part = ET.Element("part", {"id": "P1"})
    # establish two staves via note content elsewhere in the part
    ctx = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(ctx, divisions=1, beats=4, beat_type=4)
    ctx.append(_note(staff="1"))
    ctx.append(_note(staff="2"))
    # an empty multirest measure that would otherwise be fixable
    target = ET.SubElement(part, "measure", {"number": "2"})
    _attrs(target, multi_rest=4)

    fixed = fix_multirest_part(part, log=lambda s: None)

    assert fixed == 0
    assert target.find("note") is None


def test_fix_multirest_part_skips_without_time_signature_yet():
    part = ET.Element("part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, multi_rest=3)  # no divisions/time seen yet

    fixed = fix_multirest_part(part, log=lambda s: None)

    assert fixed == 0


def test_fix_multirest_file_roundtrip(tmp_path):
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=2, beats=3, beat_type=4, multi_rest=2)
    path = tmp_path / "page.musicxml"
    ET.ElementTree(root).write(path)

    fixed = fix_multirest_file(path, log=lambda s: None)

    assert fixed == 1
    reparsed = ET.parse(path).getroot()
    note = reparsed.find(".//note")
    assert note.find("duration").text == str(2 * 3)  # divisions * beats


def test_suspicious_whole_rests_flags_untagged_whole_measure_rest(tmp_path):
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=1, beats=4, beat_type=4)
    m.append(_note(duration=4))  # whole-measure rest, no <multiple-rest> tag
    path = tmp_path / "page.musicxml"
    ET.ElementTree(root).write(path)

    notes = suspicious_whole_rests(path)

    assert len(notes) == 1
    assert "measure 1" in notes[0]


def test_suspicious_whole_rests_ignores_tagged_multirest(tmp_path):
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=1, beats=4, beat_type=4, multi_rest=8)
    m.append(_note(duration=4))
    path = tmp_path / "page.musicxml"
    ET.ElementTree(root).write(path)

    assert suspicious_whole_rests(path) == []


def test_suspicious_whole_rests_ignores_partial_measure_rest(tmp_path):
    root = ET.Element("score-partwise")
    part = ET.SubElement(root, "part", {"id": "P1"})
    m = ET.SubElement(part, "measure", {"number": "1"})
    _attrs(m, divisions=1, beats=4, beat_type=4)
    m.append(_note(duration=2))  # half-measure rest -- not suspicious
    path = tmp_path / "page.musicxml"
    ET.ElementTree(root).write(path)

    assert suspicious_whole_rests(path) == []
