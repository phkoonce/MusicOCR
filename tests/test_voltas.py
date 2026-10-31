import xml.etree.ElementTree as ET

from musicocr.assemble import voltas as vt


def bar(system=False, ending=None, stop=False):
    m = ET.Element("measure")
    if system:
        ET.SubElement(m, "print", {"new-system": "yes"})
    if ending or stop:
        bl = ET.SubElement(m, "barline")
        if ending:
            ET.SubElement(bl, "ending", {"number": str(ending), "type": "start"})
        if stop:
            ET.SubElement(bl, "ending", {"number": str(stop), "type": "stop"})
    return m, 1


def test_roles_follow_the_ending_brackets_across_a_page_break():
    p1 = [bar(), bar(ending=1), bar(stop=1)]
    p2 = [bar(ending=2), bar(stop=2), bar()]
    assert vt.roles([p1, p2]) == [[0, 1, 1], [2, 2, 0]]


def test_the_second_ending_goes_back_to_the_first_endings_number():
    pg = [bar(system=True), bar(), bar(ending=1, stop=1), bar(ending=2, stop=2), bar()]
    w = vt.walk([pg], vt.roles([pg]), [[0]], [{0: 10}], [None])
    assert w.numbers == [[10, 11, 12, 12, 13]]
    assert w.voltas == {12: [1, 1]}


def test_the_grid_gives_every_notated_bar_its_own_slot():
    g = vt.Grid([(12, 1, 1), (20, 2, 2)])
    assert [g.slot(n) for n in (11, 13, 19, 22)] == [11, 14, 20, 25]
    assert (g.slot(12, 1), g.slot(12, 2)) == (12, 13)
    assert (g.slot(20, 2), g.slot(21, 2)) == (23, 24)


def test_only_endings_most_parts_saw_become_part_of_the_grid():
    seen = vt.Walk(voltas={12: [1, 1], 30: [1, 1]})
    other = vt.Walk(voltas={12: [1, 1]})
    assert vt.consensus([seen, other, other]) == [(12, 1, 1)]


def test_a_page_is_cut_at_its_anchored_systems_and_a_wrong_anchor_is_ignored():
    # 3 systems of 4 bars read; the second system's number says bar 20 on a page that starts at 1: kept,
    # the third's says 3 (before the second): dropped
    assert vt.segments(12, 1, 12, [0, 4, 8], {1: 5}) == [(0, 4, 1, 5), (4, 12, 5, 13)]
    assert vt.segments(12, 1, 12, [0, 4, 8], {1: 5, 2: 5}) == [(0, 4, 1, 5), (4, 12, 5, 13)]
