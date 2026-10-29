"""Instrument -> transposition table, shared by every score we process.

Each value is the number of semitones to ADD to a *written* pitch to get the
*concert* (sounding) pitch -- so French horn is -7 (written C sounds F, a
perfect fifth lower). Pass it straight to ``musicocr.scoreops.transpose``.

Some instruments are notated differently depending on clef: a Bb baritone /
tenor horn reads treble clef a major ninth (-14) above where it sounds, but is
written at concert pitch in bass clef (0). Those entries are dicts keyed by
clef family -- ``"treble"`` (G clef), ``"bass"`` (F clef), ``"c"`` (alto/tenor)
-- with an optional ``"default"``. A clef missing from the dict is an error
rather than a guess.

To add an instrument: add it to ``TRANSPOSITIONS`` and any alternate spellings
to ``ALIASES`` (lookup is case-insensitive and ignores a trailing part number,
so "Horn in F 3" finds "horn in f").
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, Union

Semitones = Union[int, Dict[str, int]]

TRANSPOSITIONS: Dict[str, Semitones] = {
    "french horn": -7,
    "baritone horn": {"treble": -14, "bass": 0},
    "tenor horn": {"treble": -14, "bass": 0},
    "euphonium": {"treble": -14, "bass": 0},   # same convention as baritone horn
    # Plain "tuba" has no treble entry on purpose: treble-clef tuba parts are
    # transposed by key (Eb: octave + major 6th; Bb: two octaves + major 2nd),
    # so the part must say which.
    "tuba": {"bass": 0},
    "eb tuba": {"treble": -21, "bass": 0},
    "bb tuba": {"treble": -26, "bass": 0},
    "trombone": 0,
    "bass trombone": 0,
    "trumpet": -2,
    "clarinet in bb": -2,
}

ALIASES: Dict[str, str] = {
    "horn": "french horn",
    "horn in f": "french horn",
    "f horn": "french horn",
    "baritone": "baritone horn",
    "bb baritone horn": "baritone horn",
    "tenor horn in bb": "tenor horn",
    "tuba in eb": "eb tuba",
    "tuba in bb": "bb tuba",
    "bbb tuba": "bb tuba",
    "tuba in bbb": "bb tuba",
    "bass tuba": "tuba",
    "trumpet in bb": "trumpet",
    "bb trumpet": "trumpet",
    "bb clarinet": "clarinet in bb",
    "clarinet": "clarinet in bb",
}

# Display name, abbreviation, MusicXML instrument-sound and 1-based MIDI program,
# written into the part so MuseScore picks the right instrument (and, with the
# <transpose> element, its transposition). Keyed like ``TRANSPOSITIONS``.
INFO: Dict[str, Tuple[str, str, str, int]] = {
    "french horn": ("Horn in F", "Hn.", "brass.french-horn", 61),
    "baritone horn": ("Baritone Horn", "Bar.", "brass.euphonium", 59),
    "tenor horn": ("Tenor Horn", "T. Hn.", "brass.euphonium", 59),
    "euphonium": ("Euphonium", "Euph.", "brass.euphonium", 59),
    "tuba": ("Tuba", "Tba.", "brass.tuba", 59),
    "eb tuba": ("Tuba in Eb", "Tba.", "brass.tuba", 59),
    "bb tuba": ("Tuba in Bb", "Tba.", "brass.tuba", 59),
    "trombone": ("Trombone", "Tbn.", "brass.trombone", 58),
    "bass trombone": ("Bass Trombone", "B. Tbn.", "brass.trombone.bass", 58),
    "trumpet": ("Trumpet in Bb", "Tpt.", "brass.trumpet", 57),
    "clarinet in bb": ("Clarinet in Bb", "Cl.", "wind.reed.clarinet", 72),
}

_PART_NUMBER = re.compile(r"\s+(\d+(st|nd|rd|th)?|i{1,3}|iv)$")


def normalize_name(name: str) -> str:
    """Lowercase, unify flat signs, drop punctuation and a trailing part number."""
    s = name.lower().replace("♭", "b").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = " ".join(s.split())
    return _PART_NUMBER.sub("", s)


def canonical_name(name: str) -> Optional[str]:
    """The ``TRANSPOSITIONS`` key for ``name``, or None if we don't know it."""
    s = normalize_name(name)
    s = ALIASES.get(s, s)
    return s if s in TRANSPOSITIONS else None


def semitones_for(instrument: str, clef: Optional[str] = None) -> int:
    """Written -> concert semitone shift for ``instrument`` read in ``clef``.

    ``clef`` is "treble", "bass" or "c"; it is only needed for instruments whose
    transposition depends on it. Raises ValueError for an unknown instrument or
    a clef the table has no entry for.
    """
    canon = canonical_name(instrument)
    if canon is None:
        raise ValueError(
            f"unknown instrument {instrument!r}; known: {', '.join(sorted(TRANSPOSITIONS))}"
            " (add it to musicocr/scoreops/instruments.py)"
        )
    spec = TRANSPOSITIONS[canon]
    if isinstance(spec, int):
        return spec
    if clef in spec:
        return spec[clef]
    if "default" in spec:
        return spec["default"]
    raise ValueError(
        f"{canon} transposes by clef ({', '.join(sorted(spec))}); got clef {clef!r}"
    )
