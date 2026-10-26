"""Whole-bar rests take the bar's length (musicocr/barrests.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.barrests import fix_tree

GRACE = "<note><grace/><pitch><step>C</step><octave>6</octave></pitch><voice>1</voice><type>eighth</type></note>"


def _score(*bars):
    ms = "".join(f"<measure number='{i + 1}'>{'<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>' if i == 0 else ''}{b}</measure>"
                 for i, b in enumerate(bars))
    return ET.fromstring(f"<score-partwise><part id='P1'>{ms}</part></score-partwise>")


def _rest(d):
    return f"<note><rest measure='yes'/><duration>{d}</duration><voice>1</voice></note>"


def test_cue_notes_over_a_too_long_rest_leave_one_bar():
    root = _score(GRACE * 4 + _rest(18), _rest(16))
    assert fix_tree(root, log=lambda *_: None) == 1
    first = root.findall("part/measure")[0].findall("note")[-1]
    assert first.findtext("duration") == "16"      # the grace notes keep no duration, the rest fills the bar


def test_bars_with_notes_or_two_rests_are_left_alone():
    note = "<note><pitch><step>C</step><octave>5</octave></pitch><duration>8</duration><voice>1</voice><type>half</type></note>"
    root = _score(note + _rest(10), _rest(4) + _rest(4))
    assert fix_tree(root, log=lambda *_: None) == 0
