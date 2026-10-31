

def test_a_heading_one_book_missed_or_misplaced_follows_the_other_books():
    from musicocr.collection.split import Found, fill_from_peers
    same = lambda: [Found(11, 14, .1), Found(12, 17, .1), Found(13, 18, .4)]
    books = {"1": same(), "2": same(), "3": same(),
             "4": [Found(11, 14, .1), Found(12, 14, .35), Found(13, 18, .4)]}      # 12 found on 11's page
    books["2"] = [f for f in books["2"] if f.num != 12]                                 # 12 not read at all
    out = fill_from_peers(books, log=lambda s: None)
    assert [(f.num, f.page) for f in out["4"]] == [(11, 14), (12, 17), (13, 18)]
    assert [(f.num, f.page) for f in out["2"]] == [(11, 14), (12, 17), (13, 18)]
