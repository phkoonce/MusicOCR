"""Print what is needed to write structure.json: headings, meters, keys, barlines and text by bar."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name in sys.argv[1:]:
    r = ET.parse(ROOT / f"output/bach-suites-rescan/{name}/{name}.musicxml").getroot()
    bars = r.find("part").findall("measure")
    print(name, "parts", len(r.findall("part")), "bars", len(bars), "staves", r.findtext(".//staves"))
    for i, m in enumerate(bars):
        words = [w.text for w in m.iter("words")]
        key = m.findtext("attributes/key/fifths")
        t = m.find("attributes/time")
        time = "" if t is None else f"{t.findtext('beats')}/{t.findtext('beat-type')}"
        lines = [(b.findtext("bar-style"), None if b.find("repeat") is None else b.find("repeat").get("direction"))
                 for b in m.findall("barline")]
        head = any(w and (w.startswith("check") or w[:4].isupper() or w[:1].isupper()) for w in words)
        if head or key is not None or time or any(s == "light-heavy" for s, _ in lines):
            print(i + 1, words or "", f"key {key}" if key is not None else "", time, lines or "")
