"""Tests for musicocr.measure_normalize against the "note read a step too
long" homr failure mode documented in omr-eval/README.md."""
from __future__ import annotations

import pytest

music21 = pytest.importorskip("music21")
from music21 import clef, meter, note, stream  # noqa: E402

from musicocr.measure_normalize import normalize_part  # noqa: E402


def _part(measure_lengths: list[float]) -> stream.Part:
    """Build a 4/4 part whose measures each hold a single note of the given length."""
    p = stream.Part()
    p.append(clef.TrebleClef())
    p.append(meter.TimeSignature("4/4"))
    for i, length in enumerate(measure_lengths, 1):
        m = stream.Measure(number=i)
        m.append(note.Note("C4", quarterLength=length))
        p.append(m)
    return p


def test_overlong_measure_is_truncated_at_barline():
    p = _part([4, 5, 4])  # middle measure: a "5 beats, expected 4" misread

    fixed = normalize_part(p, "test", log=lambda s: None)

    assert fixed == 1
    measures = list(p.getElementsByClass(stream.Measure))
    assert measures[1].duration.quarterLength == pytest.approx(4.0)
    assert measures[0].duration.quarterLength == pytest.approx(4.0)
    assert measures[2].duration.quarterLength == pytest.approx(4.0)


def test_underlong_interior_measure_is_padded_with_rest():
    p = _part([4, 2, 4])  # middle measure short two beats

    fixed = normalize_part(p, "test", log=lambda s: None)

    assert fixed == 1
    measures = list(p.getElementsByClass(stream.Measure))
    assert measures[1].duration.quarterLength == pytest.approx(4.0)
    rests = list(measures[1].getElementsByClass(note.Rest))
    assert len(rests) == 1
    assert rests[0].duration.quarterLength == pytest.approx(2.0)


def test_pickup_and_final_measure_are_left_alone():
    p = _part([2, 4, 3])  # pickup first measure, incomplete final measure

    fixed = normalize_part(p, "test", log=lambda s: None)

    assert fixed == 0
    measures = list(p.getElementsByClass(stream.Measure))
    assert measures[0].duration.quarterLength == pytest.approx(2.0)
    assert measures[2].duration.quarterLength == pytest.approx(3.0)


def test_correct_measures_are_untouched():
    p = _part([4, 4, 4])

    fixed = normalize_part(p, "test", log=lambda s: None)

    assert fixed == 0


def test_later_measures_are_repositioned_after_a_fix():
    p = _part([4, 5, 4])
    normalize_part(p, "test", log=lambda s: None)

    measures = list(p.getElementsByClass(stream.Measure))
    assert measures[0].offset == pytest.approx(0.0)
    assert measures[1].offset == pytest.approx(4.0)
    assert measures[2].offset == pytest.approx(8.0)
