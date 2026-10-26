"""Bar-number OCR fallback for scanned PDFs (musicocr.alignment.systems)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from musicocr.alignment import systems
from musicocr.alignment.systems import _increasing_by_y, _staff_systems, pdf_words_or_ocr


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
    import musicocr.ocr_backends as ob
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
