"""List bars whose length is wrong and not explained by a pickup split (two short bars making one)."""
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from correct import OUT, bar_length, parse, quarters, read  # noqa: E402

for name in sys.argv[1:]:
    root = ET.parse(OUT / name / f"{name}.musicxml").getroot()
    for part in root.findall("part"):
        bars = read(part.findall("measure"))
        size = []
        for b in bars:
            notes = parse(b["text"])
            size.append(None if not notes or b["text"] == "rw" else sum(map(quarters, notes), Fraction(0)))
        paired = set()
        for i, b in enumerate(bars):
            full = bar_length(b["time"])
            for j in (i + 1, i + 2):
                if j < len(bars) and size[i] and size[j] and size[i] < full and size[i] + size[j] == full:
                    paired |= {i, j}
        for i, b in enumerate(bars):
            full = bar_length(b["time"])
            ends = i in (0, len(bars) - 1)
            if size[i] != full and i not in paired and not (ends and size[i] and size[i] < full):
                print(f"{name} {part.get('id')} bar {i + 1} {b['time']} has {float(size[i] or 0):g}/{float(full):g}: {b['text'][:150]}")
