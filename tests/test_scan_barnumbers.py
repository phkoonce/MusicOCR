"""Bar-number OCR fallback for scanned PDFs (musicocr.assemble.alignment.systems)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from musicocr.assemble.alignment import systems
from musicocr.assemble.alignment.systems import _increasing_by_y, _staff_systems, pdf_words_or_ocr


def _page(staves=((300, 20), (700, 20)), w=1000, h=1200) -> Image.Image:
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for top, gap in staves:
        for i in range(5):
            d.line([(150, top + i * gap), (w - 50, top + i * gap)], fill=0, width=2)
    return img


def test_staff_systems_finds_each_staff():
    found = _staff_systems(_page())
    assert [(t, b) for t, b, _ in found] == [(300, 380), (700, 780)]
    assert all(abs(left - 150) <= 12 for *_, left in found)


def test_staff_systems_ignores_unevenly_spaced_lines():
    img = Image.new("L", (1000, 600), 255)
    d = ImageDraw.Draw(img)
    for y in (100, 110, 140, 200, 330):
        d.line([(150, y), (950, y)], fill=0, width=2)
    assert _staff_systems(img) == []


def test_increasing_by_y_drops_misreads():
    words = [(0, 10, 1, 11, "1"), (0, 20, 1, 21, "9"), (0, 30, 1, 31, "5"), (0, 40, 1, 41, "9"),
             (0, 50, 1, 51, "13")]
    assert [w[4] for w in _increasing_by_y(words)] == ["1", "5", "9", "13"]
    assert _increasing_by_y([]) == []


def test_pdf_words_or_ocr_prefers_text_layer(monkeypatch):
    layer = [(0.0, 1.0, 2.0, 3.0, "7")]
    monkeypatch.setattr(systems, "pdf_words", lambda *a, **k: layer)
    assert pdf_words_or_ocr("whatever.pdf", 1) == layer


def test_pdf_words_or_ocr_scan_fallback_and_cache(monkeypatch, tmp_path):
    import musicocr.text.ocr_backends as ob
    pdf = tmp_path / "scan.pdf"
    _page().convert("RGB").save(pdf, "PDF", resolution=150)
    monkeypatch.setattr(systems, "pdf_words", lambda *a, **k: [])
    monkeypatch.setattr(ob, "_CACHE", tmp_path / "cache")
    calls = []

    def fake_ocr(png, backend):
        calls.append(png)
        return [{"text": "12", "xyxy": (30, 30, 60, 50)}, {"text": "abc", "xyxy": (0, 0, 5, 5)}]

    monkeypatch.setattr(ob, "ocr_words", fake_ocr)
    words = pdf_words_or_ocr(str(pdf), 1)
    assert calls and {w[4] for w in words} == {"12"}      # digits only
    assert len(words) == 1                                # same value on both staves: only an increasing run is kept
    n = len(calls)
    assert pdf_words_or_ocr(str(pdf), 1) == words and len(calls) == n    # second call served from cache


def test_pick_system_starts_skips_stray_digits():
    import re
    tok = lambda t, x: (x, 0, x + 5, 8, t, re.fullmatch(r"(\d+)\s*[-–](\d+)", t))
    cands = [[tok("1", 10)], [tok("40", 10), tok("11", 40)], [tok("19-25", 90)], [], [tok("26", 10)]]
    picked = systems._pick_system_starts(cands)
    assert [None if p is None else p[4] for p in picked] == ["1", "11", "19-25", None, "26"]


def test_range_token_tolerates_stray_marks_but_not_glued_numbers():
    from musicocr.assemble.alignment.systems import _RANGE_TOKEN
    assert _RANGE_TOKEN.fullmatch("130-133ł").groups() == ("130", "133")
    assert _RANGE_TOKEN.fullmatch("-3-") is None


def test_align_never_puts_a_played_bar_on_a_printed_multirest_slot():
    from musicocr.assemble.combine import align_to_target
    # source: R R N N R N (rest/note); print: rests at slots 0,1 and 4..7 (multi-rest spans), 8 slots
    src = [1.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    ref = [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]          # a noisy reference that rests where this part plays
    must = [True, True, False, False, True, True, True, True]
    ins = [0.01 if m else 2.0 for m in must]
    slots = align_to_target(src, ref, ins, must)
    assert slots[2] == 2 and slots[3] == 3 and slots[4] == 4        # the notes stay on bars 3 and 4


def test_a_rest_bars_meter_change_pins_where_it_lands():
    from musicocr.assemble.combine import align_to_target
    # source: R(declares a change) N N N; the print has 6 slots with two lost rests before it. The other
    # parts change meter at slot 2, so the rest bar belongs there, not at slot 0.
    src = [1.0, 0.0, 0.0, 0.0]
    ref = [0.5] * 6
    ins = [0.01] * 6
    slots = align_to_target(src, ref, ins, None, [True, None, None, None], [0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
    assert slots.index(0) == 2


def test_digits_far_below_the_staff_are_not_system_starts():
    from musicocr.assemble.alignment.systems import _start_candidates
    top, bot = 100, 200                                  # staff space 25
    near = (14, 230, 30, 245, "104", None)               # 1.2 spaces under the staff: a bar number
    tempo = (14, 308, 30, 320, "104", None)              # 4.3 spaces under: "= 104" before the next system
    rng = (300, 330, 330, 345, "116-123", object())      # a range label is kept wherever it sits
    assert _start_candidates([tempo, near, rng], top, bot) == [near, rng]


def test_common_left_is_where_most_staves_start_and_ignores_a_binding():
    from musicocr.assemble.alignment.systems import _common_left
    starts = [[20, 150], [60, 155], [152], [140], [300], [149, 400]]      # blobs of a spiral binding at 20 / 60
    assert 145 <= _common_left(starts, 1200) <= 155
    assert _common_left([[20], [300], [700], [90]], 1200) is None          # no agreement: keep the per-staff reading
    assert _common_left([[150], [152]], 1200) is None                      # too few staves to trust


def test_a_first_system_number_high_on_the_page_is_not_taken_for_the_header():
    from musicocr.assemble.alignment.systems import _below_header, page_text_layout
    words = [(50.0, 45.0, 62.0, 55.0, "30"), (50.0, 118.0, 62.0, 128.0, "34"),
             (90.0, 30.0, 110.0, 40.0, "07")]
    starts, _ = page_text_layout(_below_header(words))
    assert [n for _, n in starts] == [30, 34]       # the header's "07" sits right of the margin: not a start
