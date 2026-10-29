"""Tests for the Copisteria pre/post-processing helpers (no engine needed)."""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from musicocr.text import page_text
from musicocr.engines.copisteria.fixes import fill_empty_measures
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
    from musicocr.text import ocr_backends
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
    from musicocr.text import ocr_backends
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


# ---- pickup + gap reveal ---------------------------------------------------

def test_fix_opening_pickup(tmp_path):
    from musicocr.engines.copisteria.fixes import fix_opening_pickup
    xml = tmp_path / "p.musicxml"
    xml.write_text("""<score-partwise><part id="P1">
<measure number="1"><attributes><time print-object="no"><beats>4</beats><beat-type>4</beat-type></time></attributes>
<note><rest/><duration>4</duration></note>
<note print-object="no"><rest/><duration>12</duration></note></measure>
<measure number="2"><note><pitch><step>C</step><octave>4</octave></pitch><duration>16</duration></note></measure>
</part></score-partwise>""")
    assert fix_opening_pickup(xml, log=lambda s: None) == 2
    m1 = ET.parse(xml).getroot().find("part/measure")
    assert [n.find("duration").text for n in m1.findall("note")] == ["4"]
    assert m1.find("attributes/time").get("print-object") is None
    assert fix_opening_pickup(xml, log=lambda s: None) == 0


def test_pickup_keeps_bar_with_nothing_real(tmp_path):
    from musicocr.engines.copisteria.fixes import fix_opening_pickup
    xml = tmp_path / "p.musicxml"
    xml.write_text("""<score-partwise><part id="P1"><measure number="1">
<note print-object="no"><rest/><duration>16</duration></note></measure></part></score-partwise>""")
    assert fix_opening_pickup(xml, log=lambda s: None) == 0
    assert len(ET.parse(xml).getroot().findall("part/measure/note")) == 1


def test_reveal_gaps(tmp_path):
    from musicocr.engines.copisteria.fixes import GAP_COLOR, reveal_gaps
    xml = tmp_path / "p.musicxml"
    xml.write_text("""<score-partwise><part id="P1">
<measure number="1"><note><pitch><step>C</step><octave>4</octave></pitch><duration>6</duration><voice>1</voice></note>
<forward><duration>2</duration><voice>1</voice></forward>
<note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note></measure>
<measure number="2"><note print-object="no" print-spacing="yes"><rest/><duration>4</duration></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>12</duration></note></measure>
<measure number="3"><forward><duration>16</duration></forward></measure>
<measure number="4"><note><pitch><step>C</step><octave>4</octave></pitch><duration>8</duration></note><backup><duration>8</duration></backup>
<forward><duration>4</duration></forward></measure>
</part></score-partwise>""")
    assert reveal_gaps(xml, log=lambda s: None) == 2
    ms = ET.parse(xml).getroot().findall("part/measure")
    rest1 = ms[0].findall("note")[1]
    assert rest1.find("rest") is not None and rest1.get("color") == GAP_COLOR
    assert rest1.find("duration").text == "2" and rest1.find("voice").text == "1"
    assert ms[0].find("forward") is None
    hidden = ms[1].find("note")
    assert hidden.get("print-object") is None and hidden.get("color") == GAP_COLOR
    assert ms[2].find("forward") is not None        # empty bar untouched
    assert ms[3].find("forward") is not None        # multi-voice bar untouched


# ---- second pass -----------------------------------------------------------

def _xml(tmp_path, name, bars):
    p = tmp_path / name
    body = "".join(f'<measure number="{i + 1}">{b}</measure>' for i, b in enumerate(bars))
    p.write_text(f'<score-partwise><part id="P1">{body}</part></score-partwise>')
    return p


NOTE = "<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>"
GAP = "<forward><duration>4</duration></forward>"


def test_merge_second_pass_replaces_only_filled_gap_bars(tmp_path):
    from musicocr.engines.copisteria.second_pass import gap_bars, merge_second_pass
    first = _xml(tmp_path, "a.xml", [NOTE + NOTE, NOTE + GAP + NOTE, NOTE + GAP + NOTE])
    # bar 2 filled; bar 3 still has a gap; bar 1 differs but is not a gap bar (ignored)
    second = _xml(tmp_path, "b.xml", [NOTE, NOTE + NOTE + NOTE, NOTE + GAP + NOTE])
    out = tmp_path / "m.xml"
    n, why = merge_second_pass(first, second, out)
    assert n == 1 and "1 of 2" in why
    assert gap_bars(out) == [2]                       # bar 2 now whole, bar 3 still a gap
    bars = ET.parse(out).getroot().findall("part/measure")
    assert [len(b.findall("note")) for b in bars] == [2, 3, 2]   # bar 1 kept from the first reading
    assert [b.get("number") for b in bars] == ["1", "2", "3"]


def test_merge_second_pass_guards(tmp_path):
    from musicocr.engines.copisteria.second_pass import merge_second_pass
    out = tmp_path / "m.xml"
    first = _xml(tmp_path, "a.xml", [NOTE + GAP + NOTE, NOTE])
    assert merge_second_pass(first, _xml(tmp_path, "b.xml", [NOTE]), out)[0] == 0      # bar count
    assert merge_second_pass(first, _xml(tmp_path, "c.xml", [NOTE + GAP + NOTE, NOTE]), out)[0] == 0
    assert not out.exists()
    d = _xml(tmp_path, "d.xml", [NOTE + NOTE + NOTE, NOTE])
    d.write_text(d.read_text().replace("<measure number=\"1\">",
                 '<measure number="1"><attributes><key><fifths>1</fifths></key></attributes>', 1))
    assert merge_second_pass(first, d, out)[0] == 0                                   # attributes differ


def test_union_dets_adds_only_nonoverlapping_notes_and_rests():
    from musicocr.engines.copisteria.detect import union_dets
    box = lambda x: [x, 0, x + 10, 10]  # noqa: E731
    base = [{"cls": "note_eighth", "xyxy": box(0)}]
    big = [{"cls": "note_eighth", "xyxy": box(1)},        # overlaps a base note: skipped
           {"cls": "note_eighth", "xyxy": box(50)},       # new note: added
           {"cls": "rest_quarter", "xyxy": box(0)},       # different family: added
           {"cls": "measure", "xyxy": box(100)}]          # not note/rest: ignored
    got = union_dets(base, big)
    assert [r["cls"] for r in got] == ["note_eighth", "note_eighth", "rest_quarter"]


# ---- half-read tuplets -----------------------------------------------------

def _n(dur, tm=False, beam="", typ="eighth"):
    t = ("<time-modification><actual-notes>2</actual-notes><normal-notes>3</normal-notes>"
         "</time-modification>") if tm else ""
    b = f'<beam number="1">{beam}</beam>' if beam else ""
    return (f"<note><pitch><step>A</step><octave>5</octave></pitch><duration>{dur}</duration>"
            f"<voice>1</voice><type>{typ}</type>{t}<stem>down</stem>{b}</note>")


def test_fix_half_tuplets_completes_duplet_and_leaves_others(tmp_path):
    from musicocr.engines.copisteria.fixes import fix_half_tuplets
    attrs = ("<attributes><divisions>4</divisions><time><beats>6</beats><beat-type>8</beat-type>"
             "</time></attributes>")
    gap1 = "<note print-object=\"no\"><rest/><duration>1</duration></note>"
    # 6 + duplet(3) + half-read duplet(2) + gap(1) = 12: finished -> 6 + 3 + 3
    ok = attrs + _n(6, typ="quarter") + _n(3, True, "begin") + _n(2, False, "end") + gap1
    # the notes would still not add up to the bar after scaling -> untouched
    no = _n(4, typ="quarter") + _n(3, True, "begin") + _n(2, False, "end") + gap1
    p = _xml(tmp_path, "t.xml", [ok, no])
    assert fix_half_tuplets(p, log=lambda s: None) == 1
    bars = ET.parse(p).getroot().findall("part/measure")
    assert [n.findtext("duration") for n in bars[0].findall("note")] == ["6", "3", "3"]
    assert bars[0].findall("note")[2].find("time-modification") is not None
    assert len(bars[1].findall("note")) == 4


def _det(cls, x0, y0, x1, y1, conf=0.99, grace="<na>", acc=0.0):
    return {"cls": cls, "conf": conf, "xyxy": [x0, y0, x1, y1],
            "attrs": {"grace": grace}, "attr_p": {"grace": 1.0},
            "attr_dist": {"grace": {grace: 1.0 - acc, "acc": acc} if acc else {grace: 1.0}}}


def test_mark_small_graces_sets_grace_on_clearly_small_notes(tmp_path):
    import json

    from musicocr.text.page_text import mark_small_graces
    normal = [_det("note_quarter", 100 * i, 0, 100 * i + 34, 26) for i in range(8)]
    small = _det("note_eighth", 900, 0, 922, 17, acc=0.05)                  # 22x17: a grace the detector missed
    medium = _det("note_eighth", 1000, 0, 1033, 26)                         # ordinary
    p = tmp_path / "p001.dets_v7_1600.json"
    p.write_text(json.dumps(normal + [small, medium]))
    assert mark_small_graces(p) == 1
    out = json.loads(p.read_text())
    assert out[-2]["attrs"]["grace"] == "acc" and out[-2]["attr_dist"]["grace"]["acc"] > 0.9
    assert out[-1]["attrs"]["grace"] == "<na>" and out[0]["attrs"]["grace"] == "<na>"


def test_mark_small_graces_needs_a_reference(tmp_path):
    import json

    from musicocr.text.page_text import mark_small_graces
    p = tmp_path / "p001.dets_v7_1600.json"
    p.write_text(json.dumps([_det("note_eighth", 0, 0, 22, 17), _det("note_quarter", 50, 0, 84, 26)]))
    assert mark_small_graces(p) == 0


def test_union_into_empty_only_fills_measure_boxes_that_hold_nothing():
    from musicocr.engines.copisteria.detect import empty_measures, union_into_empty
    box = lambda cls, x0, x1, conf=0.99: {"cls": cls, "conf": conf, "xyxy": [x0, 0, x1, 50]}  # noqa: E731
    base = [box("measure", 0, 100), box("note_quarter", 40, 60),            # a bar with a note
            box("measure", 100, 200),                                        # empty: its whole rest was missed
            box("measure", 200, 300), box("multiRest", 220, 280)]            # a multi-rest bar
    assert [m["xyxy"] for m in empty_measures(base)] == [[100, 0, 200, 50]]
    big = [box("mRest_measure", 140, 160), box("mRest_measure", 20, 30),     # second is in a bar with a note
           box("note_half", 240, 260), box("mRest_measure", 110, 120, conf=0.3)]
    got = union_into_empty(base, big)
    assert [r["cls"] for r in got[len(base):]] == ["mRest_measure"]
    assert got[len(base)]["xyxy"][0] == 140
    assert union_into_empty([box("measure", 0, 100), box("note_quarter", 40, 60)], big)[2:] == []


def test_key_reset_skips_multirest_follower_bars(tmp_path):
    import json
    from musicocr.engines.copisteria.fixes import fix_missing_key_cancel
    rest = '<note><rest measure="yes"/><duration>12</duration></note>'
    xml = tmp_path / "p.musicxml"
    xml.write_text(f"""<score-partwise><part id="P1">
<measure number="1"><attributes><key><fifths>3</fifths></key>
<measure-style><multiple-rest>3</multiple-rest></measure-style></attributes>{rest}</measure>
<measure number="2">{rest}</measure><measure number="3">{rest}</measure>
<measure number="4"><note><pitch><step>C</step><alter>1</alter><octave>5</octave></pitch><duration>12</duration></note></measure>
</part></score-partwise>""")
    # two printed bars: the multi-rest, then a bar opening a staff with a clef but no key
    tok = lambda key: {"cls": {"v": "measure"}, "clef": {"v": "G2"}, "key": {"v": key, "p": 1.0}}  # noqa: E731
    rj = tmp_path / "p.reading.json"
    rj.write_text(json.dumps({"tokens": {"1": tok(3), "2": tok(None)}}))
    assert fix_missing_key_cancel(xml, rj, log=lambda s: None) == 1
    ms = ET.parse(xml).getroot().findall("part/measure")
    assert [m.find("attributes/key") is not None for m in ms] == [True, False, False, True]
    assert ms[3].find("attributes/key/fifths").text == "0"
    assert ms[3].find("note/pitch/alter") is None         # the key's C# no longer applies


# ---- combine ---------------------------------------------------------------

def _meas(*durs, attrs=""):
    notes = "".join(f"<note><pitch><step>C</step><octave>4</octave></pitch><duration>{d}</duration></note>"
                    for d in durs)
    return ET.fromstring(f"<measure number='1'>{attrs}{notes}</measure>")


def test_split_overlong_splits_at_bar_lines_only():
    from musicocr.assemble.combine import split_overlong
    two = _meas(6, 6, 6, 6, attrs="<attributes><key><fifths>1</fifths></key></attributes>")   # 2 bars of 6/8 at div 4: 12 each
    got = split_overlong(two, 4, (6, 8))
    assert got is not None and len(got) == 2
    assert got[0].find("attributes") is not None and got[1].find("attributes") is None
    assert [n.findtext("duration") for n in got[1].findall("note")] == ["6", "6"]
    assert split_overlong(_meas(6, 6), 4, (6, 8)) is None             # a single bar
    assert split_overlong(_meas(8, 8, 8), 4, (6, 8)) is None          # a note straddles the bar line


def test_align_to_target_uses_only_the_needed_indels():
    from musicocr.assemble.combine import align_to_target
    src = [0, 1, 1, 0, 1]
    slots = align_to_target(src, [0, 1, 0, 1])         # one bar too many -> exactly one deletion
    assert len([s for s in slots if s is not None]) == 4 and slots == sorted(slots)
    assert slots.count(None) == 0
    slots = align_to_target([0, 1, 0], [0, 1, 1, 0])   # one bar short -> exactly one inserted slot
    assert slots.count(None) == 1 and [s for s in slots if s is not None] == [0, 1, 2]


def test_combine_cover_page_and_total_lower_bound(tmp_path):
    from musicocr.assemble.combine import PartInput, combine
    rest = '<note><rest measure="yes"/><duration>12</duration></note>'
    def page(n, first=False):
        attrs = ("<attributes><divisions>4</divisions><key><fifths>0</fifths></key>"
                 "<time><beats>3</beats><beat-type>4</beat-type></time></attributes>") if first else ""
        body = "".join(f'<measure number="{i + 1}">{attrs if i == 0 else ""}{rest}</measure>' for i in range(n))
        return f'<score-partwise><part id="P1">{body}</part></score-partwise>'
    parts = []
    for pid in ("a", "b"):
        files = []
        for k, (n, first) in enumerate([(2, False), (4, True), (3, False)], 1):   # p1 = junk on a cover page
            f = tmp_path / f"{pid}.p{k:03d}.musicxml"
            f.write_text(page(n, first))
            files.append(f)
        parts.append(PartInput(pid, f"Horn {pid}", "horn in f", tmp_path / f"{pid}.pdf", files))
        (tmp_path / f"{pid}.pdf").write_bytes(b"x")
    score, report, summary = combine(
        parts, "T", starts_of=lambda pdf: [None, 1, 5], last_start_of=lambda pdf: 7,
        spans_of=lambda pdf: [])
    # cover page ignored; bars 1-4 on page 2, bars 5.. on page 3 (3 bars -> total 7)
    assert summary["total"] == 7
    assert any("hold no printed bars" in r for r in report)
    assert any("ignored" in r for r in report)
    assert len(score.findall("part")[0].findall("measure")) == 7
    # written pitch stays; the horn part declares its transposition (written -> concert)
    tr = score.find("part/measure/attributes/transpose")
    assert score.findtext("part-list/score-part/score-instrument/instrument-sound") == "brass.french-horn"
    assert tr is not None and tr.findtext("chromatic") == "-7" and tr.findtext("diatonic") == "-4"


def test_combine_shares_the_concert_key_across_transposing_parts(tmp_path):
    from musicocr.assemble.combine import PartInput, combine
    rest = '<note><rest measure="yes"/><duration>12</duration></note>'
    parts = []
    for pid, label, inst, fifths in (("h", "Horn", "horn in f", 2), ("t", "Tuba", "tuba", 1)):
        attrs = (f"<attributes><divisions>4</divisions><key><fifths>{fifths}</fifths></key>"
                 "<time><beats>3</beats><beat-type>4</beat-type></time></attributes>")
        f = tmp_path / f"{pid}.p001.musicxml"
        f.write_text('<score-partwise><part id="P1">' + "".join(
            f'<measure number="{i + 1}">{attrs if i == 0 else ""}{rest}</measure>' for i in range(2))
            + "</part></score-partwise>")
        (tmp_path / f"{pid}.pdf").write_bytes(b"x")
        parts.append(PartInput(pid, label, inst, tmp_path / f"{pid}.pdf", [f]))
    score, report, _ = combine(parts, "T", starts_of=lambda pdf: [1], last_start_of=lambda pdf: 2,
                               spans_of=lambda pdf: [])
    keys = [p.findtext("measure/attributes/key/fifths") for p in score.findall("part")]
    assert keys == ["2", "1"]                       # horn written D major, tuba (concert) G major
    assert any("concert pitch" in r and "+1" in r for r in report)


def test_combine_gives_unpitched_percussion_no_key_signature(tmp_path):
    from musicocr.assemble.combine import PartInput, combine
    rest = '<note><rest measure="yes"/><duration>12</duration></note>'
    parts = []
    for pid, label, inst, clef, fifths in (("h", "Horn", "horn in f", "G", 2), ("t", "Tuba", "tuba", "F", 2),
                                           ("p", "Perkussion 1", "", "percussion", 0)):
        attrs = (f"<attributes><divisions>4</divisions><key><fifths>{fifths}</fifths></key>"
                 f"<time><beats>3</beats><beat-type>4</beat-type></time><clef><sign>{clef}</sign></clef></attributes>")
        f = tmp_path / f"{pid}.p001.musicxml"
        f.write_text('<score-partwise><part id="P1">' + "".join(
            f'<measure number="{i + 1}">{attrs if i == 0 else ""}{rest}</measure>' for i in range(2))
            + "</part></score-partwise>")
        (tmp_path / f"{pid}.pdf").write_bytes(b"x")
        parts.append(PartInput(pid, label, inst, tmp_path / f"{pid}.pdf", [f]))
    score, _, _ = combine(parts, "T", starts_of=lambda pdf: [1], last_start_of=lambda pdf: 2,
                          spans_of=lambda pdf: [])
    perc = score.findall("part")[2]
    assert [k.text for k in perc.findall("measure/attributes/key/fifths")] == ["0"]   # one C-major key, never changed


def test_add_missing_multirest_dets_guards(tmp_path):
    import json
    from musicocr.text.page_text import add_missing_multirest_dets
    meas = lambda x0, x1: {"cls": "measure", "conf": 0.99, "xyxy": [x0, 100, x1, 160]}  # noqa: E731
    base = [meas(0, 1000), meas(1000, 1400)] + [  # staff space ~12 px (from the 60 px measures)
        {"cls": "note_quarter", "conf": 0.99, "xyxy": [1100, 120, 1112, 140]}]          # bar 2 has a note
    texts = [
        {"text": "8", "xyxy": [490, 20, 520, 100]},       # centred over empty bar 1: a count
        {"text": "4", "xyxy": [20, 20, 50, 100]},         # near bar 1's left edge: a time signature
        {"text": "3", "xyxy": [1190, 20, 1220, 100]},     # over bar 2, which holds a note
    ]
    d, t = tmp_path / "p.dets_v7_1600.json", tmp_path / "p.texts.json"
    d.write_text(json.dumps(base))
    t.write_text(json.dumps(texts))
    assert add_missing_multirest_dets(d, t) == 1
    added = [x for x in json.loads(d.read_text()) if x["cls"] == "multiRest"]
    assert len(added) == 1 and added[0]["xyxy"][0] < 30 and added[0]["xyxy"][2] > 970
    # stacked pair (4 over 4) centred on a bar is a time signature, not a count
    d.write_text(json.dumps(base))
    t.write_text(json.dumps([{"text": "4", "xyxy": [490, 20, 520, 100]}, {"text": "4", "xyxy": [490, 60, 520, 140]}]))
    assert add_missing_multirest_dets(d, t) == 0


def test_low_confidence_breve_under_count_digit_becomes_multirest(tmp_path):
    import json
    from musicocr.text.page_text import add_missing_multirest_dets
    dets = [{"cls": "measure", "conf": 0.99, "xyxy": [1190, 100, 1700, 202]},          # sp ~25
            {"cls": "clef_G2", "conf": 1.0, "xyxy": [50, 90, 120, 280]},
            {"cls": "meterSig_4_4", "conf": 0.99, "xyxy": [158, 100, 213, 200]},
            {"cls": "note_breve", "conf": 0.14, "xyxy": [675, 140, 734, 180]},
            {"cls": "barLine_single", "conf": 1.0, "xyxy": [1185, 100, 1198, 202]}]
    texts = [{"text": "6", "xyxy": [651, 0, 744, 118]}]                                 # 4.7 spaces tall
    d, t = tmp_path / "p.dets.json", tmp_path / "p.texts.json"
    d.write_text(json.dumps(dets)), t.write_text(json.dumps(texts))
    assert add_missing_multirest_dets(d, t) == 1
    out = json.loads(d.read_text())
    assert not [x for x in out if x["cls"] == "note_breve"]
    mr = [x for x in out if x["cls"] == "multiRest"]
    assert len(mr) == 1 and 213 < mr[0]["xyxy"][0] < 300 and 1100 < mr[0]["xyxy"][2] < 1185
    # no digit over it and a note sharing its bar: a real (if doubtful) breve stays
    d.write_text(json.dumps(dets + [{"cls": "note_quarter", "conf": 0.99, "xyxy": [900, 120, 912, 140]}])), t.write_text("[]")
    assert add_missing_multirest_dets(d, t) == 0


def test_lone_very_weak_breve_is_a_multirest_even_without_a_digit(tmp_path):
    import json
    from musicocr.text.page_text import add_missing_multirest_dets
    dets = [{"cls": "measure", "conf": 0.99, "xyxy": [0, 100, 1000, 202]},
            {"cls": "barLine_single", "conf": 1.0, "xyxy": [995, 100, 1005, 202]},
            {"cls": "note_breve", "conf": 0.21, "xyxy": [450, 140, 510, 180]}]
    d, t = tmp_path / "p.dets.json", tmp_path / "p.texts.json"
    d.write_text(json.dumps(dets)), t.write_text("[]")
    assert add_missing_multirest_dets(d, t) == 1
    # a breve with another note in its bar, or a firmer one, stays
    for extra, conf in (({"cls": "note_quarter", "conf": 0.99, "xyxy": [700, 120, 712, 140]}, 0.21), (None, 0.45)):
        dd = [dict(x) for x in dets]
        dd[2]["conf"] = conf
        if extra:
            dd.append(extra)
        d.write_text(json.dumps(dd))
        assert add_missing_multirest_dets(d, t) == 0


def test_repeat_marks_on_a_count_digit_are_dropped(tmp_path):
    import json
    from musicocr.text.page_text import drop_digit_repeat_marks
    dets = [{"cls": "measure", "conf": 0.99, "xyxy": [0, 100, 1000, 202]},                      # sp ~25
            {"cls": "repeatMark_segno", "conf": 0.43, "xyxy": [480, 0, 540, 90]},               # on the "5"
            {"cls": "repeatMark_segno", "conf": 0.95, "xyxy": [800, 0, 860, 90]}]               # a real one
    texts = [{"text": "5", "xyxy": [470, 0, 550, 95]}]
    d, t = tmp_path / "p.dets.json", tmp_path / "p.texts.json"
    d.write_text(json.dumps(dets)), t.write_text(json.dumps(texts))
    assert drop_digit_repeat_marks(d, t) == 1
    assert [x["xyxy"][0] for x in json.loads(d.read_text()) if x["cls"].startswith("repeat")] == [800]


def test_printed_multirest_span_regex():
    from musicocr.assemble.combine import _RANGE
    assert _RANGE.fullmatch("58–60") and _RANGE.fullmatch("98 — 101")
    assert not _RANGE.fullmatch("1-4")          # hyphen: not a multi-rest label


def test_align_inserts_after_the_first_bar_of_a_rest_run():
    from musicocr.assemble.combine import align_to_target
    # source: note bar, rest bar (carries the time-signature declaration); target: a 3-bar rest run
    slots = align_to_target([0, 1, 0], [0, 1, 1, 1, 0])
    assert slots[0] == 0 and slots[1] == 1 and slots[4] == 2      # the run keeps its first bar first
    assert slots.count(None) == 2


def test_align_insertions_restricted_to_printed_spans():
    from musicocr.assemble.combine import align_to_target
    src = [0, 0, 1, 0]                         # note, note, rest(=multi-rest read as one bar), note
    ref = [0.5] * 6
    free = align_to_target(src, ref)           # no hint: insertion lands wherever it is cheapest
    ins = [2.0, 2.0, 2.0, 0.01, 0.01, 2.0]     # only bars 4-5 (index 3,4) can have lost bars
    slots = align_to_target(src, ref, ins)
    assert [i for i, s in enumerate(slots) if s is None] and all(i in (3, 4) for i, s in enumerate(slots) if s is None)
    assert slots.count(None) == 2
    assert slots.count(None) == free.count(None)


def test_copy_marks_keeps_directions_and_drops_orphan_hairpins():
    import xml.etree.ElementTree as ET
    from musicocr.assemble.combine import balance_wedges, copy_marks
    src = ET.fromstring(
        '<measure number="1"><direction><direction-type><dynamics><p/></dynamics></direction-type></direction>'
        '<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>'
        '<direction><direction-type><wedge type="crescendo"/></direction-type></direction>'
        '<barline location="right"><bar-style>light-light</bar-style></barline></measure>')
    dst = ET.Element("measure")
    copy_marks(src, dst, 4)
    assert [c.tag for c in dst] == ["direction", "note", "direction", "barline"]
    assert balance_wedges([dst]) == 1                      # the crescendo never stops
    assert [c.tag for c in dst] == ["direction", "note", "barline"]


def test_trailing_courtesy_clef_takes_effect_next_bar():
    import xml.etree.ElementTree as ET
    from musicocr.assemble.combine import _clefs
    note = "<note><rest/><duration>4</duration></note>"
    bar = ET.fromstring(f'<measure>{note}<attributes><clef><sign>G</sign><line>2</line></clef></attributes></measure>')
    assert _clefs((bar, 4)) == (None, ("G", "2"))
    bar = ET.fromstring(f'<measure><attributes><clef><sign>F</sign><line>4</line></clef></attributes>{note}</measure>')
    assert _clefs((bar, 4)) == (("F", "4"), None)
