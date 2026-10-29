"""Generic tuplet clean-up (musicocr/tuplets.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.tuplets import fix_measure, fix_tree

DIV = 30


def _n(kind="note", dur=15, tm=None, tup=None, typ="eighth", voice=1, chord=False):
    body = ("<chord/>" if chord else "") + ("<rest/>" if kind == "rest" else "<pitch><step>A</step><octave>4</octave></pitch>")
    body += f"<duration>{dur}</duration><voice>{voice}</voice><type>{typ}</type>"
    if tm:
        body += f"<time-modification><actual-notes>{tm[0]}</actual-notes><normal-notes>{tm[1]}</normal-notes></time-modification>"
    if tup:
        body += f"<notations><tuplet type='{tup}'/></notations>"
    return f"<note>{body}</note>"


def _measure(*notes):
    return ET.fromstring(f"<measure number='1'>{''.join(notes)}</measure>")


def _summary(m):
    return [(int(n.findtext("duration")), n.findtext("time-modification/actual-notes"),
             [t.get("type") for t in n.iter("tuplet")]) for n in m.findall("note")]


def _bar(*notes, beats=4):
    return ET.fromstring("<score-partwise><part id='P1'><measure number='1'><attributes><divisions>30</divisions>"
                         f"<time><beats>{beats}</beats><beat-type>4</beat-type></time></attributes>"
                         + "".join(notes) + "</measure></part></score-partwise>")


def _m(root):
    return root.find("part/measure")


def test_bar_44_case_mixed_ratios_and_orphan_start():
    root = _bar(_n(dur=15), _n("rest", 15), _n("rest", 30, typ="quarter"),
                _n(dur=10, tm=(3, 2)), _n("rest", 10, tm=(6, 4)), _n(dur=10, tm=(3, 2), tup="start"),
                _n("rest", 10, tm=(3, 2)), _n(dur=10, tm=(3, 2), tup="stop"), _n("rest", 12, tm=(5, 4)))
    logs = []
    assert fix_tree(root, log=logs.append) == 1 and "mixed tuplet ratios" in logs[0] and "4.0666" not in logs[0]
    m = _m(root)
    assert [d for d, *_ in _summary(m)][3:] == [10] * 6
    assert sum(int(d.findtext("duration")) for d in m.findall("note")) == 120
    assert [t for *_, t in _summary(m)][3:] == [["start"], [], ["stop"], ["start"], [], ["stop"]]
    assert m.find("direction/direction-type/words").text.startswith("check:")      # flagged for a look
    assert all(n.get("color") for n in m.findall("note")[3:])


def test_bars_that_add_up_are_never_touched():
    # odd bracket structure, but the bar is exactly 4 beats: leave it
    root = _bar(_n(dur=10, tm=(3, 2)), _n(dur=10, tm=(3, 2), tup="start"), _n(dur=10, tm=(3, 2), tup="stop"),
                _n("rest", 90, typ="half"))
    # 30 + 90 = 120 = 4 beats
    before = ET.tostring(root)
    assert fix_tree(root, log=lambda s: None) == 0 and ET.tostring(root) == before


def test_wrong_length_bar_whose_rebuild_does_not_fix_it_is_restored():
    root = _bar(_n(dur=10, tm=(3, 2)), _n(dur=10, tm=(3, 2)), _n(dur=12, tm=(5, 4)), _n(dur=10, tm=(3, 2)))
    before = ET.tostring(root)
    logs = []
    assert fix_tree(root, log=logs.append) == 0 and ET.tostring(root) == before
    assert logs and "not fixed" in logs[0]


def test_chord_tones_follow_their_lead_note():
    root = _bar(_n(dur=10, tm=(3, 2), tup="start"), _n(dur=10, tm=(3, 2), chord=True), _n(dur=12, tm=(5, 4)),
                _n(dur=10, tm=(3, 2), tup="stop"), _n("rest", 90, typ="half"))
    assert fix_tree(root, log=lambda s: None) == 1                   # 10 + 12 + 10 + 90 = 122 -> 120
    assert [int(n.findtext("duration")) for n in root.iter("note")] == [10, 10, 10, 10, 90]


def test_stage_config_defaults_on():
    from musicocr.config import StagesConfig
    c = StagesConfig.from_dict({})
    assert c.tuplets_enabled and c.tuplets_flag
    assert not StagesConfig.from_dict({"tuplets": {"enabled": False}}).tuplets_enabled


def test_beamed_note_missing_its_tuplet_ratio_is_adopted():
    def n(dur, typ, beam, tm=None, tup=None):
        t = f"<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>" if tm else ""
        nt = f"<notations><tuplet type='{tup}'/></notations>" if tup else ""
        return (f"<note><pitch><step>E</step><octave>5</octave></pitch><duration>{dur}</duration><voice>1</voice>"
                f"<type>{typ}</type>{t}<beam number='1'>{beam}</beam>{nt}</note>")
    root = _bar(_n(dur=30, typ="quarter"), _n(dur=30, typ="quarter"),
                n(10, "eighth", "begin", tm=1, tup="start"), n(5, "16th", "continue", tm=1),
                n(5, "16th", "continue", tm=1, tup="stop"), n(15, "eighth", "end"), beats=3)
    logs = []
    assert fix_tree(root, log=logs.append) == 1 and "had no ratio" in logs[0]
    last = _m(root).findall("note")[-1]
    assert last.findtext("duration") == "10" and last.findtext("time-modification/actual-notes") == "3"
    assert [t.get("type") for t in last.iter("tuplet")] == ["stop"]
    assert sum(int(d.findtext("duration")) for d in _m(root).findall("note")) == 90


def test_bar_whose_tuplet_has_no_notation_is_cleared_to_a_rest():
    # 3:2 run of quarter, eighth rest, eighth (4/3 beat), then an eighth pair: adds up to 2 beats yet unwritable
    root = _bar(_n(dur=20, tm=(3, 2), typ="quarter"), _n("rest", 10, tm=(3, 2)), _n(dur=10, tm=(3, 2)),
                _n("rest", 10, tm=(3, 2), typ="eighth"), _n(dur=10, tm=(3, 2)), _n("rest", 10, tm=(3, 2)),
                _n(dur=15), _n(dur=15), beats=3)
    logs = []
    assert fix_tree(root, log=logs.append) == 1 and logs[0].strip().startswith("measure 1: cleared")
    m = _m(root)
    notes = m.findall("note")
    assert len(notes) == 1 and notes[0].find("rest") is not None and notes[0].findtext("duration") == "90"
    assert m.find("direction/direction-type/words").text.startswith("check:")


def test_writable_triplets_are_left_alone():
    root = _bar(*[_n(dur=10, tm=(3, 2)) for _ in range(3)], _n(dur=30, typ="quarter"), _n(dur=60, typ="half"))
    assert fix_tree(root, log=lambda *_: None) == 0
    assert len(_m(root).findall("note")) == 5
