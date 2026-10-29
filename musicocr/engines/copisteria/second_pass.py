"""Second pass for Copisteria pages whose first reading left gaps.

Copisteria draws only what it read, so a symbol its detector missed leaves a gap
(``<forward>`` / hidden rest) in the bar. Re-detecting the page one input size up
finds many of them (``second_pass_detect.py``); the page is then re-read with those
extra note/rest detections. Because the bigger size also changes how bars are
detected, the second reading is accepted only if it is *confined to the gap bars*:
same number of bars, and every bar that differs from the first reading had a gap
there. Otherwise the first reading is kept.
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from musicocr.musicxml import write_tree


def _measures(path: Path) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    part = root.find("part")
    return list(part.findall("measure")) if part is not None else []


def _has_gap(measure: ET.Element) -> bool:
    if measure.find("backup") is not None:
        return False
    notes = [k for k in measure if k.tag == "note"]
    if not notes:
        return False        # a wholly empty bar is fill_empty_measures' job
    return measure.find("forward") is not None or any(
        n.get("print-object") == "no" and n.find("rest") is not None for n in notes)


def _canon(measure: ET.Element) -> str:
    m = ET.fromstring(ET.tostring(measure))
    m.attrib.pop("number", None)
    return ET.tostring(m, encoding="unicode")


def gap_bars(path: Path) -> list[int]:
    """0-based indices of the bars with a gap (first part only)."""
    return [i for i, m in enumerate(_measures(path)) if _has_gap(m)]


def _attrs(measure: ET.Element) -> str:
    a = measure.find("attributes")
    return ET.tostring(a, encoding="unicode") if a is not None else ""


def _divisions(path: Path) -> list[str]:
    return [d.text or "" for d in ET.parse(path).getroot().iter("divisions")]


def merge_second_pass(first: Path, second: Path, out: Path) -> tuple[int, str]:
    """Write ``out`` = the first reading with its gap bars replaced by the second
    reading's version, for each gap bar the second reading fills completely.

    Bars are independent in MusicXML, so changes the second reading makes
    elsewhere (the larger scale also shifts how some bars are read) are ignored.
    Guards: one part each, same bar count, same ``<divisions>`` (durations are in
    those units), and the replaced bar keeps the first reading's attributes.
    Returns ``(bars replaced, reason)``; nothing is written when it is 0.
    """
    t1, t2 = ET.parse(first), ET.parse(second)
    p1, p2 = t1.getroot().findall("part"), t2.getroot().findall("part")
    if len(p1) != 1 or len(p2) != 1:
        return 0, "not a single-part score"
    a, b = p1[0].findall("measure"), p2[0].findall("measure")
    if len(a) != len(b):
        return 0, f"bar count changed ({len(a)} -> {len(b)})"
    if _divisions(first)[:1] != _divisions(second)[:1]:
        return 0, "divisions differ between the two readings"
    gaps = [i for i, m in enumerate(a) if _has_gap(m)]
    done = 0
    for i in gaps:
        if _has_gap(b[i]) or _attrs(a[i]) != _attrs(b[i]):
            continue
        if _canon(a[i]) == _canon(b[i]):
            continue
        new = ET.fromstring(ET.tostring(b[i]))
        new.set("number", a[i].get("number", ""))
        parent = p1[0]
        parent[list(parent).index(a[i])] = new
        done += 1
    if not done:
        return 0, f"no gap bar was filled ({len(gaps)} gap bar(s))"
    write_tree(t1, out)
    return done, f"{done} of {len(gaps)} gap bar(s) filled"
