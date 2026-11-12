import xml.etree.ElementTree as ET

from musicocr.assemble.anchored import lay_out_by_marks, mark_numbers, part_marks
from musicocr.assemble.rehearsal import unify_rehearsal


def bar(onset=0, mark=None):
    """A bar with one note starting at ``onset`` quarter-ticks (so bars can differ), and a rehearsal placeholder."""
    m = ET.Element("measure")
    if mark is not None:
        d = ET.SubElement(m, "direction", placement="above")
        ET.SubElement(ET.SubElement(d, "direction-type"), "rehearsal").text = mark
    if onset:
        ET.SubElement(ET.SubElement(m, "forward"), "duration").text = str(onset)
    n = ET.SubElement(m, "note")
    ET.SubElement(n, "pitch")
    ET.SubElement(n, "duration").text = "1"
    return (m, 4)


def run(marks_at, n, skip=()):
    """n bars; ``marks_at`` = {bar index: '#k'}; bar i has onset i%3 so neighbours differ."""
    return [bar(i % 3, marks_at.get(i)) for i in range(n) if i not in skip]


def test_mark_numbers_and_movements():
    r = [bar(0, "#1"), bar(0, "#2"), bar(0, "#3"), bar(0, "#4"), bar(0, "#1"), bar(0, "#"), bar(0, "#2")]
    assert mark_numbers(r[5][0]) == [None]
    assert part_marks(r) == {(1, 1): 0, (1, 2): 1, (1, 3): 2, (1, 4): 3, (2, 1): 4, (2, 2): 6}


def test_a_part_that_lost_a_bar_is_corrected_inside_its_stretch_only():
    at = {0: "#1", 4: "#2", 8: "#3", 12: "#4", 16: "#5"}
    parts = {"a": run(at, 20), "b": run(at, 20), "c": run(at, 20)}
    # d lost bar 6 (inside the stretch after mark 2) and carries the marks on its own bars
    d = [bar(i % 3, at.get(i)) for i in range(20) if i != 6]
    data = {pid: {"pages": [r]} for pid, r in {**parts, "d": d}.items()}
    report = []
    total, bars, flags = lay_out_by_marks(data, report)
    assert total == 20
    assert all(len(b) == 20 for b in bars.values())
    assert all(s is not None for s in bars["a"])
    assert bars["d"].count(None) == 1 and bars["d"][6] is None        # the lost bar is put back where it was
    assert bars["d"][8] is d[7]                                       # nothing after it moved
    assert "d" in flags and 7 in flags["d"]


def test_too_few_marks_gives_up():
    data = {p: {"pages": [run({0: "#1"}, 6)]} for p in "abc"}
    assert lay_out_by_marks(data, []) is None


def test_printed_numbers_are_kept_by_unify_rehearsal():
    def part():
        return [bar(0, "#7")[0] if i == 2 else bar(0)[0] for i in range(4)]
    parts = {1: part(), 2: part()}
    unify_rehearsal(parts)
    assert [r.text for r in parts[1][2].iter("rehearsal")] == ["7"]



def test_letters_are_a_style_of_their_own():
    from musicocr.assemble.labels import book_style, label_values, ordinal
    assert ordinal("AA") == 27 and ordinal("C") == 3 and ordinal("12") == 12
    r = [bar(0, "#A"), bar(0, "#B"), bar(0, "#C"), bar(0, "#8"), bar(0, "#D"), bar(0, "B"), bar(0, "#")]
    assert book_style(m for m, _ in r) == "letter"
    assert label_values(r[3][0], "letter") == [None]                  # the 8 is a misread B
    assert part_marks(r, "letter")[(1, 4)] == 4
    assert part_marks(r, "letter")[(1, 3)] == 2


def test_lettered_marks_align_like_numbered_ones():
    at = {0: "#A", 4: "#B", 8: "#C", 12: "#D"}
    d = [bar(i % 3, at.get(i)) for i in range(16) if i != 6]
    data = {p: {"pages": [run(at, 16)]} for p in "abc"}
    data["d"] = {"pages": [d]}
    total, bars, flags = lay_out_by_marks(data, [])
    assert total == 16 and bars["d"][6] is None and 7 in flags["d"]


def test_unread_labels_are_filled_in_the_books_style():
    def part(mark):
        return [bar(0, mark)[0] if i == 2 else bar(0)[0] for i in range(4)]
    parts = {1: part("#"), 2: part("#")}
    parts[1][0].append(ET.Element("x"))
    unify_rehearsal(parts)
    assert [r.text for r in parts[1][2].iter("rehearsal")] == ["A"]    # nothing read anywhere: lettered in order


def test_marks_check_a_layout_that_drifted():
    from musicocr.assemble.anchored import check_against_marks
    at = {2: "#A", 6: "#B", 10: "#C"}
    good = run(at, 14)
    late = run({k + 3: v for k, v in at.items()}, 14)
    flags, report = {}, []
    check_against_marks({"a": good, "b": list(good), "c": late}, flags, report)
    assert set(flags) == {"c"} and "rehearsal mark" in report[0]
