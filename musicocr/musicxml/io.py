"""Writing MusicXML files with ElementTree."""
from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET


def write_tree(tree: ET.ElementTree, path: Path) -> None:
    """Write ``tree`` to ``path`` indented, with an XML declaration (the form every fix uses)."""
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


_WEDGE_SPREAD = re.compile(r'(<wedge\b[^>]*?)\s+spread="[^"]*"')


def open_wedges(path: Path) -> None:
    """Drop the ``spread`` music21 writes on hairpins: it puts 0 on the start of a crescendo, which MuseScore
    takes as the hairpin's opening, so every crescendo is drawn as one flat line."""
    text = path.read_text(encoding="utf-8")
    fixed = _WEDGE_SPREAD.sub(r"\1", text)
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")
