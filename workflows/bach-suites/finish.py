"""Structure fixes on top of correct.py: ossia bars out, movement headings and section breaks, rebuild.

    .venv/bin/python workflows/bach-suites/finish.py NAME...

Run after ``correct.py apply``, which starts from the pipeline baseline again (so both can be repeated).
Bar numbers in structure.json are the pipeline's, before any bar is removed.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from musicocr.assemble.mscz_sections import add_section_breaks  # noqa: E402
from musicocr.scoreops.layout import apply_layout  # noqa: E402
from musicocr.stages.convert import _convert  # noqa: E402

OUT = ROOT / "output/bach-suites-rescan"
MUSESCORE = "/Applications/MuseScore 4.app/Contents/MacOS/mscore"
HEADING = re.compile(r"(?i)^(check: )?(prelude|allemande|courante|sarabande|minuett?o|bourr.e|gavotte|gigue)\b")


def drop_bars(part: ET.Element, indices):
    """Remove bars, handing their attributes on to the following bar."""
    bars = part.findall("measure")
    for i in sorted(indices, reverse=True):
        gone = bars[i]
        held = gone.find("attributes")
        if held is not None and i + 1 < len(bars):
            nxt = bars[i + 1]
            have = nxt.find("attributes")
            if have is None:
                nxt.insert(1 if nxt.find("print") is not None else 0, held)
            else:
                have.extend(c for c in held if have.find(c.tag) is None)
        if gone.find("print") is not None and i + 1 < len(bars) and bars[i + 1].find("print") is None:
            bars[i + 1].insert(0, gone.find("print"))
        part.remove(gone)


def plain(text):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def heading(measure: ET.Element, title: str, tempo: str):
    for d in measure.findall("direction"):
        if any(HEADING.match(w.text or "") or (tempo and plain(w.text).endswith(plain(tempo)))
               for w in d.iter("words")):
            measure.remove(d)
    at = next((i for i, c in enumerate(measure) if c.tag == "note"), len(measure))
    for text, weight in ((tempo, None), (title, "bold")):
        if not text:
            continue
        d = ET.Element("direction", placement="above")
        w = ET.SubElement(ET.SubElement(d, "direction-type"), "words")
        w.text = text
        if weight:
            w.set("font-weight", weight)
        measure.insert(at, d)


def final_barline(measure: ET.Element):
    for b in measure.findall("barline"):
        if b.get("location") == "right":
            if b.find("repeat") is None:
                style = b.find("bar-style")
                (style if style is not None else ET.SubElement(b, "bar-style")).text = "light-heavy"
            return
    ET.SubElement(ET.SubElement(measure, "barline", location="right"), "bar-style").text = "light-heavy"


def finish(name: str):
    spec = json.loads((Path(__file__).parent / "structure.json").read_text())[name]
    target = OUT / name / f"{name}.musicxml"
    tree = ET.parse(target)
    root = tree.getroot()
    audit = Path(__file__).parent / "answers" / name / "audit.json"
    added = json.loads(audit.read_text())["inserts"] if audit.exists() else []
    ossia = [i - 1 for i in spec.get("ossia", [])]
    expected = len(ET.parse(OUT / "ttl=30d/arbitration" / f"{name}.pipeline-baseline.musicxml").getroot()
                   .find("part").findall("measure")) + len(added)
    if len(root.find("part").findall("measure")) != expected:
        sys.exit(f"{name}: run correct.py apply and audit.py apply first (see rebuild.sh)")
    starts = []
    for part in root.findall("part"):
        every = part.findall("measure")
        # pipeline bar number -> bar; bars added by audit.py are numbered x0, x1, ...
        own = [m for m in every if not m.get("number", "").startswith("x")]
        bars = own
        for d in [d for m in bars for d in m.findall("direction")]:
            for w in d.iter("words"):
                if re.match(r"(?i)check: ossia", w.text or ""):
                    w.text = ""
                elif w.text == "check: allarg":
                    w.text = "allarg."
        for number, text in spec.get("words", {}).items():
            for d in bars[int(number) - 1].findall("direction"):
                for w in d.iter("words"):
                    if (w.text or "").replace("check: ", "") == text[0]:
                        w.text = text[1]
        fixed = {"check: poco mt.": "poco rit.", "check: rall. last time": "rall. last time", "check: cres": "cres -",
                 "check: cen": "- cen -", "tempo": "a tempo"}
        for m in every:
            sounding = any(n.find("pitch") is not None for n in m.findall("note"))
            for d in m.findall("direction"):
                if d.find("direction-type/rehearsal") is not None:      # the print has none
                    m.remove(d)
                    continue
                for w in d.iter("words"):
                    text = w.text or ""
                    if text in fixed:
                        w.text = fixed[text]
                    elif sounding and text.startswith("check: bar cleared"):
                        w.text = ""
                    elif HEADING.match(text) and text.startswith("check: ") or re.match(r"check: (Allegro|Quasi)", text):
                        w.text = ""
        for m in bars:                      # emptied text
            for d in m.findall("direction"):
                words = list(d.iter("words"))
                if words and not any(w.text for w in words):
                    m.remove(d)
        movements = spec.get("movements", [])
        first_of = {}
        for number, title, tempo in movements:
            # a pickup that audit.py put back opens the movement: it takes the heading and signatures
            opening = bars[number - 1]
            at = every.index(opening)
            while at and every[at - 1].get("number", "").startswith("x") and \
                    added[int(every[at - 1].get("number")[1:])]["first_on_line"]:
                at -= 1
            first = first_of[number] = every[at]
            held = opening.find("attributes")
            if first is not opening and held is not None:
                opening.remove(held)
                first.insert(1 if first.find("print") is not None else 0, held)
            heading(first, title, tempo)
            if number > 1:
                at = every.index(first_of[number]) - 1
                while every[at] in [own[i] for i in ossia]:
                    at -= 1
                final_barline(every[at])
        starts = [first_of[number] for number, _, _ in movements]
        drop_bars(part, [every.index(own[i]) for i in ossia])
        kept = part.findall("measure")
        for n, m in enumerate(kept, 1):
            m.set("number", str(n))
        starts = [kept.index(m) for m in starts if m in kept]
    header = target.read_text("utf-8").split("<score-partwise", 1)[0]
    target.write_text(header + ET.tostring(root, encoding="unicode"), "utf-8")

    mscz = OUT / name / f"{name}.mscz"
    scratch = OUT / name / "ttl=30d"
    _convert(MUSESCORE, target, mscz, print)
    add_section_breaks(mscz, [s - 1 for s in starts if s > 0])
    with tempfile.TemporaryDirectory() as t:
        pages, offset = [], 0
        for page in sorted(scratch.glob(f"{name}.p[0-9][0-9][0-9].musicxml")):
            doc = ET.parse(page)
            counts = 0
            for part in doc.getroot().findall("part"):
                local = part.findall("measure")
                counts = len(local)
                gone = [local[i - offset] for i in ossia if offset <= i < offset + counts]
                for item in reversed(added):
                    i = item["after"] - offset          # bar of this page it follows (1-based)
                    # a bar that opens a line belongs to the page of the bar it precedes
                    if not (0 <= i < counts if item["first_on_line"] else 1 <= i <= counts):
                        continue
                    made = ET.fromstring(item["xml"])
                    now = part.findall("measure")
                    at = now.index(local[i - 1]) + 1 if i > 0 else 0
                    if item["first_on_line"] and at < len(now) and now[at].find("print") is not None:
                        marker = now[at].find("print")
                        now[at].remove(marker)
                        made.insert(0, marker)
                    part.insert(list(part).index(now[at]) if at < len(now) else len(part), made)
                now = part.findall("measure")
                drop_bars(part, [now.index(m) for m in gone])
            offset += counts
            copy = Path(t) / page.name
            doc.write(copy, encoding="utf-8", xml_declaration=True)
            pages.append(copy)
        try:
            apply_layout(mscz, mscz, pages, scratch / "source.pdf", MUSESCORE, "pdftoppm", print)
        except Exception as exc:  # noqa: BLE001 - keep MuseScore's own layout
            print("layout left as MuseScore made it:", exc)
    _convert(MUSESCORE, mscz, scratch / "render.pdf", print)
    print(f"{name}: {len(kept)} bars, {len(ossia)} ossia bars removed, {len(starts)} movements")


if __name__ == "__main__":
    for each in sys.argv[1:]:
        finish(each)
