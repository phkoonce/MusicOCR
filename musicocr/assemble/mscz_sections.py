"""Section breaks in a MuseScore file, so each piece of a multi-piece score restarts at bar 1.

A score that strings together many short pieces (film-score cues, a book of quartets) is written to
MusicXML with each piece ending in a barline and the next one opening a new system
(``<print new-system="yes">``). MuseScore's importer turns that into a plain line break, which keeps
the bar numbers running. A *section break* on the last bar of a piece does what is wanted: the next
section starts a new system and, by default, restarts the bar numbers at 1 (and shows the long
instrument names again). MuseScore's MusicXML import never creates one, so this module patches the
converted ``.mscz``: every bar that precedes a ``new-system`` bar of the MusicXML gets a section break
(replacing the line break the import made there).
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Sequence
from xml.etree import ElementTree as ET

_BREAK = ("<LayoutBreak>\n          <subtype>section</subtype>\n"
          "          <startWithMeasureOne>1</startWithMeasureOne>\n          </LayoutBreak>")
_LINE_BREAK = re.compile(r"<LayoutBreak>\s*(?:<eid>[^<]*</eid>\s*)?<subtype>(?:line|page|section)</subtype>.*?</LayoutBreak>", re.S)


def section_ends(musicxml: Path) -> List[int]:
    """0-based indices of the bars that end a piece: the bar before each ``new-system`` bar."""
    part = ET.parse(musicxml).getroot().find("part")
    ms = part.findall("measure") if part is not None else []
    return [i - 1 for i, m in enumerate(ms) if i and m.find("print[@new-system='yes']") is not None]


def patch_mscx(text: str, ends: Sequence[int]) -> str:
    """``text`` (a .mscx) with a section break on the first staff's bar ``i`` for each ``i`` in ``ends``."""
    # MuseScore keeps the MusicXML bar numbers as <noOffset> shifts on bars where they differ from its
    # own running count; with the numbering restarted by section breaks those shifts would be wrong
    text = re.sub(r"\n[ \t]*<noOffset>[^<]*</noOffset>", "", text)
    start = text.index('<Staff id="1">')
    nxt = text.find('<Staff id="2">', start)
    stop = nxt if nxt != -1 else len(text)
    block = text[start:stop]
    spans = [m.span() for m in re.finditer(r"<Measure(?:\s[^>]*)?>.*?</Measure>", block, re.S)]
    out, last = [], 0
    for i in sorted(set(ends)):
        if not 0 <= i < len(spans):
            continue
        a, b = spans[i]
        bar = block[a:b]
        bar = _LINE_BREAK.sub("", bar)
        head = re.match(r"<Measure(?:\s[^>]*)?>(\s*<eid>[^<]*</eid>)?", bar)
        bar = bar[:head.end()] + "\n        " + _BREAK + bar[head.end():]
        out.append(block[last:a] + bar)
        last = b
    out.append(block[last:])
    return text[:start] + "".join(out) + text[stop:]


def add_section_breaks(mscz: Path, ends: Sequence[int]) -> int:
    """Patch ``mscz`` in place; returns the number of section breaks written."""
    if not ends:
        return 0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t) / "x.mscz"
        with zipfile.ZipFile(mscz) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".mscx"):
                    data = patch_mscx(data.decode("utf-8"), ends).encode("utf-8")
                zout.writestr(item, data)
        shutil.copyfile(tmp, mscz)
    return len(set(ends))
