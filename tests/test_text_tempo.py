"""One tempo mark per bar across the parts of a combined score (musicocr/assemble/text_tempo.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.assemble.text_tempo import tempo_key, unify_tempo


def bar(n, bpm=None, words=None, unit="quarter", dot=False, extra=""):
    d = ""
    if bpm:
        w = f"<direction-type><words>{words}</words></direction-type>" if words else ""
        d = (f"<direction placement='above'>{w}<direction-type><metronome><beat-unit>{unit}</beat-unit>"
             f"{'<beat-unit-dot/>' if dot else ''}<per-minute>{bpm}</per-minute></metronome></direction-type>"
             f"<staff>1</staff><sound tempo='{bpm}'/></direction>")
    return ET.fromstring(f"<measure number='{n}'><attributes><divisions>4</divisions></attributes>{d}{extra}"
                         "<note><rest/><duration>16</duration></note></measure>")


def tempos(m):
    return [tempo_key(d) for d in m.findall("direction") if tempo_key(d)]


def test_plurality_wins_and_is_written_once_in_the_first_part():
    parts = {1: [bar(1, 103, "Sl. Faster")], 2: [bar(1, 103, "Sl. Faster")], 3: [bar(1, 108)]}
    lines = unify_tempo(parts)
    assert tempos(parts[1][0]) == [("quarter", False, 103)]
    assert tempos(parts[2][0]) == [] and tempos(parts[3][0]) == []
    d = parts[1][0].find("direction")
    assert d.findtext("direction-type/words") == "Sl. Faster" and d.find("staff") is None
    assert any("disagree" in line and "check" in line for line in lines)


def test_a_mark_missed_in_the_first_part_is_filled_in_from_the_others():
    parts = {1: [bar(1)], 2: [bar(1, 124)], 3: [bar(1, 124)]}
    unify_tempo(parts)
    assert tempos(parts[1][0]) == [("quarter", False, 124)]
    names = [c.tag for c in parts[1][0]]
    assert names.index("direction") < names.index("note")          # before the first note


def test_other_directions_and_unmarked_bars_are_untouched():
    extra = "<direction><direction-type><words>marc.</words></direction-type></direction>"
    parts = {1: [bar(1, extra=extra), bar(2)], 2: [bar(1, extra=extra), bar(2)]}
    assert unify_tempo(parts) == []
    assert len(parts[2][0].findall("direction")) == 1


def test_dotted_beat_unit_is_part_of_the_reading():
    parts = {1: [bar(1, 59, unit="quarter", dot=True)], 2: [bar(1, 59, unit="quarter", dot=False)]}
    unify_tempo(parts)
    assert tempos(parts[1][0]) == [("quarter", True, 59)]          # a tie goes to the earliest part


def test_single_part_is_a_no_op():
    parts = {1: [bar(1, 100)]}
    assert unify_tempo(parts) == []
    assert tempos(parts[1][0]) == [("quarter", False, 100)]


def test_opening_word_becomes_one_tempo_mark_in_the_first_part():
    from musicocr.assemble.text_tempo import opening_tempo

    def bar(*words):
        m = ET.Element("measure")
        for w in words:
            d = ET.SubElement(m, "direction", placement="above", system="only-top")
            ET.SubElement(ET.SubElement(d, "direction-type"), "words").text = w
        ET.SubElement(m, "note")
        return m
    parts = {1: [bar()], 2: [bar("Allegro")], 3: [bar("Allegro", "Solo")]}
    lines = opening_tempo(parts)
    assert lines and "144" in lines[0]
    d = parts[1][0].find("direction")
    assert d.find("sound").get("tempo") == "144" and d.get("system") is None
    assert parts[2][0].find("direction") is None
    assert [e.findtext("direction-type/words") for e in parts[3][0].findall("direction")] == ["Solo"]
    assert opening_tempo({1: [bar("Solo")]}) == []


def test_a_flagged_reading_of_a_tempo_word_is_accepted_without_its_flag():
    from musicocr.assemble.text_tempo import opening_tempo
    m = ET.Element("measure")
    d = ET.SubElement(m, "direction", placement="above")
    w = ET.SubElement(ET.SubElement(d, "direction-type"), "words", color="#7B1FA2")
    w.text = "check: Marsch"
    ET.SubElement(m, "note")
    assert opening_tempo({1: [m]}) and w.text == "Marsch" and w.get("color") is None
    assert d.find("sound").get("tempo") == "120"
