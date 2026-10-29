"""musicocr.musicxml.merge.merge_pages on tiny synthetic scores."""
from __future__ import annotations

from music21 import converter, note, stream

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
