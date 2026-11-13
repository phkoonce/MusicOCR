"""Round 6: staff choice for score pages on skewed scans (spacing, narrow-window staff finder), all-playing stretches
that cannot hold the bars the next system number implies, tied notes merged in the reference scoring."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

from musicocr.assemble import voltas as vt
from musicocr.scoreops.staffpick import isolate_staff, local_staves, spacing_groups, staff_groups
from musicocr.text.words import Staff


def _staves(tops, h=96):
    return [Staff(t, t + h, 100) for t in tops]


def test_spacing_groups_tell_systems_by_gaps():
    # three systems of three: 170 inside, 240-340 between (paxman p89)
    tops = [900, 1170, 1440, 1880, 2150, 2420, 2880, 3150, 3420]
    assert [len(g) for g in spacing_groups(_staves(tops))] == [3, 3, 3]
    # a part page with one slightly larger gap, or evenly spaced staves, has no system structure
    assert [len(g) for g in spacing_groups(_staves([900, 1200, 1500, 1800, 2180, 2490]))] == [1] * 6
    assert [len(g) for g in spacing_groups(_staves([900, 1200, 1500, 1800]))] == [1] * 4
    # paired staves on a part page (two versions) alternate 270 / 450: two clusters but only one kind of evidence
    # (no barline): is_score_page is decided by the caller, the groups here are pairs
    assert [len(g) for g in spacing_groups(_staves([300, 670, 1200, 1570, 2150, 2530, 3130, 3530]))] == [2, 2, 2, 2]


def _page(systems, per=3, w=700, skew_last=0, h=1300):
    """White page; ``systems`` systems of ``per`` staves (space 8 px, gap 40 px inside, 90 px between); a system line at x=100
    through the gaps; the last system's lines slope by ``skew_last`` px across the page (a warped scan)."""
    a = np.full((h, w), 255, np.uint8)
    y = 60
    for s in range(systems):
        first = y
        for _ in range(per):
            for k in range(5):
                for x in range(120, 640):
                    yy = y + 8 * k + (skew_last * (x - 120) // 520 if s == systems - 1 else 0)
                    a[yy, x] = 0
            y += 32 + 40
        a[first:y - 40, 100:103] = 0
        y += 50
    return a


def test_local_staves_find_a_warped_system_the_wide_band_loses(tmp_path):
    from musicocr.text.words import find_staves
    a = _page(4, skew_last=18)
    img = Image.fromarray(a)
    wide = find_staves(img)
    narrow = local_staves(img)
    assert len(narrow) == 12
    assert len(wide) < 12
    arr = np.asarray(img) < 160
    staves, groups = staff_groups(img, arr)
    assert len(staves) == 12 and [len(g) for g in groups] == [3, 3, 3, 3]
    p = tmp_path / "p001.png"
    img.save(p)
    assert isolate_staff(p, 2, uniform=True) == 4


def test_segments_drop_an_anchor_over_an_all_playing_stretch_that_cannot_hold_its_bars():
    breaks = [0, 4, 9, 13]
    anchors = {1: 5, 2: 16}                  # the 5 bars of system 2 would have to fill 11 slots
    playing = [False] * 17
    cuts = vt.segments(17, 1, 25, breaks, anchors, rest=playing)
    assert [c[2] for c in cuts] == [1, 5]    # anchor 2 (slot 16) ignored
    with_rest = playing[:]
    with_rest[6] = True                      # a rest bar there: the lost count is believable, anchor kept
    assert [c[2] for c in vt.segments(17, 1, 25, breaks, anchors, rest=with_rest)] == [1, 5, 16]
    assert [c[2] for c in vt.segments(17, 1, 25, breaks, anchors)] == [1, 5, 16]        # no rest flags: as before
    small = {1: 5, 2: 10}                    # a stretch that fits: the anchor stays
    assert [c[2] for c in vt.segments(17, 1, 25, breaks, small, rest=playing)] == [1, 5, 10]


def test_reference_scoring_merges_tied_notes_inside_a_bar(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from score_alignment import bar_match, read_score

    def xml(tied):
        def note(dur, tie=None):
            t = "".join(f'<tie type="{x}"/>' for x in (tie or []))
            return f"<note><pitch><step>C</step><octave>4</octave></pitch><duration>{dur}</duration>{t}</note>"
        notes = note(2, ["start"]) + note(1, ["stop"]) + note(1) if tied else note(3) + note(1)
        return ('<score-partwise><part-list><score-part id="P1"><part-name>Horn 1</part-name></score-part></part-list>'
                f'<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats>'
                f'<beat-type>4</beat-type></time></attributes>{notes}</measure></part></score-partwise>')
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    a.write_text(xml(True))
    b.write_text(xml(False))
    m = bar_match(read_score(a)[0], read_score(b)[0])
    assert m["extra"] == 1 and m["missing"] == 1             # two notes vs one longer note: a difference ...
    assert m["extra_tm"] == 0 and m["missing_tm"] == 0       # ... unless the tie is merged
