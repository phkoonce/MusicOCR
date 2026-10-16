import xml.etree.ElementTree as ET

from musicocr.marks import MarksOptions, normalize_marks, _pairs, _scan


def bar(n, *items, notes=4):
    """A 4/4 bar of ``notes`` quarter notes with directions placed before the note index given."""
    out = [f'<measure number="{n}">']
    if n == 1:
        out.append("<attributes><divisions>4</divisions></attributes>")
    for i in range(notes):
        for kind, val in (x for at, x in items if at == i):
            if kind == "dyn":
                out.append(f'<direction placement="below"><direction-type><dynamics><{val}/></dynamics>'
                           f'</direction-type></direction>')
            else:
                out.append(f'<direction><direction-type><wedge type="{val}"/></direction-type></direction>')
        out.append("<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>")
    for at, (kind, val) in items:
        if at >= notes:
            out.append(f'<direction><direction-type><wedge type="{val}"/></direction-type></direction>')
    out.append("</measure>")
    return ET.fromstring("".join(out))


def part(*bars):
    return list(bars)


def test_dedupe_drops_same_dynamic_in_a_bar_but_keeps_different_ones():
    m = bar(1, (0, ("dyn", "p")), (0, ("dyn", "p")), (2, ("dyn", "f")))
    stats, _ = normalize_marks([("Horn 1", [m])], MarksOptions(hairpins=False))
    assert stats["deduped"] == 1
    assert [k.value for k in _scan("x", [m]).marks] == ["p", "f"]


def test_one_note_hairpin_extends_to_bar_end_and_to_next_dynamic():
    # crescendo on one note of bar 1; the next dynamic is the downbeat of bar 3 (one bar past bar 1's end)
    ms = [bar(1, (1, ("wedge", "crescendo")), (2, ("wedge", "stop"))), bar(2), bar(3, (0, ("dyn", "f")))]
    normalize_marks([("Tuba", ms)], MarksOptions(dedupe=False, mark=None))
    p = _scan("Tuba", ms)
    a, z = _pairs(p)[0]
    assert (z.bar, z.pos) == (2, 4)             # runs through bar 2, stops where the f lands

    lone = [bar(1, (1, ("wedge", "crescendo")), (2, ("wedge", "stop"))), bar(2)]
    normalize_marks([("Tuba", lone)], MarksOptions(dedupe=False, mark=None))
    a, z = _pairs(_scan("Tuba", lone))[0]
    assert (z.bar, z.pos) == (1, 4)             # no dynamic nearby: just the end of its bar


def test_hairpin_never_runs_into_the_next_one():
    ms = [bar(1, (0, ("wedge", "crescendo")), (1, ("wedge", "stop")), (3, ("wedge", "diminuendo")),
              (4, ("wedge", "stop")))]
    normalize_marks([("Tuba", ms)], MarksOptions(dedupe=False, mark=None))
    first, second = _pairs(_scan("Tuba", ms))
    assert first[1].pos <= second[0].pos


def test_fill_gives_the_section_dynamic_to_the_part_that_lost_it():
    parts = [(f"Horn {i}", [bar(1, *([(0, ("dyn", "f"))] if i <= 4 else []))]) for i in range(1, 6)]
    stats, _ = normalize_marks(parts, MarksOptions(dedupe=False, hairpins=False, fill=True, mark=None))
    assert stats["filled"] == 1
    assert [m.value for m in _scan("h", parts[4][1]).marks] == ["f"]
    other = [("Tuba", [bar(1)])]                 # a lone part has no section to vote
    assert normalize_marks(other, MarksOptions(fill=True))[0]["filled"] == 0


def test_rules_parse():
    assert MarksOptions.parse("all").fill and not MarksOptions.parse("dedupe").hairpins
    try:
        MarksOptions.parse("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown rule accepted")
