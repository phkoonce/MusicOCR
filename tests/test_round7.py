"""Round 7: the count-digit reader (musicocr/text/countdigits.py): bold digits above multi-rest H-bars read from the pixels,
self-supervised templates, the sanity rules around them."""
from __future__ import annotations

import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from musicocr.text import countdigits as cd

SP = 10          # staff space (px) of the synthetic pages
TOP = 300        # y of the staff's top line


def _font(size=34):
    return ImageFont.load_default(size=size)


def _glyph_bin(ch: str, size=34) -> np.ndarray:
    im = Image.new("L", (80, 80), 255)
    ImageDraw.Draw(im).text((10, 10), ch, font=_font(size), fill=0, stroke_width=1)
    b = (np.asarray(im) < 128).astype(np.uint8)
    ys, xs = np.nonzero(b)
    return b[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _bank(book=True) -> cd.DigitBank:
    bank = cd.DigitBank()
    for d in range(10):
        for size in (30, 34, 38):
            if book:
                bank.add(d, _glyph_bin(str(d), size))
            else:
                bank.add(d, _glyph_bin(str(d), size), book=False)
    for ch in "pfmt":
        bank.junk.append(cd._norm_glyph(_glyph_bin(ch)))
    return bank


def _page(text="12", x0=200, x1=700, digit_x=None, w=1000, extra=None):
    """Page with one staff (top line at TOP), a multi-rest H-bar over [x0, x1] and ``text`` bold above it."""
    im = Image.new("L", (w, 600), 255)
    dr = ImageDraw.Draw(im)
    for k in range(5):
        dr.line([(100, TOP + k * SP), (w - 100, TOP + k * SP)], fill=0, width=1)
    dr.rectangle([x0, TOP + 17, x1, TOP + 23], fill=0)
    if text:
        cx = digit_x if digit_x is not None else (x0 + x1) // 2
        f = _font(34)
        tw = dr.textlength(text, font=f)
        dr.text((cx - tw / 2, TOP - 62), text, font=f, fill=0, stroke_width=1)
    if extra:
        extra(dr)
    dets = [{"cls": "measure", "conf": 1.0, "xyxy": [100.0, float(TOP), float(w - 100), float(TOP + 4 * SP)]},
            {"cls": "multiRest", "conf": 0.99, "xyxy": [float(x0), float(TOP - SP), float(x1), float(TOP + 28)]}]
    return np.asarray(im), dets


def test_blobs_found_and_glued_digits_split():
    gray, dets = _page("12")
    g = cd.find_count_blobs(gray, dets[1]["xyxy"], SP, staff_top_for := cd.staff_top_for(dets, dets[1]["xyxy"], SP))
    assert staff_top_for == TOP and len(g) == 2
    # one glued component (width > height) is cut into its digits
    m = np.zeros((30, 52), np.uint8)
    m[:, 2:20] = 1
    m[:, 26:50] = 1
    m[14:16, 20:26] = 1
    pieces = cd.split_glued((0, 0, 52, 30, m))
    assert len(pieces) == 2 and all(p[4].shape[1] <= 30 for p in pieces)


def test_classifier_reads_digits_and_rejects_letters():
    bank = _bank()
    for d in "0123456789":
        got, sim, margin = bank.classify(_glyph_bin(d, 32))
        assert got == int(d) and sim > 0.8
    assert bank.classify(_glyph_bin("p"))[0] is None
    assert bank.classify(np.ones((30, 30), np.uint8))[0] is None      # a block of ink is nothing


def test_font_templates_alone_are_a_fallback():
    bank = _bank(book=False)
    assert bank.classify(_glyph_bin("4", 32))[0] == 4


def test_mislabelled_book_template_is_dropped():
    bank = _bank()
    for _ in range(3):
        bank.add(6, _glyph_bin("9"))                       # a 9 the OCR read as 6
    dropped = bank.drop_contradicted()
    assert dropped >= 3 and bank.classify(_glyph_bin("9"))[0] == 9


def test_read_page_fills_a_missing_count_and_corrects_a_misread():
    bank = _bank()
    gray, dets = _page("9")
    found = cd.read_page(bank, gray, dets, [])
    assert [(f["n"], f["old"]) for f in found] == [(9, None)]
    cx = (found[0]["box"][0] + found[0]["box"][2]) / 2
    ocr6 = [{"text": "6", "xyxy": [cx - 8, TOP - 60, cx + 8, TOP - 30], "role": "words"}]
    found = cd.read_page(bank, gray, dets, ocr6)
    assert [(f["n"], f["old"]) for f in found] == [(9, "6")]
    # an OCR read that agrees is left alone
    assert cd.read_page(bank, gray, dets, [{"text": "9", "xyxy": ocr6[0]["xyxy"], "role": "words"}]) == []
    # an OCR box elsewhere on the page is not the count of this bar: nothing to correct, the glyph is read as a gap
    far = [{"text": "6", "xyxy": [20, 20, 40, 60], "role": "words"}]
    assert [f["n"] for f in cd.read_page(bank, gray, dets, far)] == [9]


def test_sanity_rules():
    bank = _bank()
    gray, dets = _page("1")
    assert cd.read_page(bank, gray, dets, []) == []                 # a rest of 1 has no count
    gray, dets = _page("4", digit_x=205)                           # off-centre at the bar's start: a bar number
    assert cd.read_page(bank, gray, dets, []) == []
    gray, dets = _page("4", x0=105, x1=185)                       # centred, but the first bar of its system
    assert cd.read_page(bank, gray, dets, []) == []
    gray, dets = _page("4")
    ring = [[450 - 30, TOP - 70, 450 + 30, TOP - 20]]
    assert cd.read_page(bank, gray, dets, [], rings=ring) == []     # a circled rehearsal number
    gray, dets = _page(None)
    assert cd.read_page(bank, gray, dets, []) == []                 # nothing above the bar
    gray, dets = _page("p")
    assert cd.read_page(bank, gray, dets, []) == []                 # a dynamic mark is not a count


def test_read_counts_writes_texts_json(tmp_path):
    gray, dets = _page("8")
    Image.fromarray(gray).save(tmp_path / "p001.png")
    (tmp_path / "p001.dets_v7_1.json").write_text(json.dumps(dets))
    texts = tmp_path / "p001.texts.json"
    n = cd.read_counts(tmp_path / "p001.png", tmp_path / "p001.dets_v7_1.json", texts, _bank())
    assert n == 1
    t = json.loads(texts.read_text())
    assert [x["text"] for x in t] == ["8"] and t[0]["source"] == "countdigits"
    # the engine's rule accepts the box as the bar's count: tall and centred over the H-bar
    bar = dets[1]["xyxy"]
    assert cd.count_text_for(t, bar, SP) is not None
    # a second run finds the count already there
    assert cd.read_counts(tmp_path / "p001.png", tmp_path / "p001.dets_v7_1.json", texts, _bank()) == 0


def test_shipped_bank_loads_and_reads_its_own_digits():
    bank = cd.DigitBank.load(cd.BANK_FILE)
    assert all(bank.book[k] for k in range(10)) and bank.junk
    t = bank.book[3][0]
    assert t.shape == (cd.SIZE, cd.SIZE)


def test_config_flag_defaults_on():
    from musicocr.config import CopisteriaConfig
    assert CopisteriaConfig().read_count_digits is True
    assert CopisteriaConfig.from_dict({"read_count_digits": False}).read_count_digits is False


def test_a_tempo_figure_over_the_bar_does_not_become_its_count(tmp_path):
    """Braveheart hn3: "Giocoso = 128" stands over the left end of a 5-bar rest; the OCR box would be taken as the count."""
    gray, dets = _page("5")
    Image.fromarray(gray).save(tmp_path / "p001.png")
    (tmp_path / "p001.dets_v7_1.json").write_text(json.dumps(dets))
    tempo = {"text": "128", "xyxy": [230.0, TOP - 55.0, 290.0, TOP - 25.0], "role": "words"}
    (tmp_path / "p001.texts.json").write_text(json.dumps([tempo]))
    assert cd.engine_counts([tempo], dets[1]["xyxy"], SP) == [tempo]
    found = cd.read_page(_bank(), gray, dets, [tempo])
    assert [(f["n"], f["old"], f["add"]) for f in found] == [(5, "128", True)]
    assert cd.read_counts(tmp_path / "p001.png", tmp_path / "p001.dets_v7_1.json", tmp_path / "p001.texts.json", _bank()) == 1
    t = json.loads((tmp_path / "p001.texts.json").read_text())
    assert [x["text"] for x in t] == ["5"]


def test_tempo_figures_are_dropped_from_the_digit_texts(tmp_path):
    texts = [{"text": "80", "xyxy": [1300, 455, 1350, 520], "role": "words"}, {"text": "5", "xyxy": [900, 455, 940, 520], "role": "words"}]
    layer = [{"text": "Andante sostenuto", "kind": "tempo", "xyxy": [854, 451, 1364, 548]},
             {"text": "dim", "kind": "expression", "xyxy": [880, 440, 960, 540]}]
    (tmp_path / "p.texts.json").write_text(json.dumps(texts))
    (tmp_path / "p.textlayer.json").write_text(json.dumps(layer))
    # the "5" lies inside the tempo box too (x 854-1364): both are dropped; one outside it survives
    texts.append({"text": "3", "xyxy": [100, 455, 140, 520], "role": "words"})
    (tmp_path / "p.texts.json").write_text(json.dumps(texts))
    assert cd.drop_tempo_digits(tmp_path / "p.texts.json", tmp_path / "p.textlayer.json") == 2
    assert [t["text"] for t in json.loads((tmp_path / "p.texts.json").read_text())] == ["3"]
    assert cd.drop_tempo_digits(tmp_path / "p.texts.json", tmp_path / "missing.json") == 0
