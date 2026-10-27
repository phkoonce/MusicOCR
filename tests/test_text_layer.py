"""Placing, filtering and emitting phrases (musicocr/text_layer.py, copisteria_run.py text layer)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np

from musicocr.config import Config, CopisteriaConfig, TextLayerConfig, ConfigError
from musicocr.copisteria_run import build_direction, place_items
from musicocr.text_layer import TextLayerSettings, build_items, is_dotted_beat
from musicocr.text_words import Staff, Word

STAVES = [Staff(1000, 1104, 30), Staff(1400, 1504, 30), Staff(1800, 1904, 30)]   # sp = 26


def W(text, x0, y0, x1=None, y1=None, source="ocr"):
    return Word(text, (x0, y0, x1 if x1 is not None else x0 + 14 * len(text), y1 if y1 is not None else y0 + 36), source)


def kinds(res):
    return [(i["kind"], i["text"]) for i in res.items]


def test_words_near_a_staff_are_kept_with_their_kind():
    res = build_items([W("marc.", 300, 1340), W("cantabile", 800, 1745)], STAVES)
    assert kinds(res) == [("technique", "marc."), ("expression", "cantabile")]


def test_title_block_above_the_first_staff_is_furniture_but_a_tempo_mark_is_not():
    words = [W("TRUMPET", 30, 800), W("1M1", 2600, 790),                  # 5+ sp above staff 1
             W("(J=124)", 400, 930, 560, 970)]                             # 1.5 sp above
    res = build_items(words, STAVES)
    assert kinds(res) == [("tempo", "")]
    assert res.items[0]["bpm"] == 124 and res.items[0]["parens"]
    assert {d["text"] for d in res.dropped} == {"TRUMPET"}           # "1M1" is not a marking at all


def test_unknown_text_above_a_staff_is_flagged_below_is_dropped():
    res = build_items([W("Flugelhorn", 300, 1340), W("Flugelhorn", 900, 1130)], STAVES)
    assert [(i["kind"], i["flag"]) for i in res.items] == [("unknown", True)]
    assert res.items[0]["xyxy"][1] == 1340
    assert len(res.dropped) == 1
    res = build_items([W("Flugelhorn", 300, 1340)], STAVES, cfg=TextLayerSettings(mode="on"))
    assert res.items == []


def test_mode_off_does_nothing():
    assert build_items([W("marc.", 300, 1340)], STAVES, cfg=TextLayerSettings(mode="off")).items == []


def test_dynamics_and_numbers_are_ignored():
    words = [W("mf", 300, 1340), W("64", 500, 1340), W("ff", 700, 1130), W("47-55", 900, 1130)]
    assert build_items(words, STAVES).items == []


def test_text_made_of_detected_music_is_not_flagged():
    dets = [{"cls": "artic_acc", "xyxy": [305, 1342, 335, 1370]}]
    assert build_items([W("Ibv", 300, 1340)], STAVES, dets=dets).items == []
    assert kinds(build_items([W("marc.", 300, 1340)], STAVES, dets=dets)) == [("technique", "marc.")]


def test_far_text_is_out_of_zone():
    res = build_items([W("marc.", 300, 2100)], STAVES)                    # 7.5 sp under the last staff
    assert res.items == [] and res.dropped[0]["why"] == "out of zone"


def test_tempo_mark_below_a_staff_is_not_a_tempo():
    # nearer to the staff above than the one below: "=200" under a hairpin is a bar number
    assert build_items([W("=200", 300, 1130)], STAVES).items == []


def test_rehearsal_letters_only_from_the_pdf_text_layer():
    ocr = build_items([W("B", 300, 1340, 330, 1400)], STAVES)
    pdf = build_items([W("B", 300, 1340, 330, 1400, source="pdf")], STAVES)
    assert ocr.items == [] and kinds(pdf) == [("rehearsal", "B")]


def test_tempo_item_carries_words_and_unit():
    res = build_items([W("Sl.", 300, 1340), W("Faster", 345, 1340), W("J", 440, 1340), W("=", 460, 1340),
                       W("103", 490, 1340)], STAVES)
    assert kinds(res) == [("tempo", "Sl. Faster")]
    assert res.items[0]["bpm"] == 103 and res.items[0]["beat_unit"] == "quarter"


def test_dotted_beat_unit_from_pixels():
    def mark(dotted):
        im = np.zeros((40, 200), bool)
        im[2:34, 20:23] = True                                            # stem
        im[26:34, 10:22] = True                                           # head
        if dotted:
            im[30:34, 30:34] = True                                       # dot
        im[14:17, 60:80] = True                                           # '='
        im[22:25, 60:80] = True
        for x in (100, 130):                                              # digits (tall)
            im[4:34, x:x + 4] = True
            im[4:7, x:x + 18] = True
        return im
    assert is_dotted_beat(mark(True)) is True
    assert is_dotted_beat(mark(False)) is False
    assert is_dotted_beat(np.zeros((10, 10), bool)) is None


def _geometry():
    # two staves, each with two bars (x ranges), sp 26
    return [{"y0": 1000, "y1": 1104, "sp": 26, "bars": [(40, 900), (900, 1800)]},
            {"y0": 1400, "y1": 1504, "sp": 26, "bars": [(40, 1000), (1000, 1800)]}]


def test_items_go_to_the_nearest_staff_and_the_bar_their_left_edge_is_in():
    it = lambda t, x, y: {"text": t, "kind": "technique", "xyxy": [x, y, x + 100, y + 36]}
    placed = place_items([it("a", 500, 940), it("b", 950, 1340), it("c", 20, 1530), it("d", 950, 2000)], _geometry())
    flat = {p[0]["text"]: (k, p[1]) for k, v in placed.items() for p in v}
    assert flat["a"] == ((0, 0), False)           # above staff 0, bar 0
    assert flat["b"] == ((1, 0), False)           # 950 starts bar 0 of staff 1 (bar 1 starts at 1000 - 26)
    assert flat["c"] == ((1, 0), True)            # below staff 1
    assert "d" not in flat                        # far under the last staff: attached to nothing


def test_direction_xml_for_tempo_words_and_flagged_text():
    meas = ET.Element("measure")
    sub = lambda parent, tag, text=None, **a: _sub(parent, tag, text, **a)
    build_direction(meas, {"kind": "tempo", "text": "Sl. Faster", "bpm": 103, "style": "bold", "dotted": True,
                           "parens": True}, False, None, sub)
    build_direction(meas, {"kind": "unknown", "text": "Flugelhorn", "style": "italic"}, False, 2, sub)
    build_direction(meas, {"kind": "expression", "text": "dolce", "style": "italic"}, True, None, sub)
    d1, d2, d3 = meas.findall("direction")
    assert d1.get("placement") == "above" and d1.find("sound").get("tempo") == "103"
    assert d1.find("direction-type/words").text == "Sl. Faster"
    assert d1.find("direction-type/words").get("font-weight") == "bold"
    assert d1.find("direction-type/metronome").get("parentheses") == "yes"
    assert d1.findtext("direction-type/metronome/beat-unit") == "quarter"
    assert d1.find("direction-type/metronome/beat-unit-dot") is not None
    assert d1.findtext("direction-type/metronome/per-minute") == "103"
    assert [c.tag for c in d1][-1] == "sound"
    w = d2.find("direction-type/words")
    assert w.text == "check: Flugelhorn" and w.get("color") == "#7B1FA2" and d2.findtext("staff") == "2"
    assert d3.get("placement") == "below" and d3.find("direction-type/words").get("font-style") == "italic"


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def test_text_layer_config_defaults_and_validation():
    cfg = CopisteriaConfig.from_dict({"text_layer": {"mode": "on", "above_sp": 7, "lexicon": ["x.toml"]}})
    assert cfg.text_layer.mode == "on" and cfg.text_layer.above_sp == 7.0 and cfg.text_layer.lexicon == ("x.toml",)
    assert CopisteriaConfig.from_dict({}).text_layer == TextLayerConfig()
    assert TextLayerConfig().mode == "flag"
    try:
        TextLayerConfig.from_dict({"mode": "bogus"})
    except ConfigError:
        pass
    else:
        raise AssertionError("bad mode accepted")


def test_tempo_mark_in_the_gap_between_systems_belongs_to_the_next_staff():
    # printed 1.4 sp under staff 1's bottom and 4.6 sp over staff 2's top: the nearer staff is staff 1, but a tempo mark sits above: above staff 2, not below staff 1
    s = [Staff(1000, 1104, 30), Staff(1300, 1404, 30)]
    res = build_items([W("(J=124)", 400, 1140, 560, 1180)], s)
    assert [(i["kind"], i["bpm"]) for i in res.items] == [("tempo", 124)]
    assert res.items[0]["above"] is True and res.dropped == []
    # place_items then puts it over staff 2 (a hint the copisteria-side staves honour)
    geo = [{"y0": 1000, "y1": 1104, "sp": 26, "bars": [(40, 1800)]}, {"y0": 1300, "y1": 1404, "sp": 26, "bars": [(40, 1800)]}]
    assert list(place_items(res.items, geo)) == [(1, 0)]


def test_tempo_with_unreadable_words_beside_it_is_dropped():
    res = build_items([W("mf-", 300, 1340), W("199", 345, 1340), W("J=200", 395, 1340)], STAVES)
    assert res.items == [] and res.dropped[0]["why"].startswith("words beside")


def test_music_font_beat_glyph_gives_unit_and_dot_from_the_pdf_text():
    res = build_items([W("q.=54", 300, 1340, source="pdf")], STAVES)
    assert (res.items[0]["beat_unit"], res.items[0]["dotted"], res.items[0]["bpm"]) == ("quarter", True, 54)
    res = build_items([W("e=120", 300, 1340, source="pdf")], STAVES)
    assert res.items[0]["beat_unit"] == "eighth" and "dotted" not in res.items[0]
