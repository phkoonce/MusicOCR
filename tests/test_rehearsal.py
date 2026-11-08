import xml.etree.ElementTree as ET

from musicocr.assemble.rehearsal import letter, unify_rehearsal


def bar(*marks):
    m = ET.Element("measure")
    for _ in marks:
        d = ET.SubElement(m, "direction", placement="below")
        ET.SubElement(ET.SubElement(d, "direction-type"), "rehearsal").text = "#"
        ET.SubElement(d, "staff").text = "1"
    ET.SubElement(m, "note")
    return m


def letters(measure):
    return [r.text for r in measure.iter("rehearsal")]


def test_letters():
    assert [letter(i) for i in (1, 2, 26, 27, 28)] == ["A", "B", "Z", "AA", "AB"]


def test_agreed_marks_are_lettered_in_order_and_written_once_in_the_first_part():
    n = 8
    def part(*at):
        return [bar("x") if i in at else bar() for i in range(n)]
    parts = {1: part(1, 5), 2: part(1, 5, 7), 3: part(2, 5)}          # bars 2 (+-1) and 6 agreed; bar 8 only part 2
    lines = unify_rehearsal(parts)
    assert lines and "2 circled" in lines[0]
    assert [letters(m) for m in parts[1]].count(["A"]) == 1 and [letters(m) for m in parts[1]].count(["B"]) == 1
    assert letters(parts[1][5]) == ["B"]
    assert not any(letters(m) for m in parts[2] + parts[3])
    assert not any(r.text == "#" for ms in parts.values() for m in ms for r in m.iter("rehearsal"))


def test_a_mark_only_one_part_found_is_dropped():
    parts = {1: [bar("x"), bar()], 2: [bar(), bar()]}
    unify_rehearsal(parts)
    assert not any(letters(m) for ms in parts.values() for m in ms)
    assert not any(m.find("direction") is not None for ms in parts.values() for m in ms)
