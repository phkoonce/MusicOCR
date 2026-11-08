import xml.etree.ElementTree as ET

from musicocr.scoreops.marks import MarksOptions, normalize_marks, _pairs, _scan


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


def test_reach_runs_a_hairpin_on_to_the_next_dynamic_two_to_four_beats_away():
    # crescendo over beats 0-1 of bar 1 (stop before note 2); f on the downbeat of bar 2: 2 beats past its end
    ms = [bar(1, (0, ("wedge", "crescendo")), (2, ("wedge", "stop"))), bar(2, (0, ("dyn", "f")))]
    stats, _ = normalize_marks([("Horn", ms)], MarksOptions(dedupe=False, hairpins=False, placement=False, mark=None))
    assert stats["reached"] == 1
    a, z = _pairs(_scan("Horn", ms))[0]
    assert (z.bar, z.pos) == (1, 4)      # the end of bar 1 = the downbeat of bar 2, where the f is

    near = [bar(1, (0, ("wedge", "crescendo")), (3, ("wedge", "stop"))), bar(2, (0, ("dyn", "f")))]   # only 1 beat short
    assert normalize_marks([("Horn", near)], MarksOptions(dedupe=False, hairpins=False, placement=False))[0]["reached"] == 0
    far = [bar(1, (0, ("wedge", "crescendo")), (1, ("wedge", "stop"))), bar(2), bar(3, (0, ("dyn", "f")))]
    assert normalize_marks([("Horn", far)], MarksOptions(dedupe=False, hairpins=False, placement=False))[0]["reached"] == 0


def test_placement_follows_the_majority_of_the_dynamics():
    ms = [bar(1, (0, ("dyn", "p")), (1, ("dyn", "f")), (2, ("dyn", "mf")))]
    ms[0].findall("direction")[1].set("placement", "above")
    stats, _ = normalize_marks([("Horn", ms)], MarksOptions(dedupe=False, hairpins=False, reach=False))
    assert stats["replaced"] == 1
    assert {d.get("placement") for d in ms[0].findall("direction")} == {"below"}


def test_cresc_text_becomes_a_dashed_line_to_the_next_dynamic():
    def words_bar(n, text):
        m = bar(n, notes=4)
        d = ET.fromstring(f'<direction placement="below"><direction-type><words>{text}</words></direction-type></direction>')
        m.insert(1 if n == 1 else 0, d)
        return m
    ms = [words_bar(1, "cresc."), bar(2), bar(3, (0, ("dyn", "f")))]
    stats, _ = normalize_marks([("Horn", ms)], MarksOptions(dedupe=False, hairpins=False, reach=False, placement=False))
    assert stats["lines"] == 1
    starts = [e.get("type") for m in ms for e in m.iter("dashes")]
    assert starts == ["start", "stop"]
    assert [m.find("direction/direction-type/dashes") is not None for m in ms] == [True, True, False]   # the stop sits at the end of bar 2 = where the f lands
    lone = [words_bar(1, "dim."), bar(2)]
    assert normalize_marks([("Horn", lone)], MarksOptions(dedupe=False, hairpins=False, reach=False, placement=False))[0]["lines"] == 0
