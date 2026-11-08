import xml.etree.ElementTree as ET

from musicocr.assemble.repeats import bar_marks, unify_repeats


def src(*lines):
    m = ET.Element("measure")
    for loc, kids in lines:
        bl = ET.SubElement(m, "barline", location=loc)
        for tag, attrs in kids:
            ET.SubElement(bl, tag, **attrs)
    ET.SubElement(m, "note")
    return (m, 1)


FWD = ("left", [("repeat", {"direction": "forward"})])
BACK = ("right", [("ending", {"number": "1", "type": "stop"}), ("repeat", {"direction": "backward"})])
START1 = ("left", [("ending", {"number": "1", "type": "start"})])


def built(n):
    return [ET.fromstring("<measure><note/></measure>") for _ in range(n)]


def test_bar_marks_reads_repeats_and_endings():
    assert bar_marks(src(FWD)[0]) == {"forward"}
    assert bar_marks(src(START1, BACK)[0]) == {"start 1", "stop 1", "backward"}


def test_marks_read_by_two_parts_are_written_to_every_part():
    bars = {"a": [src(FWD), src(START1, BACK)], "b": [src(FWD), src(START1, BACK)],
            "c": [src(), src(START1)], "d": [src(), src()]}
    out = {i: built(2) for i in range(1, 5)}
    lines = unify_repeats(bars, out)
    assert lines and "voted" in lines[0]
    for i, ms in out.items():
        assert ET.tostring(ms[0].find("barline")).count(b"forward") == 1
        assert bar_marks(ms[1]) == ({"start 1", "stop 1", "backward"} if i == 1 else {"backward"})
        assert ms[1].find("barline[@location='right']/bar-style").text == "light-heavy"


def test_a_mark_only_one_part_read_is_not_written():
    bars = {"a": [src(FWD)], "b": [src()], "c": [src()], "d": [src()]}
    out = {i: built(1) for i in range(1, 5)}
    assert unify_repeats(bars, out) == []
    assert all(bar_marks(ms[0]) == set() for ms in out.values())


def test_key_change_at_a_system_start_slides_to_the_bar_another_part_reads_mid_system():
    from musicocr.assemble.combine import slide_key_changes

    def bar(new_system):
        m = ET.Element("measure")
        if new_system:
            ET.SubElement(m, "print", {"new-system": "yes"})
        return (m, 1)
    sysstart = [True, False, False, False, False, False]          # bars 0 and ... system start at bar 0 only
    a = [bar(i in (0, 2)) for i in range(6)]
    b = [bar(i == 0) for i in range(6)]
    # part a puts C -> F at bar 2 (a system start); part b reads it at bar 4 (mid-system)
    key_by = {"a": [0, 0, -1, -1, -1, -1], "b": [0, 0, 0, 0, -1, -1]}
    assert slide_key_changes(key_by, {"a": a, "b": b}) == 1
    assert key_by["a"] == [0, 0, 0, 0, -1, -1]


def test_key_change_at_a_system_start_slides_back_to_the_bar_another_part_reads_mid_system():
    from musicocr.assemble.combine import slide_key_changes

    def bar(new_system):
        m = ET.Element("measure")
        if new_system:
            ET.SubElement(m, "print", {"new-system": "yes"})
        return (m, 1)
    a = [bar(i == 0) for i in range(6)]
    b = [bar(i in (0, 4)) for i in range(6)]
    # part a reads F -> C at bar 2 (mid-system); part b hung it on bar 4, the next system's first bar
    key_by = {"a": [-1, -1, 0, 0, 0, 0], "b": [-1, -1, -1, -1, 0, 0]}
    assert slide_key_changes(key_by, {"a": a, "b": b}) == 1
    assert key_by["b"] == [-1, -1, 0, 0, 0, 0]
