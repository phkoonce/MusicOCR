#!/usr/bin/env python3
"""Write batches/gb-all.toml from input/gb/pieces.json (see scripts/gb_split.py).

One part per piece and horn (id ``pNN_hM``), each reading its cropped piece PDF; Horn 4 is in the
old notation (bass clefs an octave low), see musicocr/retouch.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# TOC lines the OCR got wrong or merged (checked against the printed Inhalt)
OVERRIDES = {
    13: ("O Täler weit, o Höhen", "Felix Mendelssohn Bartholdy"),
    41: ("Wie's daheim war", "Gustav Wohlgemuth (1863-1937)"),
    69: ("Wie sie so sanft ruhn", "Friedrich Burchard Benecken (1760-1818)"),
    79: ("Wach auf, Chor aus „Die Meistersinger von Nürnberg“", "Richard Wagner"),
    81: ("Schlaf wohl, aus „Das Paradies und die Peri“", "Robert Schumann"),
    82: ("Volkslied und Märchen", "Karl Komzak (1850-1905)"),
}


def clean(title: str) -> str:
    title = re.sub(r"(\w)- (\w)", r"\1\2", title)
    return re.sub(r"[\s.]+$", "", title).replace('"', "'")


def main() -> None:
    table = json.loads((ROOT / "input/gb/pieces.json").read_text())
    out = ['title = "Quartette für Waldhorn, 1. Folge (Liebert) - Horns 1-4"',
           'key_prefix = "GB_"', "bar_numbers = false", ""]
    for e in table["entries"]:
        title, composer = OVERRIDES.get(e["num"], (clean(e["title"]), e["composer"]))
        if composer.startswith("Komponist:") or composer.isdigit():
            composer = ""
        for h in (1, 2, 3, 4):
            pdf = table["pdfs"][str(h)][str(e["num"])]
            pages = int(next(l.split()[1] for l in __import__("subprocess").run(
                ["pdfinfo", pdf], capture_output=True, text=True).stdout.splitlines() if l.startswith("Pages")))
            out += ["[[parts]]", f'id = "p{e["num"]:02d}_h{h}"',
                    f'label = "{e["num"]}. {title} - Horn {h}"', 'instrument = "horn in f"',
                    f'title = "{title}"', f'composer = "{composer.replace(chr(34), chr(39))}"',
                    f'source_pdf = "{pdf}"', "first_page = 1", f"last_page = {pages}"]
            if h == 4:
                out.append("old_bass_clef = true")
            out.append("")
    (ROOT / "batches/gb-all.toml").write_text("\n".join(out))
    print(f"{len(table['entries'])} pieces -> batches/gb-all.toml")


if __name__ == "__main__":
    main()
