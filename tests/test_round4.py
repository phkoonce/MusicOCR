"""Round 4: multi-rest counts under 2.5 spaces, one staff of a score page inside a part book."""
from __future__ import annotations

import numpy as np
from PIL import Image

from musicocr.engines.copisteria.run import _is_count
from musicocr.scoreops.staffpick import isolate_staff, linked_groups


def test_small_count_digit_centred_over_the_h_bar_is_a_count():
    box = (300, 530, 900, 560)             # the H-bar
    sp = 27.0
    assert _is_count((565, 407, 650, 486), box, sp)          # 2.9 sp tall: always
    assert _is_count((560, 420, 620, 476), box, sp)          # 2.07 sp tall, centred: a count
    assert not _is_count((310, 420, 370, 476), box, sp)      # same size at the bar's left end: a bar number
    assert not _is_count((580, 440, 600, 470), box, sp)      # under 1.5 sp: never


def _score_page(groups=2, per=3, w=600, h=900):
    """A white page with ``groups`` systems of ``per`` staves joined by a vertical line at x=100."""
    a = np.full((h, w), 255, np.uint8)
    y = 60
    for _ in range(groups):
        first = y
        for _ in range(per):
            for k in range(5):
                a[y + 8 * k, 120:560] = 0
            y += 32 + 40
        a[first:y - 40, 100:103] = 0          # the system line, through the gaps
        y += 60
    return a


def test_linked_groups_and_isolate_keep_one_staff(tmp_path):
    from musicocr.text.words import find_staves
    arr = _score_page()
    img = Image.fromarray(arr)
    p = tmp_path / "p001.png"
    img.save(p)
    staves = find_staves(Image.open(p).convert("L"))
    assert len(staves) == 6
    groups = linked_groups(np.asarray(Image.open(p).convert("L")) < 160, staves)
    assert [len(g) for g in groups] == [3, 3]
    assert isolate_staff(p, 2) == 2
    out = np.asarray(Image.open(p).convert("L")) < 160
    rows = np.flatnonzero(out[:, 200:500].mean(axis=1) > 0.5)
    assert len(rows) == 10                  # five lines of the kept staff per system, two systems
    assert (tmp_path / "p001.full.png").exists()


def test_plain_part_page_is_left_alone(tmp_path):
    a = np.full((900, 600), 255, np.uint8)
    for s in range(4):
        for k in range(5):
            a[100 + 200 * s + 8 * k, 120:560] = 0
    p = tmp_path / "p001.png"
    Image.fromarray(a).save(p)
    assert isolate_staff(p, 2) == 0
    assert not (tmp_path / "p001.full.png").exists()


def test_small_count_digit_over_an_empty_bar_is_a_multirest(tmp_path):
    import json
    from musicocr.text.page_text import add_missing_multirest_dets
    meas = lambda x0, x1: {"cls": "measure", "conf": 0.99, "xyxy": [x0, 100, x1, 160]}  # noqa: E731   (space 15 px)
    d, t = tmp_path / "p.dets_v7_1600.json", tmp_path / "p.texts.json"
    base = [meas(0, 1000), meas(1000, 1400)]
    # 2.2 spaces tall (33 px), centred over bar 1: a count; the same digit at the bar's left end: a bar number
    d.write_text(json.dumps(base))
    t.write_text(json.dumps([{"text": "4", "xyxy": [485, 40, 515, 73]}, {"text": "7", "xyxy": [20, 40, 50, 73]}]))
    assert add_missing_multirest_dets(d, t) == 1
    # a stacked pair of small digits is a time signature
    d.write_text(json.dumps(base))
    t.write_text(json.dumps([{"text": "4", "xyxy": [485, 20, 515, 53]}, {"text": "4", "xyxy": [485, 60, 515, 93]}]))
    assert add_missing_multirest_dets(d, t) == 0
