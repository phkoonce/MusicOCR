from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.assemble.unnumbered import align_by_rhythm, dissimilarity, onsets

F = frozenset


def _bar(*notes, div=2):
    m = ET.Element("measure")
    for dur, rest in notes:
        n = ET.SubElement(m, "note")
        ET.SubElement(n, "rest" if rest else "pitch")
        ET.SubElement(n, "duration").text = str(dur)
    return m, div


def test_onsets_skip_rests_and_chords():
    m, div = _bar((2, False), (2, True), (4, False))
    assert onsets(m, div) == F({0, 24})            # quarter (2 div) = 12 ticks; rest skipped
    ch = ET.SubElement(m, "note")
    ET.SubElement(ch, "chord")
    ET.SubElement(ch, "duration").text = "4"
    assert onsets(m, div) == F({0, 24})


def test_dissimilarity():
    assert dissimilarity(F(), F()) == 0.0
    assert dissimilarity(F({0}), F()) == 1.0
    assert dissimilarity(F({0, 12}), F({0, 12})) == 0.0
    assert dissimilarity(F({0, 12}), F({0})) == 0.5


def test_lost_bar_is_inserted_where_the_others_play_something_else():
    pats = [F({0}), F({0, 6}), F({0, 4, 8}), F({0, 12}), F({0, 3}), F({0, 9})]
    ref = [[p, p] for p in pats]                     # two other parts agreeing bar for bar
    src = pats[:2] + pats[3:]                         # bar 3 (index 2) was lost
    slots = align_by_rhythm(src, ref)
    assert slots == [0, 1, None, 2, 3, 4]


def test_extra_bar_is_dropped():
    pats = [F({0}), F({0, 6}), F({0, 4, 8}), F({0, 12})]
    ref = [[p] for p in pats]
    src = [pats[0], pats[1], F({0, 1, 2, 3}), pats[2], pats[3]]
    assert align_by_rhythm(src, ref) == [0, 1, 3, 4]


def _measure(pattern, div=2):
    """A 4/4 bar whose notes start at the given quarter-note beats (the last runs to the barline)."""
    m = ET.Element("measure")
    beats = list(pattern) + [4]
    for a, b in zip(beats, beats[1:]):
        n = ET.SubElement(m, "note")
        p = ET.SubElement(n, "pitch")
        ET.SubElement(p, "step").text = "C"
        ET.SubElement(p, "octave").text = "4"
        ET.SubElement(n, "duration").text = str((b - a) * div)
    return m, div


def test_combine_without_bar_numbers_repairs_a_short_part(tmp_path):
    from pathlib import Path
    from musicocr.assemble.combine import PartInput, combine

    bars = [(0,), (0, 2), (0, 1, 2), (0, 3), (0, 1), (0, 2, 3), (0, 1, 3), (0, 2)]
    pages = {"a": [_measure(p) for p in bars], "b": [_measure(p) for p in bars],
             "c": [_measure(p) for i, p in enumerate(bars) if i != 4]}      # c lost bar 5
    parts = [PartInput(k, k, "", None, [Path(k)]) for k in pages]
    score, report, summary = combine(parts, "T", read_pages=lambda f: pages[f.name], bar_numbers=False)
    assert summary["total"] == 8
    c = score.findall("part")[2].findall("measure")
    assert len(c) == 8
    assert c[4].find("note/rest") is not None            # the lost bar is a rest, the rest are in place
    a = score.findall("part")[0].findall("measure")
    assert [len(m.findall("note")) for m in c] == [len(m.findall("note")) for m in a[:4]] + [1] + [len(m.findall("note")) for m in a[5:]]
    assert any("rhythm" in line for line in report)
