"""Post-OMR touch-ups that depend on knowing the edition rather than on the scan.

* **Old-notation bass clef** (``old_bass_clef``): older horn editions write the bass-clef notes of the
  low horns an octave below where modern editions put them, so a horn-in-F part read at face value
  sounds a fourth up from the printed pitch instead of a fifth down. Every bass clef becomes an
  octave-up bass clef (``clef-octave-change`` 1) and every note under it goes up an octave, which
  keeps each note on the same staff position as in the scan.
* **Title and composer** (``title``, ``composer``): written into the score's metadata, and the
  directions OMR read the printed title / composer line as ("check: Heinz Liebert (geb.1918)") are
  dropped.
"""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Dict, Optional

_HEADER = re.compile(r"^\s*(?:check:\s*)?horn\s+[ivx1-4]+(?:\s+in(?:\s+f)?)?\s*$", re.I)   # running head "Horn IV in F"
_MATCH = 0.75   # similarity at which a direction's text counts as the printed title / composer


@dataclass(frozen=True)
class RetouchOptions:
    old_bass_clef: bool = False
    title: str = ""
    composer: str = ""

    @property
    def active(self) -> bool:
        return self.old_bass_clef or bool(self.title or self.composer)


def _is_plain_bass(clef: ET.Element) -> bool:
    return (clef.findtext("sign") == "F" and clef.findtext("line") in (None, "4")
            and int(clef.findtext("clef-octave-change") or 0) == 0)


def octavize_bass_clefs(root: ET.Element) -> int:
    """Old-notation bass clefs -> octave-up bass clefs, notes under them raised an octave.

    Returns the number of notes moved. Re-running changes nothing (the clefs are no longer plain).
    """
    moved = 0
    for part in root.iter("part"):
        old: Dict[str, bool] = {}      # staff number -> a plain bass clef is in force
        for measure in part.findall("measure"):
            for el in measure:
                if el.tag == "attributes":
                    for clef in el.findall("clef"):
                        staff = clef.get("number", "1")
                        old[staff] = _is_plain_bass(clef)
                        if old[staff]:
                            line = clef.find("line")
                            oc = ET.Element("clef-octave-change")
                            oc.text = "1"
                            clef.insert(list(clef).index(line) + 1 if line is not None else 1, oc)
                            old[staff] = True
                        elif clef.findtext("sign") == "F" and int(clef.findtext("clef-octave-change") or 0) == 1:
                            old[staff] = False    # already octavized: its notes are already moved
                elif el.tag == "note":
                    pitch = el.find("pitch")
                    if pitch is not None and old.get(el.findtext("staff") or "1"):
                        oct_el = pitch.find("octave")
                        oct_el.text = str(int(oct_el.text) + 1)
                        moved += 1
    return moved


def _norm(text: str) -> str:
    text = re.sub(r"^\s*check:\s*", "", text, flags=re.I)
    text = re.sub(r"\([^)]*\)?", " ", text)           # dates: "(geb.1918)", "(1867-1928)"
    return re.sub(r"[^a-zäöüß ]", "", text.lower().replace("ü", "ü")).strip()


def _same(text: str, target: str) -> bool:
    a, b = _norm(text), _norm(target)
    return len(a) >= 4 and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= _MATCH


def set_metadata(root: ET.Element, title: str, composer: str, first_bars: int = 16) -> int:
    """Title/composer into the score; returns the number of misread title/composer directions dropped."""
    if title:
        work = root.find("work")
        if work is None:
            work = ET.Element("work")
            root.insert(0, work)
        wt = work.find("work-title")
        if wt is None:
            wt = ET.SubElement(work, "work-title")
        wt.text = title
        mt = root.find("movement-title")
        if mt is not None:
            mt.text = title                # music21 fills this with the file name
    if composer:
        ident = root.find("identification")
        if ident is None:
            ident = ET.Element("identification")
            root.insert(list(root).index(root.find("part-list")), ident)
        for c in ident.findall("creator[@type='composer']"):
            ident.remove(c)
        c = ET.Element("creator", type="composer")
        c.text = composer
        ident.insert(0, c)
    dropped = 0
    for part in root.iter("part"):
        for measure in part.findall("measure")[:first_bars]:
            for d in measure.findall("direction"):
                words = [w.text or "" for w in d.iter("words")]
                if len(words) == 1 and (_HEADER.match(words[0]) or any(t and _same(words[0], t) for t in (title, composer))):
                    measure.remove(d)
                    dropped += 1
    return dropped


def retouch_tree(root: ET.Element, opts: RetouchOptions, log: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    log = log or (lambda s: None)
    out = {"octavized": 0, "dropped": 0}
    if opts.old_bass_clef:
        out["octavized"] = octavize_bass_clefs(root)
        log(f"old-notation bass clefs: {out['octavized']} note(s) raised an octave")
    if opts.title or opts.composer:
        out["dropped"] = set_metadata(root, opts.title, opts.composer)
        log(f"title/composer set; {out['dropped']} misread direction(s) dropped")
    return out


def retouch_file(src, dst, opts: RetouchOptions, log=None) -> Dict[str, int]:
    tree = ET.parse(src)
    res = retouch_tree(tree.getroot(), opts, log)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return res
