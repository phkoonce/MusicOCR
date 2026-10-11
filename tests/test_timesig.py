"""Tests for musicocr.timesig."""
from xml.etree import ElementTree as ET

from musicocr.timesig import fix_time_signatures


def _score(blocks):
    """blocks: list of (beats, beat_type or None-if-no-time, [measure quarter lengths])."""
    root = ET.Element("score-partwise")
    ET.SubElement(ET.SubElement(root, "part-list"), "score-part", id="P1")
    part = ET.SubElement(root, "part", id="P1")
    n = 0
    for beats, beat_type, lengths in blocks:
        for j, q in enumerate(lengths):
            n += 1
            m = ET.SubElement(part, "measure", number=str(n))
            if n == 1 or (j == 0 and beats):
                at = ET.SubElement(m, "attributes")
                if n == 1:
                    ET.SubElement(at, "divisions").text = "2"
                if beats:
                    t = ET.SubElement(at, "time")
                    ET.SubElement(t, "beats").text = str(beats)
                    ET.SubElement(t, "beat-type").text = str(beat_type)
            note = ET.SubElement(m, "note")
            ET.SubElement(note, "duration").text = str(int(q * 2))
    return root


def _times(root):
    return [t.findtext("beats") + "/" + t.findtext("beat-type") for t in root.iter("time")]


def test_8_8_becomes_6_8_across_following_measures():
    root = _score([(4, 4, [4] * 10), (8, 8, [3] * 20 + [4])])
    assert fix_time_signatures(root, log=lambda s: None) == 1
    assert _times(root) == ["4/4", "6/8"]


def test_correct_signatures_untouched():
    root = _score([(4, 4, [4] * 10), (6, 8, [3] * 12)])
    assert fix_time_signatures(root, log=lambda s: None) == 0
    assert _times(root) == ["4/4", "6/8"]


def test_no_clear_majority_or_too_few_measures_untouched():
    root = _score([(8, 8, [3, 4, 3, 4, 3, 4, 3, 4])])
    assert fix_time_signatures(root, log=lambda s: None) == 0
    root = _score([(4, 4, [4] * 2), (8, 8, [3, 3, 3])])   # first/last bars excluded -> 1 left
    assert fix_time_signatures(root, log=lambda s: None) == 0
    assert _times(root) == ["4/4", "8/8"]


def test_files_treated_as_one_part_and_signature_carried(tmp_path):
    from musicocr.timesig import fix_time_signatures_files

    a = _score([(4, 4, [4] * 10), (8, 8, [3] * 4)])
    b = _score([(0, None, [3] * 12)])
    for m in b.iter("measure"):
        for at in m.findall("attributes"):
            m.remove(at)          # page with no attributes at all
    pa, pb = tmp_path / "a.musicxml", tmp_path / "b.musicxml"
    ET.ElementTree(a).write(pa)
    ET.ElementTree(b).write(pb)
    assert fix_time_signatures_files([pa, pb], log=lambda s: None) == 1
    assert _times(ET.parse(pa).getroot()) == ["4/4", "6/8"]
    assert _times(ET.parse(pb).getroot()) == ["6/8"]      # carried onto the signature-less page


def test_time_in_second_attributes_block():
    root = _score([(4, 4, [4] * 2)])
    first = root.find(".//measure")
    first.find("attributes").remove(first.find("attributes/time"))          # divisions-only first block
    extra = ET.Element("attributes")
    t = ET.SubElement(extra, "time")
    ET.SubElement(t, "beats").text = "3"
    ET.SubElement(t, "beat-type").text = "4"
    first.insert(1, extra)
    for n in range(3, 14):
        m = ET.SubElement(root.find("part"), "measure", number=str(n))
        ET.SubElement(ET.SubElement(m, "note"), "duration").text = "8"
    assert fix_time_signatures(root, log=lambda s: None) == 1
    assert _times(root) == ["4/4"]
