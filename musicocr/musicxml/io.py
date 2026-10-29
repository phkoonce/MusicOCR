"""Writing MusicXML files with ElementTree."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET


def write_tree(tree: ET.ElementTree, path: Path) -> None:
    """Write ``tree`` to ``path`` indented, with an XML declaration (the form every fix uses)."""
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
