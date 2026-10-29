"""Classifying printed words around a staff (musicocr/text/classify.py, text_lexicon.toml)."""
from __future__ import annotations

import pytest

from musicocr.text.classify import classify, load_lexicon


@pytest.mark.parametrize("text,kind,shown", [
    ("marc.", "technique", "marc."),
    ("rnarc.", "technique", "marc."),               # OCR: "rn" for "m"
    ("canta bile", "expression", "cantabile"),       # split word
    ("cantabile", "expression", "cantabile"),
    ("molto cantabile", "expression", "molto cantabile"),
    ("Solo", "expression", "Solo"),                  # printed capitalisation kept
    ("maestoso", "expression", "maestoso"),
    ("poco accel.", "tempo_change", "poco accel."),
    ("rall. poco a poco", "tempo_change", "rall. poco a poco"),
    ("a tempo", "tempo_change", "a tempo"),
    ("sim.", "technique", "sim."),
    ("to St. Mute", "technique", "to St. Mute"),
    ("SI. Faster", "tempo", "Sl. Faster"),           # OCR: capital I for l
    ("Much Slower", "tempo", "Much Slower"),
    ("Segue to 3M21", "navigation", "Segue to 3M21"),
    ("(+Tpt2-3)", "cue", "(+Tpt2-3)"),
    ("VIn. 1", "cue", "Vln. 1"),
    ("Choir, Cel", "cue", "Choir, Cel"),
])
def test_known_words(text, kind, shown):
    m = classify(text)
    assert (m.kind, m.text) == (kind, shown)


@pytest.mark.parametrize("text,bpm,words,dotted_text", [
    ("(•=124)", 124, "", True),
    ("(d=126)", 126, "", True),
    ("J=115", 115, "", False),
    ("Sl. Faster . = 103", 103, "Sl. Faster", False),
    ("Much Slower . = 59", 59, "Much Slower", False),
    ("Much Faster J= = 119", 119, "Much Faster", False),      # the "=" read twice
    ("64 (• = 124)", 124, "", True),                       # a bar number printed just before it
    ("→=99", 99, "", False),
])
def test_metronome_marks(text, bpm, words, dotted_text):
    m = classify(text)
    assert m.kind == "tempo" and m.bpm == bpm and m.text == words
    assert m.parens == dotted_text


@pytest.mark.parametrize("text", [
    "in", "Bb", "1M1", "-3-", "47-55", "130-133ł", "0:04 until",       # page furniture / bar numbers
    "mf", "ffmf", "sffz", "f-", "(f)", "molto f", "poco accel. mp"[-2:],             # dynamics (detector draws them)
    "3", "A", "2",                                                                     # digits / a lone letter (not tall)
    "TO T TAO", "te e", "Олт",                                         # OCR noise
])
def test_never_a_marking(text):
    assert classify(text).kind == "ignore"


def test_dynamics_beside_words_are_stripped():
    assert classify("poco accel. mp").text == "poco accel."
    assert classify("molto f").kind == "ignore"
    assert classify("to St. Mute 7").text == "to St. Mute"


def test_unknown_needs_real_letters():
    assert classify("Flugelhorn").kind == "unknown"
    assert classify("sffinf").kind == "unknown"          # kept as unknown; the caller drops it below a staff
    assert classify("ab").kind == "ignore"


def test_rehearsal_letter_needs_height():
    assert classify("B", height_sp=2.5).kind == "rehearsal"
    assert classify("B", height_sp=1.0).kind == "ignore"


def test_bpm_range_is_sane():
    assert classify("= 5").kind != "tempo"
    assert classify("♩ = 999").kind != "tempo"


def test_extra_lexicon_file(tmp_path):
    f = tmp_path / "mine.toml"
    f.write_text('[expression]\nwords = ["schmachtend"]\n')
    lex = load_lexicon([f])
    assert classify("schmachtend", lex).kind == "expression"
    assert classify("schmachtend").kind == "unknown"
    assert classify("dolce", lex).kind == "expression"      # built-ins stay
