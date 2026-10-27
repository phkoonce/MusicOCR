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


# ---- cue notes printed over a whole-bar rest ----------------------------------------------------

def _score34(*bars):
    ms = "".join(f"<measure number='{i + 1}'>{'<attributes><divisions>6</divisions><time><beats>3</beats><beat-type>4</beat-type></time></attributes>' if i == 0 else ''}{b}</measure>"
                 for i, b in enumerate(bars))
    return ET.fromstring(f"<score-partwise><part id='P1'>{ms}</part></score-partwise>")


def _n(step, d, typ):
    return f"<note><pitch><step>{step}</step><octave>5</octave></pitch><duration>{d}</duration><voice>1</voice><type>{typ}</type></note>"


WHOLE = "<note><rest/><duration>18</duration><voice>1</voice></note>"
QREST = "<note><rest/><duration>6</duration><voice>1</voice><type>quarter</type></note>"
ORDER = ["cue", "chord", "pitch", "rest", "duration", "tie", "voice", "type"]


def test_cue_notes_over_whole_bar_rest_move_to_voice_two():
    bars = [QREST + WHOLE + _n("A", 6, "quarter") + _n("B", 6, "quarter"),          # table: measure 5
            WHOLE + _n("G", 12, "half") + _n("F", 6, "quarter"),                      # measure 7
            WHOLE + _n("C", 6, "quarter") + _n("C", 3, "eighth") + _n("B", 3, "eighth") + _n("A", 6, "quarter")]
    root = _score34(*bars)
    assert fix_tree(root, log=lambda *_: None) == 3
    from musicocr.alignment.build import measure_total
    for m in root.findall("part/measure"):
        assert [c.tag for c in m if c.tag in ("backup",)] == ["backup"]
        assert m.find("backup/duration").text == "18"
        v1 = [n for n in m.findall("note") if n.findtext("voice") == "1"]
        v2 = [n for n in m.findall("note") if n.findtext("voice") == "2"]
        assert len(v1) == 1 and v1[0].find("rest") is not None and v1[0].find("cue") is None
        assert sum(int(n.findtext("duration")) for n in v2) == 18
        assert all(n.find("cue") is not None and n[0].tag == "cue" for n in v2)
        assert measure_total(m) == 18
        kids = list(m)
        assert kids.index(m.find("backup")) < kids.index(v2[0]) and kids.index(v1[0]) < kids.index(m.find("backup"))
    first = root.find("part/measure")
    assert [n.findtext("pitch/step") or "r" for n in first.findall("note") if n.findtext("voice") == "2"] == ["r", "A", "B"]


def test_cue_rule_leaves_other_bars_alone():
    cases = [WHOLE + _n("A", 6, "quarter") + _n("B", 6, "quarter") + _n("C", 6, "quarter") + _n("D", 6, "quarter"),   # 7 beats
             WHOLE + _n("A", 6, "quarter"),                                       # short
             WHOLE + WHOLE + _n("A", 6, "quarter") + _n("B", 6, "quarter") + _n("C", 6, "quarter"),   # two typeless rests
             WHOLE + WHOLE,                                                       # no pitched note
             _n("A", 18, "half") + _n("B", 18, "half"),                           # no rest
             WHOLE + _n("A", 18, "half") + "<backup><duration>18</duration></backup>"]
    root = _score34(*cases)
    before = ET.tostring(root)
    assert fix_tree(root, log=lambda *_: None) == 0
    assert ET.tostring(root) == before


def test_cue_rule_pickup_bar_untouched():
    root = _score34(WHOLE + _n("A", 3, "eighth"))
    assert fix_tree(root, log=lambda *_: None) == 0


def test_measure_total_of_two_voice_bar_is_longest_voice():
    from musicocr.alignment.build import copy_notes, is_rest_only
    root = _score34(WHOLE + _n("A", 6, "quarter") + _n("B", 6, "quarter") + _n("C", 6, "quarter"))
    fix_tree(root, log=lambda *_: None)
    m = root.find("part/measure")
    assert is_rest_only(m)                                # the sounding voice is a rest
    dst = ET.Element("measure")
    copy_notes(m, dst, 6)
    from musicocr.alignment.build import DIV, measure_total
    assert int(dst.find("backup/duration").text) == 18 * DIV // 6
    assert measure_total(dst) == 18 * DIV // 6


def test_combine_keeps_cue_voice_and_flags_nothing(tmp_path):
    from musicocr.combine import PartInput, combine
    attrs = ("<attributes><divisions>6</divisions><key><fifths>0</fifths></key>"
             "<time><beats>3</beats><beat-type>4</beat-type></time></attributes>")
    parts = []
    for pid, cue in (("a", True), ("b", False), ("c", False)):
        body = f'<measure number="1">{attrs}{_n("C", 18, "half")}</measure>'
        body += ('<measure number="2">' + (WHOLE + _n("A", 6, "quarter") + _n("B", 6, "quarter") + _n("C", 6, "quarter")
                                           if cue else WHOLE) + "</measure>")
        root = ET.fromstring(f'<score-partwise><part id="P1">{body}</part></score-partwise>')
        if cue:
            assert fix_tree(root, log=lambda *_: None) == 1
        f = tmp_path / f"{pid}.p001.musicxml"
        ET.ElementTree(root).write(f)
        (tmp_path / f"{pid}.pdf").write_bytes(b"x")
        parts.append(PartInput(pid, f"Trumpet {pid}", "trumpet", tmp_path / f"{pid}.pdf", [f]))
    score, report, _ = combine(parts, "T", starts_of=lambda pdf: [1], last_start_of=lambda pdf: 2,
                               spans_of=lambda pdf: [])
    assert not any("LENGTH" in r for r in report), report
    m2 = score.findall("part")[0].findall("measure")[1]
    assert m2.find("backup") is not None
    assert len([n for n in m2.findall("note") if n.find("cue") is not None]) == 3


def test_voice_two_already_split_gets_cue_marks_and_one_bar_backup():
    # measure 5 of the table: voice 1 = cue-line rest + whole-bar rest, voice 2 after a forward
    m5 = (QREST + WHOLE + "<backup><duration>24</duration></backup><forward><duration>6</duration><voice>2</voice></forward>"
          + _n("A", 6, "quarter").replace("<voice>1", "<voice>2") + _n("B", 6, "quarter").replace("<voice>1", "<voice>2"))
    m6 = WHOLE + "<backup><duration>18</duration></backup>" + _n("G", 18, "half").replace("<voice>1", "<voice>2")
    root = _score34(m5, m6)
    assert fix_tree(root, log=lambda *_: None) == 2
    from musicocr.alignment.build import measure_total
    a, b = root.findall("part/measure")
    assert measure_total(a) == 18 and a.find("backup/duration").text == "18" and len(a.findall("note")) == 3
    assert all(n.find("cue") is not None for n in a.findall("note") if n.findtext("voice") == "2")
    assert b.find("note[voice='2']/cue") is not None
    assert fix_tree(root, log=lambda *_: None) == 0          # idempotent
