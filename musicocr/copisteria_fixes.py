"""Post-processing fixes for Copisteria's MusicXML.

Copisteria writes a bar it found no staff content for (e.g. a multi-measure
rest whose count it could not read, at a page's end) as a bare ``<forward>``
with no note or rest. MuseScore then shows an empty, un-rested bar and
music21 sees no duration for it. ``fill_empty_measures`` turns each such bar
into a whole-measure rest of the forwarded length -- the same remedy
``homr_fixes.fix_multirest_file`` applies to homr's empty multi-rest bars.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET


def fill_empty_measures(path: Path, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place; return the number of bars given a rest."""
    tree = ET.parse(path)
    fixed = 0
    for part in tree.getroot().findall("part"):
        for measure in part.findall("measure"):
            if measure.find("note") is not None:
                continue
            fwd = measure.find("forward")
            dur = fwd.find("duration") if fwd is not None else None
            if dur is None or not dur.text:
                continue
            note = ET.Element("note")
            ET.SubElement(note, "rest", {"measure": "yes"})
            ET.SubElement(note, "duration").text = dur.text
            ET.SubElement(note, "voice").text = "1"
            measure.remove(fwd)
            measure.append(note)
            fixed += 1
            log(f"  measure {measure.get('number')}: empty bar given a whole-measure rest "
                f"(duration={dur.text})")
    if fixed:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return fixed
