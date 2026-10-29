import xml.etree.ElementTree as ET

from musicocr.text_system import text_key, text_keys, unify_system_text
from musicocr.unnumbered import align_by_rhythm


def bar(*words, placement="above"):
    m = ET.Element("measure")
    for w in words:
        d = ET.SubElement(m, "direction", placement=placement)
        ET.SubElement(ET.SubElement(d, "direction-type"), "words").text = w
        ET.SubElement(d, "staff").text = "1"
    ET.SubElement(m, "note")
    return m


def test_text_key_normalises_and_skips_other_directions():
    assert text_key(bar("A tempo")[0]) == "atempo"
    assert text_key(bar("rit.")[0]) == "rit"
    assert text_key(bar("p", placement="below")[0]) is None
    assert text_key(bar("check: sehr ruhig")[0]) is None


def test_agreed_text_becomes_one_system_text_in_the_first_part():
    parts = {1: [bar(), bar("Allegro")], 2: [bar("rit."), bar("allegro", "Solo")], 3: [bar(), bar("Allegro")]}
    lines = unify_system_text(parts)
    assert lines and "1 text" in lines[0]
    d = parts[1][1].find("direction")
    assert d.get("system") == "only-top" and d.find("staff") is None
    assert parts[2][1].findall("direction")[0].findtext("direction-type/words") == "Solo"
    assert len(parts[3][1].findall("direction")) == 0
    assert parts[2][0].find("direction").get("system") is None      # printed by one part only: stays staff text


def test_system_text_moves_into_the_first_part_when_it_lacks_a_copy():
    parts = {1: [bar()], 2: [bar("rit.")], 3: [bar("rit.")]}
    unify_system_text(parts)
    assert parts[1][0].find("direction").get("system") == "only-top"
    assert parts[2][0].find("direction") is None and parts[3][0].find("direction") is None


def test_text_steers_the_rhythm_alignment():
    same = frozenset({0, 12})
    src = [same, same, same]
    ref = [[same]] * 3
    src_text = [frozenset(), frozenset({"rit"}), frozenset()]
    ref_text = [[frozenset()], [frozenset()], [frozenset({"rit"})]]
    # one source bar short of three slots: the text decides where the rest goes
    slots = align_by_rhythm(src[:2], ref, [frozenset(), frozenset({"rit"})], ref_text)
    assert slots[2] == 1                       # the "rit." bar lands on the slot where the others print it
