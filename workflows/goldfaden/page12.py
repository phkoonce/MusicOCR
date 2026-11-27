"""Rebuild score page 12 (missing from the printed copy: page 18 is printed in its place)
from the four flatbed part transcriptions, as the score run's page-12 OMR export.

  .venv/bin/python workflows/goldfaden/page12.py
  ./run.sh run input/goldfaden-score/Goldfaden_score.pdf --output output/goldfaden-score/... --from extract
"""
import copy
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "output/goldfaden-score/Goldfaden_score/ttl=30d/pages/p012/p012.musicxml"
# part -> (first, last) bar of the part file (1-based): movement II bars 10..40
SPAN = {1: (309, 339), 2: (318, 346), 3: (318, 346), 4: (319, 349)}
# part -> bar (counted within the span) that is a collapsed 3-bar rest
MULTIREST = {2: 18 - 9, 3: 38 - 9}
SYSTEM_STARTS = (1, 9, 17, 25)
BARS = 31


def part_bars(k):
    part = ET.parse(ROOT / f"output/goldfaden-flatbed/Goldfaden_h{k}/Goldfaden_h{k}.musicxml").getroot().find("part")
    first, last = SPAN[k]
    in_force, out = {}, []
    for i, m in enumerate(part.findall("measure"), 1):
        if i == first:
            before = dict(in_force)
        for attrs in m.findall("attributes"):
            for tag in ("divisions", "key", "clef"):
                if attrs.find(tag) is not None:
                    in_force[tag] = attrs.find(tag)
        if first <= i <= last:
            out.append(copy.deepcopy(m))
    return before, out


def whole_rest(divisions):
    m = ET.Element("measure")
    note = ET.SubElement(m, "note")
    ET.SubElement(note, "rest", measure="yes")
    ET.SubElement(note, "duration").text = str(2 * divisions)
    ET.SubElement(note, "voice").text = "1"
    return m


def build(k):
    before, bars = part_bars(k)
    divisions = int(before["divisions"].text)
    if k in MULTIREST:
        at = MULTIREST[k] - 1
        assert bars[at].find("note/rest") is not None, f"horn {k}: expected a rest at {at + 1}"
        keep = [c for c in bars[at] if c.tag == "direction"]
        three = [whole_rest(divisions) for _ in range(3)]
        for d in reversed(keep):
            three[0].insert(0, d)
        bars[at:at + 1] = three
    assert len(bars) == BARS, f"horn {k}: {len(bars)} bars"
    for n, m in enumerate(bars, 1):
        m.attrib.clear()
        m.set("number", str(n))
        for el in m.findall("print"):
            m.remove(el)
        for attrs in m.findall("attributes"):
            for el in list(attrs):
                if el.tag in ("measure-style", "time") or (n == 1 and el.tag in ("divisions", "key", "clef")):
                    attrs.remove(el)
            if not len(attrs):
                m.remove(attrs)
        for d in m.findall("direction"):
            words = [w.text or "" for w in d.iter("words")]
            if any(w.startswith("check:") for w in words) or (k != 1 and d.find(".//rehearsal") is not None):
                m.remove(d)
    head = ET.Element("attributes")
    head.append(copy.deepcopy(before["divisions"]))
    head.append(copy.deepcopy(before["key"]))
    clef = bars[0].find("attributes/clef")
    head.append(copy.deepcopy(before["clef"] if clef is None else clef))
    bars[0].insert(0, head)
    for n in SYSTEM_STARTS:
        bars[n - 1].insert(0, ET.Element("print", {"new-system": "yes"}))
    if k == 1:
        d = ET.Element("direction", placement="above")
        ET.SubElement(ET.SubElement(d, "direction-type"), "words", color="#7B1FA2").text = "check: page 12 rebuilt from parts"
        bars[0].insert(2, d)
    return bars


def main():
    # the score's own page 11 export supplies the header and part list
    template = PAGE.parent.parent / "p011/p011.musicxml"
    root = ET.parse(template).getroot()
    for k, part in enumerate(root.findall("part"), 1):
        for m in list(part):
            part.remove(m)
        part.extend(build(k))
    backup = PAGE.with_suffix(".empty.musicxml")
    if not backup.exists():
        shutil.copy(PAGE, backup)
    ET.ElementTree(root).write(PAGE, encoding="UTF-8", xml_declaration=True)
    print("wrote", PAGE)


if __name__ == "__main__":
    main()
