"""Tests for musicocr.scoreops.transpose and musicocr.scoreops.instruments."""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from musicocr.scoreops.instruments import semitones_for
from musicocr.scoreops.transpose import fifths_shift, transpose_score


def _score(name, notes, fifths=0, clef=("G", "2"), key=True, extra_measures=()):
    root = ET.Element("score-partwise")
    pl = ET.SubElement(root, "part-list")
    sp = ET.SubElement(pl, "score-part", id="P1")
    ET.SubElement(sp, "part-name").text = name
    part = ET.SubElement(root, "part", id="P1")
    m = ET.SubElement(part, "measure", number="1")
    at = ET.SubElement(m, "attributes")
    ET.SubElement(at, "divisions").text = "1"
    if key:
        ET.SubElement(ET.SubElement(at, "key"), "fifths").text = str(fifths)
    c = ET.SubElement(at, "clef")
    ET.SubElement(c, "sign").text = clef[0]
    ET.SubElement(c, "line").text = clef[1]
    tr = ET.SubElement(at, "transpose")
    ET.SubElement(tr, "chromatic").text = "-7"
    for step, alter, octave in notes:
        n = ET.SubElement(m, "note")
        p = ET.SubElement(n, "pitch")
        ET.SubElement(p, "step").text = step
        if alter:
            ET.SubElement(p, "alter").text = str(alter)
            ET.SubElement(n, "accidental").text = "sharp"
        ET.SubElement(p, "octave").text = str(octave)
        ET.SubElement(n, "duration").text = "1"
    for fifths_new in extra_measures:
        m2 = ET.SubElement(part, "measure", number="2")
        ET.SubElement(ET.SubElement(ET.SubElement(m2, "attributes"), "key"), "fifths").text = str(fifths_new)
    return root


def _read(root):
    return [(p.findtext("step"), int(p.findtext("alter") or 0), int(p.findtext("octave")))
            for p in root.iter("pitch")]


def _keys(root):
    return [int(f.text) for f in root.iter("fifths")]


def test_horn_c_major_to_f_major():
    root = _score("Horn in F 2", [("C", 0, 5), ("E", 0, 5), ("G", 0, 4), ("B", 0, 4)])
    res = transpose_score(root)
    assert _keys(root) == [-1]
    assert _read(root) == [("F", 0, 4), ("A", 0, 4), ("C", 0, 4), ("E", 0, 4)]
    assert res.parts[0].semitones == -7
    assert root.find(".//transpose") is None
    assert root.find(".//accidental") is None


def test_trumpet_respells_and_changes_key():
    # written D major (2 sharps) F# -> concert C major, E
    root = _score("Trumpet 1", [("F", 1, 4), ("D", 0, 5)], fifths=2)
    transpose_score(root)
    assert _keys(root) == [0]
    assert _read(root) == [("E", 0, 4), ("C", 0, 5)]


def test_flat_spelling_and_octave_wrap():
    # written C4 down 2 -> Bb3 (octave number drops)
    root = _score("Bb Clarinet", [("C", 0, 4)])
    transpose_score(root)
    assert _read(root) == [("B", -1, 3)]
    assert _keys(root) == [-2]


def test_baritone_clef_dependent():
    treble = _score("Baritone Horn", [("C", 0, 5)], clef=("G", "2"))
    bass = _score("Baritone Horn", [("C", 0, 3)], clef=("F", "4"))
    transpose_score(treble)
    transpose_score(bass)
    assert _read(treble) == [("B", -1, 3)] and _keys(treble) == [-2]   # -14: down M9
    assert _read(bass) == [("C", 0, 3)] and _keys(bass) == [0]         # unchanged


def test_tuba_unchanged():
    root = _score("Tuba", [("F", 0, 2)], fifths=-3, clef=("F", "4"))
    transpose_score(root)
    assert _read(root) == [("F", 0, 2)] and _keys(root) == [-3]


def test_explicit_semitones_and_missing_key_is_made_explicit():
    root = _score("Anything", [("C", 0, 4)], key=False)
    transpose_score(root, semitones=-7)
    assert _keys(root) == [-1]
    assert root.find("part/measure/attributes").findall("*")[1].tag == "key"   # after divisions


def test_later_key_changes_follow_and_prefer_small_signatures():
    # horn: written 4 sharps -> 3 sharps; shift is -1 fifth not +11
    root = _score("Horn in F", [("C", 1, 4)], fifths=0, extra_measures=[4])
    transpose_score(root)
    assert _keys(root) == [-1, 3]
    assert _read(root) == [("F", 1, 3)]   # written C# -> F# a fifth below
    assert fifths_shift(-7, [0, 4]) == -1


def test_enharmonic_alternative_when_key_would_be_huge():
    # written 6 flats, horn (-1 fifth) would be 7 flats; +11 gives 5 sharps
    assert fifths_shift(-7, [-6]) == 11


def test_unknown_part_name_is_skipped_not_guessed():
    root = _score("Kazoo", [("C", 0, 4)])
    res = transpose_score(root)
    assert res.parts == [] and len(res.skipped) == 1
    assert _read(root) == [("C", 0, 4)]


def test_instrument_lookup():
    assert semitones_for("French Horn") == -7
    assert semitones_for("Horn in F 3") == -7
    assert semitones_for("Trumpet in B♭") == -2
    assert semitones_for("Baritone Horn", "treble") == -14
    assert semitones_for("Tenor Horn", "bass") == 0
    with pytest.raises(ValueError):
        semitones_for("Baritone Horn", "c")
    with pytest.raises(ValueError):
        semitones_for("Trumpet in C")


def test_tuba_treble_clef_depends_on_pitch():
    assert semitones_for("Eb Tuba", "treble") == -21
    assert semitones_for("Tuba in Bb 2", "treble") == -26
    assert semitones_for("BBb Tuba", "bass") == 0
    with pytest.raises(ValueError):
        semitones_for("Tuba", "treble")   # ambiguous: Eb or Bb?
    root = _score("Eb Tuba", [("C", 0, 5)])
    transpose_score(root)
    assert _read(root) == [("E", -1, 3)] and _keys(root) == [-3]   # C5 -> Eb3 (down 21)


def test_written_mode_keeps_pitches_and_declares_instrument():
    root = _score("Voice", [("C", 0, 5)])
    transpose_score(root, instrument="horn in f", mode="written")
    assert _read(root) == [("C", 0, 5)] and _keys(root) == [0]
    tr = root.find(".//attributes/transpose")
    assert (tr.findtext("diatonic"), tr.findtext("chromatic")) == ("-4", "-7")
    assert tr.find("octave-change") is None
    assert root.findtext(".//score-part/part-name") == "Horn in F"
    assert root.findtext(".//score-instrument/instrument-sound") == "brass.french-horn"
    assert len(root.findall(".//transpose")) == 1


def test_written_mode_octave_and_second():
    root = _score("Euphonium", [("C", 0, 5)], clef=("G", "2"))
    transpose_score(root, mode="written")
    tr = root.find(".//attributes/transpose")
    assert (tr.findtext("diatonic"), tr.findtext("chromatic"), tr.findtext("octave-change")) \
        == ("-1", "-2", "-1")


def test_stage_is_idempotent_and_restores_from_written_copy(tmp_path):
    from musicocr.config import TransposeConfig, load_config
    from musicocr.pipeline import PipelineContext
    from musicocr.stages import transpose as stage

    xml = tmp_path / "book.musicxml"
    ET.ElementTree(_score("Voice", [("C", 0, 5)])).write(xml)
    cfg = load_config()
    cfg.stages.transpose = TransposeConfig(enabled=True, instrument="horn in f", mode="concert")
    ctx = PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path, config=cfg,
                          book_name="book", log=lambda s: None)
    ctx.artifacts.musicxml = xml

    assert stage.run(ctx).status == "ok"
    first = xml.read_text()
    assert stage.run(ctx).status == "ok"          # second run must not shift again
    assert xml.read_text() == first
    assert _read(ET.parse(xml).getroot()) == [("F", 0, 4)]
    assert _read(ET.parse(tmp_path / "written" / "book.musicxml").getroot()) == [("C", 0, 5)]

    # a fresh upstream rewrite becomes the new written copy
    ET.ElementTree(_score("Voice", [("D", 0, 5)])).write(xml)
    stage.run(ctx)
    assert _read(ET.parse(xml).getroot()) == [("G", 0, 4)]


def test_stage_disabled_and_unresolvable(tmp_path):
    from musicocr.config import TransposeConfig, load_config
    from musicocr.pipeline import PipelineContext, StageError
    from musicocr.stages import transpose as stage

    xml = tmp_path / "book.musicxml"
    ET.ElementTree(_score("Voice", [("C", 0, 5)])).write(xml)
    cfg = load_config()
    ctx = PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path, config=cfg,
                          book_name="book", log=lambda s: None)
    ctx.artifacts.musicxml = xml
    assert stage.run(ctx).status == "skipped"
    cfg.stages.transpose = TransposeConfig(enabled=True)              # no instrument, generic part name
    with pytest.raises(StageError):
        stage.run(ctx)
