"""musicocr.musicxml.merge.merge_pages on tiny synthetic scores."""
from __future__ import annotations

from music21 import clef, converter, dynamics, meter, note, spanner, stream

from musicocr.musicxml.merge import merge_pages


def _page(path, pitches):
    part = stream.Part()
    for i, p in enumerate(pitches, start=1):
        m = stream.Measure(number=i)
        m.append(note.Note(p, type="whole"))
        part.append(m)
    sc = stream.Score()
    sc.insert(0, part)
    sc.write("musicxml", fp=str(path))
    return path


def test_merge_appends_and_renumbers(tmp_path):
    p1 = _page(tmp_path / "p1.musicxml", ["C4", "D4"])
    p2 = _page(tmp_path / "p2.musicxml", ["E4", "F4", "G4"])
    dest = tmp_path / "out.musicxml"
    merged, skipped = merge_pages([p1, p2], dest, log=lambda *_: None)
    assert merged == dest and skipped == []
    sc = converter.parse(str(dest))
    measures = list(sc.parts[0].getElementsByClass("Measure"))
    assert [m.number for m in measures] == [1, 2, 3, 4, 5]


def test_merge_skips_unreadable(tmp_path):
    p1 = _page(tmp_path / "p1.musicxml", ["C4"])
    bad = tmp_path / "bad.musicxml"
    bad.write_text("not xml")
    merged, skipped = merge_pages([p1, bad], tmp_path / "o.musicxml", log=lambda *_: None)
    assert merged is not None and skipped == [bad]


def test_merge_keeps_later_pages_slurs_and_drops_restated_signatures(tmp_path):
    def page(path):
        part = stream.Part()
        m = stream.Measure(number=1)
        m.append(clef.TrebleClef())
        m.append(meter.TimeSignature("2/4"))
        a, b = note.Note("C4"), note.Note("D4")
        m.append([a, b])
        part.append(m)
        part.insert(0, spanner.Slur(a, b))
        sc = stream.Score()
        sc.insert(0, part)
        sc.write("musicxml", fp=str(path))
        return path

    dest = tmp_path / "out.musicxml"
    merge_pages([page(tmp_path / "p1.musicxml"), page(tmp_path / "p2.musicxml")], dest, log=lambda *_: None)
    text = dest.read_text()
    assert text.count("<slur ") == 4
    assert text.count("<time") == 1 and text.count("<clef") == 1


def test_merged_crescendo_has_no_zero_spread(tmp_path):
    part = stream.Part()
    m = stream.Measure(number=1)
    a, b = note.Note("C4"), note.Note("D4")
    m.append([a, b])
    part.append(m)
    part.insert(0, dynamics.Crescendo(a, b))
    sc = stream.Score()
    sc.insert(0, part)
    page = tmp_path / "p.musicxml"
    sc.write("musicxml", fp=str(page))
    dest = tmp_path / "out.musicxml"
    merge_pages([page, page], dest, log=lambda *_: None)
    assert "<wedge" in dest.read_text() and "spread" not in dest.read_text()
