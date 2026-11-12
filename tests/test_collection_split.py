

def test_a_heading_one_book_missed_or_misplaced_follows_the_other_books():
    from musicocr.collection.split import Found, fill_from_peers
    same = lambda: [Found(11, 14, .1), Found(12, 17, .1), Found(13, 18, .4)]
    books = {"1": same(), "2": same(), "3": same(),
             "4": [Found(11, 14, .1), Found(12, 14, .35), Found(13, 18, .4)]}      # 12 found on 11's page
    books["2"] = [f for f in books["2"] if f.num != 12]                                 # 12 not read at all
    out = fill_from_peers(books, log=lambda s: None)
    assert [(f.num, f.page) for f in out["4"]] == [(11, 14), (12, 17), (13, 18)]
    assert [(f.num, f.page) for f in out["2"]] == [(11, 14), (12, 17), (13, 18)]


def test_headings_missing_before_a_misplaced_one_are_still_filled():
    # Horn 2 of the GB books: 4 and 5 unread, and 6 matched a look-alike on page 17 (before where 4 and 5 go).
    from musicocr.collection.split import Found, fill_from_peers
    full = lambda: [Found(3, 16, .1), Found(4, 18, .1), Found(5, 20, .1), Found(6, 22, .1), Found(7, 22, .5)]
    books = {"1": full(), "3": full(), "4": full(),
             "2": [Found(3, 16, .1), Found(6, 17, .3), Found(7, 22, .5)]}
    out = fill_from_peers(books, log=lambda s: None)
    assert [(f.num, f.page) for f in out["2"]] == [(3, 16), (4, 18), (5, 20), (6, 22), (7, 22)]


def test_stale_page_files_of_a_longer_earlier_run_are_not_combined(tmp_path):
    import json
    from musicocr.assemble.combine_cli import _pages_of_last_run
    pages = [tmp_path / f"K.p{n:03d}.musicxml" for n in (1, 2, 3, 4)]
    (tmp_path / "report.json").write_text(json.dumps({"page_files": [str(p) for p in pages[:2]]}))
    assert _pages_of_last_run(tmp_path, pages) == pages[:2]
    assert _pages_of_last_run(tmp_path / "none", pages) == pages      # no report: all of them


def _line(text, x0, y0=100, w=1000):
    words = [{"text": t, "xyxy": [x0 + 60 * i, y0, x0 + 60 * i + 50, y0 + 20]} for i, t in enumerate(text.split())]
    return {"text": text, "x0": x0, "y0": y0, "y1": y0 + 20, "words": words}


def test_a_bar_number_at_the_left_margin_is_not_a_heading():
    from musicocr.collection.split import Entry, _heading
    lines = [_line("13", 110, 250), _line("13. Ah, fors'e lui", 400, 700)]
    assert _heading(lines, 0, Entry(13, "Ah, fors'e lui", ""), 1000) == 1


def test_a_page_number_joined_to_the_heading_line_is_ignored():
    from musicocr.collection.split import Entry, _heading
    lines = [_line("18 10. Largo religioso", 80, 20)]
    assert _heading(lines, 0, Entry(10, "Largo religioso", ""), 1000) == 0


def test_a_bar_number_above_the_heading_does_not_move_its_top(monkeypatch):
    from pathlib import Path
    from musicocr.collection import split
    lines = [_line("39", 80, 230), _line("8. Chor der Priester", 360, 320)]
    monkeypatch.setattr(split, "page_lines", lambda *a, **k: (lines, 1000, 1000))
    found = split.find_pieces(Path("x.pdf"), [split.Entry(8, "Chor der Priester", "")], 1, 1, Path("."), log=lambda s: None)
    assert found[0].y == 0.32
