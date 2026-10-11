"""Tests for the Copisteria pre/post-processing helpers (no engine needed)."""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from musicocr import page_text
from musicocr.copisteria_fixes import fill_empty_measures
from musicocr.preprocess import PageGeometry
from musicocr.util import LenientResult


# ---- geometry --------------------------------------------------------------

def test_geometry_scale_and_crop():
    g = PageGeometry(dpi=144, src_size=(1000, 2000), crop_top=100)
    # 144 dpi = 2 px per point; y shifts up by the crop offset
    assert g.pdf_box_to_image((10, 60, 20, 70)) == (20.0, 20.0, 40.0, 40.0)


def test_geometry_rotation_about_centre():
    g = PageGeometry(dpi=72, src_size=(200, 200), rotate=90.0)
    # a point right of centre moves to above centre after a 90 deg CCW turn
    x0, y0, x1, y1 = g.pdf_box_to_image((150, 100, 150, 100))
    assert (round(x0), round(y0)) == (100, 50)


def test_geometry_roundtrip(tmp_path):
    png = tmp_path / "p001.png"
    PageGeometry(dpi=350, src_size=(10, 20), rotate=1.5, crop_top=7).save(png)
    assert PageGeometry.load(png) == PageGeometry(350, (10, 20), 1.5, 7)
    assert PageGeometry.load(tmp_path / "missing.png") is None


# ---- text layer ------------------------------------------------------------

BBOX = """<doc><page width="595" height="842">
<word xMin="10.0" yMin="20.0" xMax="20.0" yMax="30.0">5</word>
<word xMin="10.0" yMin="40.0" xMax="20.0" yMax="50.0">Horn</word>
<word xMin="10.0" yMin="60.0" xMax="20.0" yMax="70.0">1234</word>
<word xMin="10.0" yMin="80.0" xMax="20.0" yMax="90.0">13</word>
<word xMin="10.0" yMin="-9.0" xMax="20.0" yMax="-1.0">1</word>
</page></doc>"""


def test_digit_boxes_only(monkeypatch):
    monkeypatch.setattr(page_text, "run_cmd_lenient",
                        lambda cmd, **kw: LenientResult(0, BBOX, False))
    boxes = page_text.pdf_digit_boxes("pdftotext", Path("x.pdf"), 1)
    assert [b["text"] for b in boxes] == ["5", "13", "1"]


def test_write_texts_json_maps_and_drops_outside(tmp_path, monkeypatch):
    from PIL import Image
    png = tmp_path / "p001.png"
    Image.new("L", (400, 400), 255).save(png)
    PageGeometry(dpi=72, src_size=(400, 440), crop_top=40).save(png)
    monkeypatch.setattr(page_text, "run_cmd_lenient",
                        lambda cmd, **kw: LenientResult(0, BBOX, False))
    n = page_text.write_texts_json("pdftotext", Path("x.pdf"), 1, png)
    data = json.loads((tmp_path / "p001.texts.json").read_text())
    assert n == len(data) == 1                       # "5"/"13" fall in the cropped-away strip
    assert data[0]["text"] == "13" and data[0]["xyxy"][1] == 40.0


def test_write_texts_json_without_geometry_is_noop(tmp_path):
    png = tmp_path / "p001.png"
    png.write_bytes(b"x")
    stale = tmp_path / "p001.texts.json"
    stale.write_text("[]")
    assert page_text.write_texts_json("pdftotext", Path("x.pdf"), 1, png) == 0
    assert not stale.exists()


# ---- empty bar fix ---------------------------------------------------------

def test_fill_empty_measures(tmp_path):
    xml = tmp_path / "a.musicxml"
    xml.write_text("""<score-partwise><part id="P1">
<measure number="1"><note><rest/><duration>4</duration></note></measure>
<measure number="2"><forward><duration>12</duration></forward></measure>
</part></score-partwise>""")
    assert fill_empty_measures(xml, log=lambda s: None) == 1
    m2 = ET.parse(xml).getroot().findall("part/measure")[1]
    assert m2.find("forward") is None
    assert m2.find("note/rest").get("measure") == "yes"
    assert m2.find("note/duration").text == "12"
    assert fill_empty_measures(xml, log=lambda s: None) == 0   # idempotent


# ---- odd tuplets -----------------------------------------------------------

def test_suspicious_tuplets():
    from music21 import duration, note, stream
    from musicocr.stages.validate import suspicious_tuplet_measures

    def tup(ql_type, actual, normal):
        n = note.Note("C4")
        n.duration = duration.Duration(type=ql_type)
        n.duration.appendTuplet(duration.Tuplet(actual, normal, ql_type))
        return n

    part = stream.Part()
    ok, mixed, plain = stream.Measure(number=1), stream.Measure(number=2), stream.Measure(number=3)
    for _ in range(3):
        ok.append(tup("eighth", 3, 2))
    mixed.append(tup("eighth", 2, 3))
    mixed.append(tup("eighth", 3, 2))
    plain.append(note.Note("C4"))
    part.append([ok, mixed, plain])
    found = suspicious_tuplet_measures(part)
    assert [m for m, _ in found] == [2] and "mixed" in found[0][1]


def _dets_and_texts(tmp_path, digit_box, conf=0.65):
    dets = [{"cls": "measure", "conf": 0.99, "xyxy": [0, 2270, 1000, 2366]},
            {"cls": "multiRest", "conf": conf, "xyxy": [400, 2252, 900, 2332]},
            {"cls": "multiRest", "conf": 0.5, "xyxy": [5000, 100, 5500, 180]}]
    dp, tp = tmp_path / "p001.dets_v7_1600.json", tmp_path / "p001.texts.json"
    dp.write_text(json.dumps(dets))
    tp.write_text(json.dumps([{"text": "9", "xyxy": digit_box, "role": "words"}]))
    return dp, tp


def test_boost_multirest_dets_raises_only_covered(tmp_path):
    dp, tp = _dets_and_texts(tmp_path, [600, 2100, 640, 2313])   # tall font box; centre 2206
    assert page_text.boost_multirest_dets(dp, tp) == 1
    d = json.loads(dp.read_text())
    assert d[1]["conf"] == page_text.BOOSTED_CONF
    assert d[2]["conf"] == 0.5                                    # no digit over it


def test_boost_multirest_dets_ignores_distant_or_confident(tmp_path):
    dp, tp = _dets_and_texts(tmp_path, [600, 1000, 640, 1050])    # far above
    assert page_text.boost_multirest_dets(dp, tp) == 0
    dp, tp = _dets_and_texts(tmp_path, [600, 2100, 640, 2313], conf=0.995)
    assert page_text.boost_multirest_dets(dp, tp) == 0
    assert page_text.boost_multirest_dets(tmp_path / "missing.json", tp) == 0


# --- OCR page-text source ---------------------------------------------------------------

def test_digit_boxes_from_words_filters_noise():
    words = [{"text": "8", "xyxy": [10, 10, 40, 60]},
             {"text": "Allegro", "xyxy": [0, 0, 100, 30]},
             {"text": "64", "xyxy": [190, 1030, 667, 1231]},       # clef misread, far too wide
             {"text": "1234", "xyxy": [0, 0, 80, 50]}]
    got = page_text.digit_boxes_from_words(words)
    assert [b["text"] for b in got] == ["8"] and got[0]["role"] == "words"


def test_write_ocr_texts_json(tmp_path, monkeypatch):
    from musicocr import ocr_backends
    png = tmp_path / "p001.png"
    seen = {}

    def fake(p, backend="auto", log=None):
        seen["args"] = (p, backend)
        return [{"text": "5", "xyxy": [100, 200, 130, 250]}, {"text": "Tpt.", "xyxy": [0, 0, 9, 9]}]
    monkeypatch.setattr(ocr_backends, "ocr_words", fake)
    assert page_text.write_ocr_texts_json(png) == 1
    assert json.loads((tmp_path / "p001.texts.json").read_text()) == [
        {"text": "5", "xyxy": [100.0, 200.0, 130.0, 250.0], "role": "words"}]
    monkeypatch.setattr(ocr_backends, "ocr_words", lambda *a, **k: [])
    assert page_text.write_ocr_texts_json(png) == 0
    assert not (tmp_path / "p001.texts.json").exists()


def test_ocr_words_backend_selection(tmp_path):
    from musicocr import ocr_backends
    ocr_backends.BACKENDS["fake"] = lambda p, log=None, **o: [{"text": "3", "xyxy": [0, 0, 1, 2]}]
    ocr_backends.BACKENDS["broken"] = lambda p, log=None, **o: 1 / 0
    try:
        assert ocr_backends.ocr_words(tmp_path / "x.png", "fake")[0]["text"] == "3"
        assert ocr_backends.ocr_words(tmp_path / "x.png", "broken") == []
        assert ocr_backends.ocr_words(tmp_path / "x.png", "nonexistent") == []
        ocr_backends.BACKENDS["fake2"] = lambda p, log=None, **o: [
            {"text": "3", "xyxy": [0, 0, 1.1, 2]}, {"text": "7", "xyxy": [5, 5, 9, 9]}]
        got = ocr_backends.ocr_words(tmp_path / "x.png", "auto")      # union, de-duplicated
        assert sorted(w["text"] for w in got) == ["3", "7"]
    finally:
        for k in ("fake", "fake2", "broken"):
            ocr_backends.BACKENDS.pop(k, None)


def test_texts_config_values():
    from musicocr.config import ConfigError, CopisteriaConfig
    for v in ("pdf-layer", "ocr", "auto", "none"):
        assert CopisteriaConfig.from_dict({"texts": v}).texts == v
    with pytest.raises(ConfigError):
        CopisteriaConfig.from_dict({"texts": "tesseract"})
    with pytest.raises(ConfigError):
        CopisteriaConfig.from_dict({"ocr_backend": "tesseract"})


def test_pdf_has_text(monkeypatch):
    monkeypatch.setattr(page_text, "run_cmd_lenient",
                        lambda *a, **k: LenientResult(0, "  \n", False))
    assert not page_text.pdf_has_text("pdftotext", Path("x.pdf"), 1)
    monkeypatch.setattr(page_text, "run_cmd_lenient",
                        lambda *a, **k: LenientResult(0, "Horn in F\n", False))
    assert page_text.pdf_has_text("pdftotext", Path("x.pdf"), 1)
