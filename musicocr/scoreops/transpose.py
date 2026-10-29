"""Transpose MusicXML by a number of semitones, keys included.

Works directly on the XML (like ``homr_fixes``) so nothing else in the file is
disturbed. Transposition is done on the line of fifths: shifting by ``n``
semitones moves every pitch -- and every key signature -- by the same number of
fifths ``f`` (f = 7n mod 12), so a transposed part is spelled and signed as a
musician would write it (C major horn part -> F major, one flat). Among the
enharmonically equivalent choices of ``f`` (f, f-12, f+12) the one keeping the
part's key signatures smallest is used.

Sign convention matches ``musicocr.scoreops.instruments``: positive is up, and OMR'd
parts are *written* pitch, so ``-7`` turns a horn part into concert pitch.

Stale notation is cleaned up: explicit ``<accidental>`` marks are dropped
(MuseScore re-derives them from pitch + key), as are ``<cancel>`` elements and
any ``<transpose>`` element, since the pitches no longer need transposing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional
from xml.etree import ElementTree as ET

from musicocr.scoreops.instruments import INFO, canonical_name, semitones_for
from musicocr.musicxml import write_tree

# Line-of-fifths position of each natural note (F=-1 ... B=5) and its semitone.
_STEPS = "FCGDAEB"
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_CLEF_FAMILY = {"G": "treble", "F": "bass", "C": "c"}


@dataclass
class PartResult:
    part_id: str
    name: str
    semitones: int
    fifths_shift: int
    notes: int


@dataclass
class TransposeResult:
    parts: List[PartResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # human-readable reasons
    warnings: List[str] = field(default_factory=list)


def fifths_shift(semitones: int, fifths_in_use: List[int]) -> int:
    """Fifths to add to pitches/keys when transposing by ``semitones``.

    Picks the enharmonic equivalent minimising the largest key signature
    reached (ties -> the smaller shift).
    """
    base = (7 * semitones) % 12
    candidates = (base - 12, base, base + 12)
    keys = fifths_in_use or [0]
    return min(candidates, key=lambda c: (max(abs(k + c) for k in keys), abs(c)))


def clef_family(part: ET.Element) -> Optional[str]:
    """'treble' / 'bass' / 'c' from the part's first clef, if it has one."""
    sign = part.find(".//attributes/clef/sign")
    return _CLEF_FAMILY.get(sign.text.strip()) if sign is not None and sign.text else None


def _transpose_pitch(pitch: ET.Element, semitones: int, shift: int) -> None:
    step = pitch.findtext("step")
    octave = int(pitch.findtext("octave"))
    alter_el = pitch.find("alter")
    alter = int(round(float(alter_el.text))) if alter_el is not None and alter_el.text else 0

    midi = 12 * (octave + 1) + _SEMITONE[step] + alter + semitones
    pos = _STEPS.index(step) - 1 + 7 * alter + shift + 1   # = new index + 7 * new alter
    new_step, new_alter = _STEPS[pos % 7], pos // 7
    new_octave, rem = divmod(midi - _SEMITONE[new_step] - new_alter, 12)
    assert rem == 0, (step, alter, semitones, shift)

    pitch.find("step").text = new_step
    pitch.find("octave").text = str(new_octave - 1)
    if new_alter:
        if alter_el is None:
            alter_el = ET.Element("alter")
            pitch.insert(1, alter_el)   # after <step>
        alter_el.text = str(new_alter)
    elif alter_el is not None:
        pitch.remove(alter_el)


def transpose_part(part: ET.Element, semitones: int) -> PartResult:
    """Transpose one ``<part>`` in place."""
    keys = [int(f.text) for f in part.iter("fifths") if f.text]
    first_attrs = part.find("measure/attributes")
    has_initial_key = first_attrs is not None and first_attrs.find("key") is not None
    shift = fifths_shift(semitones, keys + ([] if has_initial_key else [0]))

    notes = 0
    for pitch in part.iter("pitch"):
        _transpose_pitch(pitch, semitones, shift)
        notes += 1
    for note in part.iter("note"):
        for acc in note.findall("accidental"):
            note.remove(acc)
    for key in part.iter("key"):
        for cancel in key.findall("cancel"):
            key.remove(cancel)
        fifths = key.find("fifths")
        if fifths is not None and fifths.text:
            fifths.text = str(int(fifths.text) + shift)
    for attrs in part.iter("attributes"):
        for tr in attrs.findall("transpose"):
            attrs.remove(tr)

    if not has_initial_key and shift and first_attrs is not None:
        # An omitted key means C major; make the new key explicit.
        key = ET.Element("key")
        ET.SubElement(key, "fifths").text = str(shift)
        divisions = first_attrs.find("divisions")
        first_attrs.insert(0 if divisions is None else list(first_attrs).index(divisions) + 1, key)

    return PartResult(part.get("id", ""), "", semitones, shift, notes)


# Diatonic steps spanned by an interval of 0..11 semitones (tritone -> fifth).
_DIATONIC = [0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6]


def _declare_transpose(part: ET.Element, semitones: int) -> None:
    """Replace the part's <transpose> with one for ``semitones`` (written -> concert)."""
    for attrs in part.iter("attributes"):
        for tr in attrs.findall("transpose"):
            attrs.remove(tr)
    first = part.find("measure/attributes")
    if first is None or not semitones:
        return
    sign = -1 if semitones < 0 else 1
    octaves, rem = divmod(abs(semitones), 12)
    tr = ET.Element("transpose")
    ET.SubElement(tr, "diatonic").text = str(sign * _DIATONIC[rem])
    ET.SubElement(tr, "chromatic").text = str(sign * rem)
    if octaves:
        ET.SubElement(tr, "octave-change").text = str(sign * octaves)
    # schema order: ... key, time, staves, part-symbol, instruments, clef, staff-details, transpose
    after = {"divisions", "key", "time", "staves", "part-symbol", "instruments", "clef", "staff-details"}
    pos = max([i + 1 for i, c in enumerate(first) if c.tag in after], default=0)
    first.insert(pos, tr)


def _name_instrument(score_part: ET.Element, canon: str) -> None:
    """Give the <score-part> the instrument's name, sound and MIDI program."""
    name, abbr, sound, program = INFO[canon]
    for tag, text in (("part-name", name), ("part-abbreviation", abbr)):
        el = score_part.find(tag)
        if el is None:
            el = ET.Element(tag)
            score_part.insert(0, el)
        el.text = text
    inst = score_part.find("score-instrument")
    if inst is None:
        inst = ET.Element("score-instrument", id=f"{score_part.get('id', 'P1')}-I1")
        score_part.insert(list(score_part).index(score_part.find("part-abbreviation")) + 1, inst)
    for tag, text in (("instrument-name", name), ("instrument-abbreviation", abbr),
                      ("instrument-sound", sound)):
        el = inst.find(tag)
        if el is None:
            el = ET.SubElement(inst, tag)
        el.text = text
    # schema order inside score-instrument: name, abbreviation, sound
    order = {"instrument-name": 0, "instrument-abbreviation": 1, "instrument-sound": 2}
    inst[:] = sorted(inst, key=lambda c: order.get(c.tag, 3))
    midi = score_part.find("midi-instrument")
    if midi is not None:
        prog = midi.find("midi-program")
        if prog is None:
            prog = ET.SubElement(midi, "midi-program")
        prog.text = str(program)


def _part_names(root: ET.Element) -> dict:
    return {sp.get("id"): (sp.findtext("part-name") or sp.findtext("part-abbreviation") or "")
            for sp in root.iter("score-part")}


def transpose_score(
    root: ET.Element,
    semitones: Optional[int] = None,
    instrument: Optional[str] = None,
    clef: Optional[str] = None,
    mode: str = "concert",
) -> TransposeResult:
    """Transpose every part of a parsed ``score-partwise`` in place.

    ``mode="concert"`` rewrites pitches and keys at concert pitch. ``mode="written"``
    leaves them as written and instead declares the instrument and its
    ``<transpose>`` so notation software shows the score as printed and does the
    concert-pitch conversion itself.

    Give ``semitones`` (applied to all parts), or ``instrument`` (looked up in
    ``musicocr.scoreops.instruments``, using each part's own clef unless ``clef`` is
    given), or neither to identify each part by its ``<part-name>``. Parts that
    can't be resolved in that last mode are left untouched and listed in
    ``skipped``.
    """
    result = TransposeResult()
    names = _part_names(root)
    for part in root.findall("part"):
        pid = part.get("id", "")
        name = names.get(pid, "")
        part_clef = clef or clef_family(part)
        if semitones is not None:
            n = semitones
        else:
            label = instrument or name
            try:
                n = semitones_for(label, part_clef)
            except ValueError as exc:
                if instrument:
                    raise
                result.skipped.append(f"{pid} ({name or 'unnamed'}): {exc}")
                continue
        if mode == "written":
            res = PartResult(pid, name, n, 0, sum(1 for _ in part.iter("pitch")))
            _declare_transpose(part, n)
            canon = canonical_name(instrument or name)
            score_part = next((sp for sp in root.iter("score-part") if sp.get("id") == pid), None)
            if canon in INFO and score_part is not None:
                _name_instrument(score_part, canon)
        else:
            res = transpose_part(part, n)
        res.name = name
        result.parts.append(res)
        if semitones is None and not clef:
            families = {_CLEF_FAMILY.get(s.text.strip()) for s in part.iter("sign") if s.text}
            if len(families - {None}) > 1:
                result.warnings.append(
                    f"{pid} ({name}): clef changes mid-part; transposition was chosen from "
                    f"the first clef ({part_clef})"
                )
    return result


def transpose_file(
    src: Path,
    dst: Path,
    semitones: Optional[int] = None,
    instrument: Optional[str] = None,
    clef: Optional[str] = None,
    mode: str = "concert",
    log: Callable[[str], None] = print,
) -> TransposeResult:
    """Read ``src`` MusicXML, transpose, write to ``dst`` (may equal ``src``)."""
    tree = ET.parse(src)
    root = tree.getroot()
    if root.tag != "score-partwise":
        raise ValueError(f"{src}: only score-partwise MusicXML is supported (got {root.tag})")
    result = transpose_score(root, semitones, instrument, clef, mode)
    for p in result.parts:
        what = "declared transposition" if mode == "written" else "transposed"
        log(f"{p.part_id} {p.name}: {what} {p.semitones:+d} semitones "
            f"({p.fifths_shift:+d} fifths), {p.notes} notes")
    for s in result.skipped:
        log(f"skipped {s}")
    for w in result.warnings:
        log(f"warning: {w}")
    write_tree(tree, dst)
    return result
