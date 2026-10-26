"""Pieces whose part PDF is a run of short scenes ("cues"), each starting on its own page.

Film-score parts (``1M2S``, ``4M30`` ...) restart the bar numbers in every scene, so the usual
one-continuous-piece assumptions of ``combine`` do not hold. This module finds the scenes in a
scanned part, reads the "0:16 until 1M4" box printed at the end of a scene, combines the scenes
one at a time (``combine``, with that scene's pages and its own slice of the PDF) and strings them
into one score: a rehearsal mark with the scene's label and title opens each scene and the box text
closes it.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

_CYRILLIC = str.maketrans("ЗМВАСНЕОР", "3MBACHEOP")
_LABEL = re.compile(r"^\s*(\d\s*M\s*\d+[A-Z]?(?:-\d+[A-Z]?|-Credits)*)|^\s*(Entr.?acte)", re.I)
_UNTIL = re.compile(r"(\d{1,2}\s*:\s*\d\d)\s*until\s+([0-9A-Za-z][\w'\-]*)", re.I)
_DPI = 150


@dataclass
class Scene:
    label: str
    title: str
    first_page: int
    last_page: int
    until: str = ""          # text of the box at the scene's end ("0:16 until 1M4"), "" if none

    @property
    def pages(self) -> range:
        return range(self.first_page, self.last_page + 1)


def _ocr_page(pdf: Path, page: int, box: Sequence[float], tmp: Path, scale: int = 2) -> str:
    from PIL import Image
    from musicocr.ocr_backends import ocr_words
    stem = tmp / f"pg{page}"
    subprocess.run(["pdftoppm", "-r", str(_DPI), "-f", str(page), "-l", str(page), "-gray", "-png",
                    "-singlefile", str(pdf), str(stem)], capture_output=True, check=True)
    im = Image.open(str(stem) + ".png")
    w, h = im.size
    crop = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    crop.save(tmp / "crop.png")
    return " ".join(x["text"] for x in ocr_words(tmp / "crop.png", "vision"))


def page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


def read_until_box(text: str) -> str:
    """"0:16 until 1M4" from OCR text, with the label's look-alike letters repaired; "" if absent."""
    m = _UNTIL.search(text.translate(_CYRILLIC))
    return f"{re.sub(r'\s+', '', m.group(1))} until {m.group(2)}" if m else ""


def detect_scenes(pdf: Path, cues: Optional[List[List[str]]] = None, log: Callable = print) -> List[Scene]:
    """The scenes of a part PDF: a page whose title block starts with a cue label opens a scene.

    ``cues`` ([[label, title], ...] in print order) supplies the canonical label and title; each
    detected start must then agree with the next cue's label or it is reported. The page range of a
    scene runs to the page before the next scene (a part's trailing pages belong to the last scene).
    """
    starts: List[tuple] = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        n = page_count(pdf)
        for pg in range(1, n + 1):
            text = _ocr_page(pdf, pg, (.3, .045, .75, .125), tmp).translate(_CYRILLIC)
            m = _LABEL.match(text)
            if m:
                starts.append((pg, re.sub(r"\s+", "", m.group(1) or "Entr'acte"), text))
        scenes: List[Scene] = []
        for i, (pg, label, text) in enumerate(starts):
            last = starts[i + 1][0] - 1 if i + 1 < len(starts) else n
            title = re.sub(r'^\S+\s*', "", text).strip(' "“”')
            if cues and i < len(cues):
                if cues[i][0].lower().replace("'", "") != label.lower().replace("'", "").replace("’", ""):
                    log(f"  scene {i + 1}: page title reads {label!r} but the cue list has {cues[i][0]!r}")
                label, title = cues[i]
            scenes.append(Scene(label, title, pg, last))
        for s in scenes:
            for pg in range(s.last_page, s.first_page - 1, -1):      # the box is on the scene's last page
                s.until = read_until_box(_ocr_page(pdf, pg, (.4, .12, 1, 1), tmp))
                break
    if cues and len(scenes) != len(cues):
        log(f"  found {len(scenes)} scene(s) but the cue list has {len(cues)}")
    return scenes


def save_scenes(path: Path, table: Dict[str, List[Scene]]) -> None:
    path.write_text(json.dumps({k: [vars(s) for s in v] for k, v in table.items()}, indent=1))


def load_scenes(path: Path) -> Dict[str, List[Scene]]:
    return {k: [Scene(**s) for s in v] for k, v in json.loads(path.read_text()).items()}


def slice_pdf(src: Path, first: int, last: int, out: Path) -> Path:
    """Pages ``first``-``last`` (1-based) of ``src`` as ``out`` (reused when present)."""
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["pdfseparate", "-f", str(first), "-l", str(last), str(src), f"{t}/p-%d.pdf"], check=True)
        subprocess.run(["pdfunite", *[f"{t}/p-{n}.pdf" for n in range(first, last + 1)], str(out)], check=True)
    return out


# ---- stringing the scenes together -------------------------------------------------

def _words(text: str, placement: str = "above", bold: bool = False) -> ET.Element:
    d = ET.Element("direction", placement=placement)
    w = ET.SubElement(ET.SubElement(d, "direction-type"), "words")
    w.text = text
    if bold:
        w.set("font-weight", "bold")
    return d


def _rehearsal(text: str) -> ET.Element:
    d = ET.Element("direction", placement="above")
    ET.SubElement(ET.SubElement(d, "direction-type"), "rehearsal").text = text
    return d


def concat_scenes(scores: Sequence[ET.Element], scenes: Sequence[Scene], title: str) -> ET.Element:
    """One score from per-scene scores (same parts, in the same order).

    Bar numbers restart at 1 in each scene (an upbeat is bar 0); each scene opens with a rehearsal mark ("1M1  Dragon
    Racing") on a new system, and its ``until`` box text sits above the scene's last bar.
    """
    out = ET.Element("score-partwise", version="3.1")
    ET.SubElement(ET.SubElement(out, "work"), "work-title").text = title
    out.append(scores[0].find("part-list"))
    parts = {p.get("id"): ET.SubElement(out, "part", id=p.get("id")) for p in scores[0].findall("part")}
    for si, (sc, scene) in enumerate(zip(scores, scenes)):
        for pe in sc.findall("part"):
            dst = parts[pe.get("id")]
            ms = pe.findall("measure")
            for k, m in enumerate(ms):
                if si:                              # the transposition is declared once, in the first scene
                    for a in m.findall("attributes"):
                        for tr in a.findall("transpose"):
                            a.remove(tr)
                idx = 1 if m.find("attributes") is not None else 0
                if k == 0:
                    if si:
                        m.insert(0, ET.Element("print", {"new-system": "yes"}))
                        idx += 1
                    m.insert(idx, _rehearsal(f"{scene.label}  {scene.title}".strip()))
                if k == len(ms) - 1 and scene.until:
                    m.insert(idx + (1 if k == 0 else 0), _words(scene.until, "above", True))
                # bar numbers restart in every scene (an upbeat is bar 0), as printed
                n_in_scene = k + 1 - (1 if ms[0].get("implicit") == "yes" else 0)
                m.set("number", str(n_in_scene))
                dst.append(m)
    return out


# ---- pickup bars ---------------------------------------------------------------------

def pickup_of(first_bars, tol: float = 1e-6):
    """The short first bar of each part's first page, when most parts open with one.

    ``first_bars`` is [(measure, divisions)] (None for a part without music). A bar shorter than the
    meter in force (its own declaration, else 4/4) is an upbeat; the printed bar numbers start *after*
    it, so ``combine`` (which counts the first bar as bar 1) must not see it. Returns the per-part
    list of upbeat bars (None where a part's first bar is full) or [] when fewer than half the parts
    have one.
    """
    from musicocr.alignment.build import measure_total
    from musicocr.combine import _declared
    found = []
    for fb in first_bars:
        if fb is None:
            found.append(None)
            continue
        m, div = fb
        _, meter = _declared(m)
        beats, beat_type = meter or (4, 4)
        full = div * 4 * beats / beat_type
        has_notes = m.find("note") is not None and m.find("note/rest[@measure='yes']") is None
        found.append(fb if has_notes and 0 < measure_total(m) < full - tol else None)
    return found if sum(f is not None for f in found) * 2 >= len(found) else []


def prepend_pickup(score: ET.Element, pickups) -> None:
    """Put each part's upbeat bar (``pickups`` in part order, None = rest) in front of its first bar.

    The new bar takes over the first bar's attributes (key, meter, clef, transposition) and is marked
    implicit so MuseScore does not count it as a bar.
    """
    from musicocr.alignment.build import rest_measure
    from musicocr.alignment.spec import DIV
    from musicocr.combine import copy_marks
    for pe, pk in zip(score.findall("part"), pickups):
        first = pe.find("measure")
        m = ET.Element("measure", number="0", implicit="yes")
        for a in first.findall("attributes"):
            first.remove(a)
            m.append(a)
        if pk is None:
            m.append(rest_measure(0, DIV, False).find("note"))
        else:
            copy_marks(pk[0], m, pk[1])
        pe.insert(list(pe).index(first), m)


def drop_pickup(srcs):
    """A page's measures without the upbeat bar; its key / meter / clef declarations move to the next bar."""
    import copy
    if len(srcs) < 2:
        return srcs[1:]
    (pm, _), (nm, nd) = srcs[0], srcs[1]
    attrs = pm.find("attributes")
    if attrs is not None:
        have = nm.find("attributes")
        if have is None:
            have = ET.Element("attributes")
            nm.insert(0, have)
        for ch in attrs:
            if have.find(ch.tag) is None:
                have.append(copy.deepcopy(ch))
    return srcs[1:]
