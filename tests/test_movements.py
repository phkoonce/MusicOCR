"""Movement headings (musicocr.scoreops.movements) and the staff-safe margin crop."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

from musicocr.assemble.alignment.systems import all_staves
from musicocr.engines.raster import drop_detection_caches
from musicocr.preprocess import _staff_band
from musicocr.scoreops.movements import apply_movements, find_headings, in_sequence
from musicocr.text.words import Staff

W = 2000
STAVES = [Staff(600, 692, 150), Staff(1000, 1092, 150)]


def _word(text, x, y, w=80, h=60):
    return {"text": text, "xyxy": [x, y, x + w, y + h]}


def test_centred_roman_numeral_with_tempo():
    words = [_word("II.", W / 2 - 40, 350), _word("Fast", 300, 450), _word("and", 400, 450),
             _word("rhythmic", 500, 450), _word("(J.", 640, 450), _word("=", 730, 450),
             _word("80-84)", 820, 450)]
    (h,) = find_headings(words, STAVES, W)
    assert (h.number, h.heading, h.staff, h.text, h.bpm) == (2, "II.", 0, "Fast and rhythmic", "80-84")


def test_tempo_level_with_the_numeral():
    words = [_word("II", W / 2 - 40, 350, h=70), _word("Andante", 300, 405)]
    (h,) = find_headings(words, STAVES, W)
    assert (h.heading, h.text) == ("II", "Andante")


def test_heading_over_a_later_staff():
    (h,) = find_headings([_word("III.", W / 2 - 40, 850)], STAVES, W)
    assert (h.number, h.staff) == (3, 1)


def test_not_headings():
    assert not find_headings([_word("II.", 200, 350)], STAVES, W)                    # not centred
    assert not find_headings([_word("II.", 960, 350), _word("Horn", 840, 350)], STAVES, W)  # part of a line
    assert not find_headings([_word("II.", 960, 620)], STAVES[:1], W)                # on the staff


def test_numbering_must_go_up():
    h = lambda n: find_headings([_word(["I.", "II.", "III."][n - 1], 960, 350)], STAVES, W)[0]
    assert in_sequence([h(1), h(2)])
    assert in_sequence([h(2)])
    assert not in_sequence([h(1)])                       # a single movement: nothing to split
    assert in_sequence([h(1), h(3)])                     # one missed: keep the others
    assert not in_sequence([h(3), h(2)])
    assert not in_sequence([h(3)])


def test_apply_movements():
    bars = "".join(f'<measure number="{i}"/>' for i in range(1, 5))
    root = ET.fromstring(f'<score-partwise><part id="P1">{bars}</part></score-partwise>')
    ET.SubElement(root.find("part")[0], "attributes").append(ET.fromstring("<time><beats>6</beats></time>"))
    mv = [{"bar": 0, "heading": "I.", "text": "Slowly", "bpm": "", "dotted": None},
          {"bar": 2, "heading": "II.", "text": "Fast", "bpm": "80-84", "dotted": None}]
    assert apply_movements(root, mv) == 2
    ms = root.find("part").findall("measure")
    assert ms[1].findtext("barline/bar-style") == "light-heavy"
    assert ms[2].find("print").get("new-system") == "yes"
    assert [w.text for w in ms[2].iter("words")] == ["II.\n", "Fast"]
    assert ms[2].find(".//beat-unit-dot") is not None                # compound meter
    assert ms[2].find(".//sound").get("tempo") == "120"


def _page(first_staff_top):
    im = Image.new("L", (1000, 1400), 255)
    d = ImageDraw.Draw(im)
    for top in (first_staff_top, 500, 700, 900, 1100):
        for k in range(5):
            d.rectangle([60, top + 12 * k, 940, top + 12 * k + 1], fill=0)
    return im


def test_crop_keeps_a_short_first_block_that_holds_a_staff():
    band = _staff_band(_page(100), 0.035)
    assert band is None or band[0] == 0


def test_beams_do_not_hide_a_staff():
    im = _page(100)
    ImageDraw.Draw(im).rectangle([200, 114, 300, 123], fill=0)       # beam fusing two lines
    ImageDraw.Draw(im).rectangle([320, 138, 420, 147], fill=0)
    assert len(all_staves(im)) == 5


def test_detection_caches_are_dropped(tmp_path):
    names = ["p002.dets_v7_1632.json", "p002.big_dets.json", "p002.texts.json"]
    for n in names:
        (tmp_path / n).write_text("[]")
    drop_detection_caches(tmp_path, "p002")
    assert [p.name for p in tmp_path.iterdir()] == ["p002.texts.json"]
