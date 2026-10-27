"""One tempo mark per bar across the parts of a combined score (musicocr/text_tempo.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.text_tempo import tempo_key, unify_tempo


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
    assert any("disagree" in l and "check" in l for l in lines)


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
