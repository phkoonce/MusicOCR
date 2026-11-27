"""Tidy the merged Goldfaden score after `--from extract`, before `--from convert`:
drop the false movement heading "V" (bar 665) and print rehearsal numbers without '#'.

  .venv/bin/python workflows/goldfaden/format.py
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

BOOK = Path(__file__).resolve().parents[2] / "output/goldfaden-score/Goldfaden_score"
XML = BOOK / "Goldfaden_score.musicxml"
STATE = BOOK / "ttl=30d/.musicocr-state.json"
FALSE_MOVEMENT_BAR = 665
# bar -> label, where the mark was unread but its neighbours settle it; None removes a false mark
LABELS = {248: "20", 586: "1", 376: "?", 490: None}


def main():
    text = XML.read_text()
    header = text[:text.index("<score-partwise")]
    root = ET.fromstring(text[len(header):])
    for part in root.findall("part"):
        for bar, measure in enumerate(part.findall("measure"), 1):
            for direction in measure.findall("direction"):
                words = [(w.text or "").strip() for w in direction.iter("words")]
                if bar == FALSE_MOVEMENT_BAR and "V" in words:
                    measure.remove(direction)
                    continue
                for mark in list(direction.iter("rehearsal")):
                    label = LABELS.get(bar, re.sub(r"^#", "", mark.text or "") or None)
                    if label is None:
                        measure.remove(direction)
                    else:
                        mark.text = label
    XML.write_text(header + ET.tostring(root, encoding="unicode"))

    state = json.loads(STATE.read_text())

    def drop(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "movements" and isinstance(value, list):
                    node[key] = [m for m in value if m.get("bar") != FALSE_MOVEMENT_BAR - 1]
                else:
                    drop(value)

    drop(state)
    STATE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    main()
