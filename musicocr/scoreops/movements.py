"""Movements of a multi-movement piece, from the centred Roman numeral over each one's first line,
or the centred "Version A" / "Version B" of a piece printed in several settings.

OMR reads the music but not the layout around it: a part whose movements are headed "I.", "II." comes
out as one run of bars. This finds those headings on the page images (full-page OCR), with the tempo
line under each, and writes them into the merged score:

* the heading and the tempo mark over the movement's first bar,
* a final (light-heavy) barline on the bar before it,
* and, in the ``.mscz``, a section break there (``musicocr.assemble.mscz_sections``), so the movement
  starts a new line and its bar numbers restart at 1.

A heading must be a lone Roman numeral (or "Version" and a letter), centred on the page, just above a staff. Headings are used only
when their numbers go up and show more than one movement; a numeral the OCR missed is reported.
"""
from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

Log = Callable[[str], None]

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_HEADING = re.compile(r"^([IVX]{1,4})\.?$")
_VERSION = re.compile(r"^Version\s*([A-F])\.?$")
_BPM = re.compile(r"=\s*(?:c\.|ca\.)?\s*(\d{2,3}(?:\s*[-–]\s*\d{2,3})?)")


@dataclass
class Heading:
    number: int
    heading: str                  # as printed: "II."
    staff: int                    # 0-based index of the staff it stands over, top to bottom
    text: str = ""                # tempo words: "Fast and rhythmic"
    bpm: str = ""                 # "56", "80-84"
    dotted: Optional[bool] = None # metronome note dotted; None = could not tell
    bar: int = 0                  # 0-based bar of the merged score (set by ``place``)


def is_heading_text(text: str) -> bool:
    """A section heading by itself: a Roman numeral with its dot, or "Version" and a letter."""
    t = text.strip()
    m = _HEADING.match(t)
    return bool(_VERSION.match(t) or (m and t.endswith(".") and m.group(1) in _ROMAN))


def _tempo(words: Sequence[dict], img=None) -> tuple:
    """``(text, bpm, dotted)`` of the first line of ``words`` (those between heading and staff)."""
    if img is not None:                    # a source / composer credit set flush right is no tempo line
        words = [w for w in words if w["xyxy"][0] < 0.55 * img.width]
    if not words:
        return "", "", None
    first = min(words, key=lambda w: w["xyxy"][1])
    mid = (first["xyxy"][1] + first["xyxy"][3]) / 2
    line = sorted((w for w in words if w["xyxy"][1] <= mid <= w["xyxy"][3]), key=lambda w: w["xyxy"][0])
    for k in range(1, len(line)):          # a wide gap ends the tempo line: a source credit at the right is not part of it
        if line[k]["xyxy"][0] - line[k - 1]["xyxy"][2] > 6 * (first["xyxy"][3] - first["xyxy"][1]):
            line = line[:k]
            break
    full = " ".join(w["text"] for w in line)
    m = _BPM.search(full)
    text = re.split(r"[(=]", full)[0].strip(" *")
    if not re.match(r"[A-Za-z]{2}", text):
        text = ""
    dotted = None
    if m and img is not None:
        import numpy as np
        from musicocr.text.layer import is_dotted_beat
        end = next((i for i, w in enumerate(line) if re.search(r"\d{2}", w["text"])), len(line) - 1)
        boxes = [w["xyxy"] for w in line[:end] or line]   # not the number: "80-84" has a dash like "="
        crop = img.crop((int(min(b[0] for b in boxes)), int(min(b[1] for b in boxes)),
                         int(max(b[2] for b in boxes)), int(max(b[3] for b in boxes))))
        dotted = is_dotted_beat(np.asarray(crop.convert("L")) < 128)
    return text, re.sub(r"\s", "", m.group(1)) if m else "", dotted


def _join_versions(words: Sequence[dict]) -> List[dict]:
    """"Version" and its letter read as two words become one."""
    out = list(words)
    for w in words:
        if w["text"].strip() != "Version":
            continue
        x0, y0, x1, y1 = w["xyxy"]
        for o in words:
            if re.fullmatch(r"[A-F]\.?", o["text"].strip()) and 0 <= o["xyxy"][0] - x1 < 2 * (y1 - y0) \
                    and y0 < (o["xyxy"][1] + o["xyxy"][3]) / 2 < y1 and w in out and o in out:
                out.remove(w)
                out.remove(o)
                out.append({"text": f"Version {o['text'].strip()}",
                            "xyxy": [x0, min(y0, o["xyxy"][1]), o["xyxy"][2], max(y1, o["xyxy"][3])]})
                break
    return out


def find_headings(words: Sequence[dict], staves: Sequence, width: int, img=None) -> List[Heading]:
    """Movement headings among a page's OCR ``words`` (``{"text", "xyxy"}``); ``staves`` have
    ``top``/``bottom``/``sp`` and are sorted top to bottom."""
    out: List[Heading] = []
    words = _join_versions(words)
    for w in words:
        text_ = w["text"].strip()
        m, v = _HEADING.match(text_), _VERSION.match(text_)
        if v:
            number = ord(v.group(1)) - ord("A") + 1
        elif m and m.group(1) in _ROMAN:
            number = _ROMAN[m.group(1)]
        else:
            continue
        x0, y0, x1, y1 = w["xyxy"]
        cy = (y0 + y1) / 2
        if abs((x0 + x1) / 2 - width / 2) > 0.08 * width:
            continue
        if any(o is not w and o["xyxy"][1] < cy < o["xyxy"][3]
               and max(o["xyxy"][0] - x1, x0 - o["xyxy"][2]) < 0.15 * width
               and not (o["text"].strip() == w["text"].strip()) for o in words):
            continue                                              # part of a longer line
        k = next((i for i, s in enumerate(staves) if s.top > y1), None)
        if k is None or staves[k].top - y1 > 16 * staves[k].sp or (k and staves[k - 1].bottom > y0):
            continue
        if any(h.staff == k for h in out):
            continue
        between = [o for o in words if o is not w and (o["xyxy"][1] + o["xyxy"][3]) / 2 > cy + (y1 - y0) / 4
                   and o["xyxy"][3] <= staves[k].top]
        text, bpm, dotted = _tempo(between, img)
        if v and text:                     # under a "Version" heading only a known marking is a tempo line
            from musicocr.text.classify import classify
            marking = classify(text)
            text = marking.text if marking.kind in ("tempo", "tempo_change", "expression", "technique") else ""
        out.append(Heading(number, text_, k, text, bpm, dotted))
    return sorted(out, key=lambda h: h.staff)


def system_starts(page_xml: Path) -> tuple:
    """``(bar count, 0-based bar that opens each system)`` of a page's MusicXML."""
    part = ET.parse(page_xml).getroot().find("part")
    ms = part.findall("measure") if part is not None else []
    return len(ms), [i for i, m in enumerate(ms) if i == 0 or m.find("print[@new-system='yes']") is not None]


def _staves_per_system(page_xml: Path) -> int:
    """Staves in one system of a page's score: one per part, or the staves a part declares."""
    root = ET.parse(page_xml).getroot()
    return max(1, sum(int(p.findtext("measure/attributes/staves") or 1) for p in root.findall("part")))


def heading_bands(staves: Sequence) -> List[tuple]:
    """``(staff, top row, bottom row)`` of the places a heading can hide: over the page's first staff,
    and over a staff with a clearly wider gap than usual above it."""
    gaps = sorted(b.top - a.bottom for a, b in zip(staves, staves[1:]))
    usual = gaps[len(gaps) // 2] if gaps else 0
    out = []
    for k, s in enumerate(staves):
        top = staves[k - 1].bottom if k else max(0, s.top - 16 * s.sp)
        if k == 0 or s.top - top > 1.25 * usual:
            out.append((k, int(top), int(s.top)))
    return [(k, a, b) for k, a, b in out if b - a > 2]


def _band_words(img, top: int, bottom: int, python: Optional[str], log: Log) -> List[dict]:
    """OCR of the middle of one band: a large lone numeral that full-page OCR passes over is read
    from a small crop."""
    from musicocr.text.ocr_backends import ocr_words
    x0 = int(0.05 * img.width)            # wide: the tempo line under a heading starts at the left margin
    with tempfile.TemporaryDirectory() as t:
        crop = Path(t) / "band.png"
        img.crop((x0, top, int(0.95 * img.width), bottom)).save(crop)
        words = ocr_words(crop, log=log, python=python)
    return [{"text": w["text"], "xyxy": [w["xyxy"][0] + x0, w["xyxy"][1] + top,
                                         w["xyxy"][2] + x0, w["xyxy"][3] + top]} for w in words]


def page_headings(png: Path, log: Log = print, python: Optional[str] = None) -> tuple:
    """``(headings, staff count)`` of one page image. ``python`` runs the rapidocr backend."""
    from PIL import Image
    from musicocr.assemble.alignment.systems import all_staves
    from musicocr.text.ocr_backends import ocr_words
    from musicocr.text.words import Staff

    img = Image.open(png).convert("L")
    half = img.resize((max(1, img.width // 2), max(1, img.height // 2)))
    staves = [Staff(t * 2, b * 2, left * 2) for t, b, left in all_staves(half)]
    if not staves:
        return [], 0
    found = find_headings(ocr_words(png, log=log, python=python), staves, img.width, img)
    for k, top, bottom in heading_bands(staves):
        if all(h.staff != k for h in found):
            words = _band_words(img, top, bottom, python, log)
            found += [h for h in find_headings(words, staves, img.width, img) if h.staff == k]
    return sorted(found, key=lambda h: h.staff), len(staves)


def in_sequence(headings: Sequence[Heading]) -> List[Heading]:
    """The headings if their numbers only go up and they show there is more than one movement
    (two or more of them, or a lone "II"), else nothing."""
    nums = [h.number for h in headings]
    ok = (len(nums) > 1 or nums == [2]) and all(a < b for a, b in zip(nums, nums[1:]))
    return list(headings) if ok else []


def detect(pages: Sequence[tuple], log: Log = print, python: Optional[str] = None) -> List[dict]:
    """Movements of a score from its ``(page image, page MusicXML)`` pairs, in merge order."""
    found: List[Heading] = []
    offset = 0
    for png, xml in pages:
        bars, starts = system_starts(xml)
        if png.exists():
            heads, n_staves = page_headings(png, log=log, python=python)
            per_system = _staves_per_system(xml)
            for h in heads:
                if h.staff and len(starts) * per_system != n_staves:
                    log(f"  movements: {h.heading} on {png.stem} not placed "
                        f"({n_staves} staves found, {len(starts)} lines read)")
                    continue
                h.bar = offset + (starts[h.staff // per_system] if h.staff else 0)
                found.append(h)
        offset += bars
    kept = in_sequence(found)
    if found and not kept and any(h.number >= 2 for h in found):
        log(f"  movements: headings {[h.heading for h in found]} do not count up; ignored")
    missed = sorted(set(range(1, kept[-1].number + 1)) - {h.number for h in kept}) if kept else []
    if kept and missed and missed != [1]:
        log(f"  movements: no heading found for movement(s) {missed}; add those by hand")
    return [asdict(h) for h in kept]


def _direction(*types: ET.Element) -> ET.Element:
    d = ET.Element("direction", placement="above")
    for t in types:
        ET.SubElement(d, "direction-type").append(t)
    return d


def _words(text: str, **attrs) -> ET.Element:
    w = ET.Element("words", {"font-weight": "bold", **attrs})
    w.text = text
    return w


def _tempo_direction(mv: dict, compound: bool) -> Optional[ET.Element]:
    types = [_words(mv["text"])] if mv.get("text") else []
    bpm = mv.get("bpm")
    dotted = mv.get("dotted")
    dotted = compound if dotted is None else dotted
    if bpm:
        met = ET.Element("metronome", parentheses="yes")
        ET.SubElement(met, "beat-unit").text = "quarter"
        if dotted:
            ET.SubElement(met, "beat-unit-dot")
        ET.SubElement(met, "per-minute").text = bpm
        types.append(met)
    if not types:
        return None
    d = _direction(*types)
    if bpm:
        ET.SubElement(d, "sound", tempo=f"{int(bpm.split('-')[0]) * (1.5 if dotted else 1):g}")
    return d


def _compound(measures: Sequence[ET.Element]) -> bool:
    """Is the meter in force at the end of ``measures`` a compound one (6/8, 9/8, 12/8)?"""
    for m in reversed(measures):
        t = m.find("attributes/time")
        if t is not None:
            return t.findtext("beats") in ("6", "9", "12")
    return False


def _final_barline(measure: ET.Element) -> None:
    bl = measure.find("barline[@location='right']")
    if bl is None:
        bl = ET.SubElement(measure, "barline", location="right")
    if bl.find("repeat") is not None:
        return
    style = bl.find("bar-style")
    if style is None:
        style = ET.Element("bar-style")
        bl.insert(0, style)
    style.text = "light-heavy"


def apply_movements(root: ET.Element, movements: Sequence[dict]) -> int:
    """Write the headings, tempo marks and final barlines into the first part; returns the number
    of movements written."""
    part = root.find("part")
    if part is None:
        return 0
    ms = part.findall("measure")
    others = [p.findall("measure") for p in root.findall("part")[1:]]
    done = 0
    for mv in movements:
        b = mv["bar"]
        if not 0 <= b < len(ms):
            continue
        bar = ms[b]
        printed = {mv["heading"], mv.get("text", "")} - {""}
        for same in [bar, *(m[b] for m in others if b < len(m))]:
            for d in same.findall("direction"):               # the same words, read by the OMR text layer
                if any(re.sub(r"^check:\s*", "", (w.text or "")).strip(" *\n") in printed for w in d.iter("words")):
                    same.remove(d)
        pr = bar.find("print")
        if pr is None:
            pr = ET.Element("print")
            bar.insert(0, pr)
        if b:
            pr.set("new-system", "yes")
            for measures in [ms, *others]:
                if b <= len(measures):
                    _final_barline(measures[b - 1])
        at = 0
        while at < len(bar) and bar[at].tag in ("print", "attributes"):
            at += 1
        # one text block: MuseScore always puts a tempo mark above other texts, so a separate
        # heading would end up under it
        d = _tempo_direction(mv, _compound(ms[:b + 1]))
        head = _words(mv["heading"] + ("\n" if d is not None else ""), **{"font-size": "14"})
        if d is None:
            d = _direction(head)
        else:
            first = ET.Element("direction-type")
            first.append(head)
            d.insert(0, first)
        bar.insert(at, d)
        done += 1
    return done


def apply_movements_file(path: Path, movements: Sequence[dict]) -> int:
    tree = ET.parse(path)
    n = apply_movements(tree.getroot(), movements)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return n
