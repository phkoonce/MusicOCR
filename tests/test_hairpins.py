"""Long hairpins found from pixels (musicocr/text/hairpins.py)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from musicocr.text.hairpins import find_hairpins

SP = 20


def _page(draw_on):
    im = Image.new("L", (1600, 600), 255)
    d = ImageDraw.Draw(im)
    draw_on(d)
    return np.asarray(im)


def test_long_hairpin_found_across_barlines():
    def draw(d):
        d.line([(200, 300), (1400, 285)], fill=0, width=3)
        d.line([(200, 300), (1400, 315)], fill=0, width=3)
        for x in (500, 900):                       # barlines through the gap
            d.line([(x, 100), (x, 500)], fill=0, width=4)

    (h,) = find_hairpins(_page(draw), [(100, 180), (420, 500)], SP)
    assert h["cls"] == "hairpin_cres"
    assert h["xyxy"][0] < 260 and h["xyxy"][2] > 1340


def test_slur_and_single_line_are_not_hairpins():
    def draw(d):
        d.arc([200, 250, 900, 330], 0, 180, fill=0, width=4)
        d.line([(1000, 300), (1500, 296)], fill=0, width=3)

    assert find_hairpins(_page(draw), [(100, 180), (420, 500)], SP) == []
