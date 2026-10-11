"""Tests for the bar-count check and Copisteria's ghost-bar fix (synthetic data)."""
from __future__ import annotations

from xml.etree import ElementTree as ET

from musicocr.barcheck import check_bar_counts, check_page, page_files_of
from musicocr.copisteria_fixes import drop_ghost_bars


def _pitch(step="F", alt=1, octv=4, dur=16, typ="whole", extra=""):
    return (f"<note><pitch><step>{step}<alter>{alt}</alter></step><octave>{octv}</octave></pitch>"
            f"<duration>{dur}</duration><voice>1</voice><type>{typ}</type>{extra}</note>"
            .replace(f"<step>{step}<alter>{alt}</alter></step>",
                     f"<step>{step}</step><alter>{alt}</alter>"))


def _rest(dur=16):
    return f'<note><rest measure="yes"/><duration>{dur}</duration><voice>1</voice></note>'


DOUBLE = '<barline location="right"><bar-style>light-light</bar-style></barline>'


def _write(tmp_path, measures, name="p001.musicxml"):
    body = "".join(f'<measure number="{i}">{m}</measure>' for i, m in enumerate(measures, 1))
    p = tmp_path / name
    p.write_text('<?xml version="1.0"?><score-partwise><part-list/><part id="P1">'
                 + body + "</part></score-partwise>")
    return p


def _nums(p):
    return [m.get("number") for m in ET.parse(p).getroot().find("part").findall("measure")]


GHOST_KEY = "<attributes><key><fifths>3</fifths></key></attributes>"
NEXT_TIME = "<attributes><time><beats>6</beats><beat-type>8</beat-type></time></attributes>"


def test_ghost_bar_dropped_and_signature_moved(tmp_path):
    p = _write(tmp_path, [_pitch("A", 0) + DOUBLE, GHOST_KEY + _pitch(), NEXT_TIME + _pitch("F", 1, 5, 6, "quarter"),
                          _rest(12)])
    logs = []
    assert drop_ghost_bars(p, log=logs.append) == 1
    assert logs and "ghost" in logs[0]
    root = ET.parse(p).getroot()
    ms = root.find("part").findall("measure")
    assert [m.get("number") for m in ms] == ["1", "2", "3"]
    assert ms[1].find("attributes/key/fifths").text == "3"      # moved onto next bar
    assert ms[1].find("attributes/time/beats").text == "6"
    assert [c.tag for c in ms[1].find("attributes")] == ["key", "time"]


def test_ghost_kept_without_double_bar(tmp_path):
    p = _write(tmp_path, [_pitch("A", 0), GHOST_KEY + _pitch(), NEXT_TIME + _pitch()])
    assert drop_ghost_bars(p, log=lambda s: None) == 0
    assert len(_nums(p)) == 3


def test_ghost_kept_when_content_unexplained(tmp_path):
    artic = "<notations><articulations><accent/></articulations></notations>"
    p = _write(tmp_path, [_pitch("A", 0) + DOUBLE, GHOST_KEY + _pitch(extra=artic), NEXT_TIME + _pitch()])
    assert drop_ghost_bars(p, log=lambda s: None) == 0
    p = _write(tmp_path, [_pitch("A", 0) + DOUBLE, GHOST_KEY + _pitch() * 3, NEXT_TIME + _pitch()])
    assert drop_ghost_bars(p, log=lambda s: None) == 0


def test_real_key_change_bar_kept(tmp_path):
    # key AND time change together in a real bar; next bar declares nothing
    both = ("<attributes><key><fifths>3</fifths></key><time><beats>6</beats>"
            "<beat-type>8</beat-type></time></attributes>")
    p = _write(tmp_path, [_pitch("A", 0) + DOUBLE, both + _pitch(), _pitch()])
    assert drop_ghost_bars(p, log=lambda s: None) == 0


# --- bar-count check -----------------------------------------------------------------

def test_check_page_system_level():
    starts = [(100.0, 7), (200.0, 13)]
    counts = [(150.0, 70.0, 5)]                          # 5-bar rest numeral above system 2's first row
    # page 1: implied first system bar 1; groups for 3 systems
    ok = check_page(1, starts, counts, 19, [6, 6 - 4, 6], True)
    assert ok == []
    # system 2 has 7 bars - 4 = 3 units expected; found 4 -> ghost
    bad = check_page(1, starts, counts, 19, [6, 3, 6], True)
    assert len(bad) == 1 and bad[0].system == 2
    assert bad[0].actual - bad[0].expected == 1 and "ghost" in bad[0].message
    miss = check_page(1, starts, [], 19, [6, 5, 6], True)
    assert miss[0].system == 2 and "missing" in miss[0].message


def test_check_page_page_level_and_last_system_skipped():
    starts = [(100.0, 45), (200.0, 51)]
    assert check_page(2, starts, [], 57, [6, 6], False) == []
    assert len(check_page(2, starts, [], 57, [6, 7], False)) == 1
    assert check_page(2, starts, [], None, [6, 7], False) == []       # no known end
    # system marks present: last system skipped when end unknown, others still checked
    assert len(check_page(2, starts, [], None, [7, 7], True)) == 1


def test_check_bar_counts_end_to_end_with_fake_text_layer(tmp_path):
    xml = _write(tmp_path, [_rest(16)] * 7, "book.p001.musicxml")
    pdf = tmp_path / "x.pdf"

    def words(_pdf, page):
        if page == 1:     # numbered system start at left margin; page 2 starts at bar 7
            return [(50.0, 200.0, 60.0, 208.0, "1")]
        if page == 2:
            return [(50.0, 200.0, 60.0, 208.0, "7")]
        return []
    # page 1, no mark, start missing for bar 1 -> page 1 implied bar 1 .. 6 expected 6 units
    issues = check_bar_counts(pdf, page_files_of([xml]), words=words)
    assert len(issues) == 1 and issues[0].expected == 6
    assert issues[0].actual == 7
    # no text layer -> silently nothing
    assert check_bar_counts(pdf, page_files_of([xml]), words=lambda *_: []) == []
