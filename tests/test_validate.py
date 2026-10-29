"""Tests for musicocr.stages.validate finding codes, using tiny synthetic scores."""
from __future__ import annotations

from dataclasses import replace

from musicocr.config import load_config
from musicocr.pipeline import PipelineContext
from musicocr.stages import validate


def _note(step="C", dur=4, typ="whole", alter=0, extra=""):
    acc = f"<alter>{alter}</alter>" if alter else ""
    return (f"<note><pitch><step>{step}</step>{acc}<octave>4</octave></pitch>"
            f"<duration>{dur}</duration><type>{typ}</type>{extra}</note>")


def _attrs(clef=True, time=True, key=None):
    return ("<attributes><divisions>1</divisions>"
            + (f"<key><fifths>{key}</fifths></key>" if key is not None else "")
            + ("<time><beats>4</beats><beat-type>4</beat-type></time>" if time else "")
            + ("<clef><sign>G</sign><line>2</line></clef>" if clef else "")
            + "</attributes>")


def _measure(n, body, attrs=""):
    return f'<measure number="{n}">{attrs}{body}</measure>'


def _part(pid, measures, name="Voice"):
    return (f'<score-part id="{pid}"><part-name>{name}</part-name></score-part>',
            f'<part id="{pid}">{"".join(measures)}</part>')


def _score(*parts):
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1">'
            "<part-list>" + "".join(p[0] for p in parts) + "</part-list>"
            + "".join(p[1] for p in parts) + "</score-partwise>")


def _run(tmp_path, xml, engine="audiveris"):
    cfg = load_config()
    cfg = replace(cfg, omr=replace(cfg.omr, engine=engine))
    ctx = PipelineContext(input_pdf=tmp_path / "x.pdf", workdir=tmp_path, config=cfg,
                          book_name="book", log=lambda s: None)
    (tmp_path / "book.musicxml").write_text(xml)
    res = validate.run(ctx)
    return res, ctx.artifacts.validation


def _codes(val):
    return [f["code"] for f in val["findings"]]


def _by_code(val, code):
    return [f for f in val["findings"] if f["code"] == code]


def test_clean_score_has_no_findings(tmp_path):
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(key=0)),
                              _measure(2, _note())]))
    res, val = _run(tmp_path, xml)
    assert res.status == "ok" and res.detail == "0 error, 0 warn, 0 info"
    assert val["findings"] == []
    assert val["stats"]["parts"] == 1
    assert val["stats"]["measures_per_part"] == [2]
    assert val["stats"]["notes"] == 2
    assert val["counts"] == {"error": 0, "warn": 0, "info": 0}


def test_missing_time_signature_flagged(tmp_path):
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(time=False, key=0))]))
    _, val = _run(tmp_path, xml)
    assert "no-time" in _codes(val)


def test_missing_clef_flagged_or_defaulted(tmp_path):
    # music21 may synthesise a default clef on import; either way run() must not crash,
    # and the no-clef check itself is exercised directly below.
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(clef=False, key=0))]))
    _, val = _run(tmp_path, xml)
    assert set(_codes(val)) <= {"no-clef"}


def test_check_part_no_clef_direct():
    from music21 import meter, stream, note
    from musicocr.stages.validate import _check_part
    p = stream.Part()
    m = stream.Measure(number=1)
    m.append(meter.TimeSignature("4/4"))
    m.append(note.Note("C4", quarterLength=4))
    p.append(m)
    findings = []
    _check_part(p, 0, findings)
    assert [f["code"] for f in findings] == ["no-clef"]


def test_no_key_only_flagged_with_accidentals(tmp_path):
    with_acc = _score(_part("P1", [_measure(1, _note(alter=1), _attrs())]))
    _, val = _run(tmp_path, with_acc)
    f = _by_code(val, "no-key")
    assert len(f) == 1 and "1 accidental" in f[0]["message"]

    plain = _score(_part("P1", [_measure(1, _note(), _attrs())]))
    _, val = _run(tmp_path, plain)
    assert "no-key" not in _codes(val)


def test_overfull_measure_is_error(tmp_path):
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(key=0)),
                              _measure(2, _note() + _note(dur=1, typ="quarter")),
                              _measure(3, _note())]))
    _, val = _run(tmp_path, xml)
    f = _by_code(val, "measure-duration")
    assert len(f) == 1
    assert f[0]["severity"] == "error" and f[0]["measure"] == 2
    assert val["counts"]["error"] == 1


def test_underfull_middle_measure_is_warn(tmp_path):
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(key=0)),
                              _measure(2, _note(dur=2, typ="half")),
                              _measure(3, _note())]))
    _, val = _run(tmp_path, xml)
    f = _by_code(val, "measure-duration")
    assert [(x["severity"], x["measure"]) for x in f] == [("warn", 2)]


def test_pickup_and_short_final(tmp_path):
    xml = _score(_part("P1", [_measure(1, _note(dur=1, typ="quarter"), _attrs(key=0)),
                              _measure(2, _note()),
                              _measure(3, _note(dur=2, typ="half"))]))
    _, val = _run(tmp_path, xml)
    # leading pickup is tolerated; short last measure is info only
    assert _codes(val) == ["short-final"]
    assert val["findings"][0]["severity"] == "info"
    assert val["counts"]["info"] == 1


def test_measure_count_mismatch(tmp_path):
    a = _part("P1", [_measure(1, _note(), _attrs(key=0)), _measure(2, _note())], "A")
    b = _part("P2", [_measure(1, _note(), _attrs(key=0))], "B")
    _, val = _run(tmp_path, _score(a, b))
    f = _by_code(val, "measure-count-mismatch")
    assert len(f) == 1 and "[2, 1]" in f[0]["message"]
    assert val["stats"]["parts"] == 2


def test_no_notes_is_error_and_empty_rests_counted(tmp_path):
    rest = "<note><rest/><duration>4</duration><type>whole</type></note>"
    xml = _score(_part("P1", [_measure(1, rest, _attrs(key=0))]))
    _, val = _run(tmp_path, xml)
    assert "no-notes" in _codes(val)
    assert val["stats"]["rests"] == 1
    assert val["counts"]["error"] == 1


def test_empty_part_flagged(tmp_path):
    a = _part("P1", [_measure(1, _note(), _attrs(key=0))], "A")
    b = _part("P2", [], "Empty")
    _, val = _run(tmp_path, _score(a, b))
    assert "empty-part" in _codes(val)


def test_many_accidentals(tmp_path):
    xml = _score(_part("P1", [_measure(1, "".join(
        _note(alter=1, dur=1, typ="quarter") for _ in range(4)), _attrs(key=0))]))
    _, val = _run(tmp_path, xml)
    assert "many-accidentals" in _codes(val)
    assert val["stats"]["accidentals"] == 4


def test_many_tuplets(tmp_path):
    trip = ("<time-modification><actual-notes>3</actual-notes>"
            "<normal-notes>2</normal-notes></time-modification>")
    notes = "".join(_note(dur=1, typ="quarter", extra=trip) for _ in range(3)) \
        + _note(dur=1, typ="quarter")
    # 3 triplet quarters (2 beats) + 1 quarter = 3 beats; pad to a full bar
    notes += "<note><rest/><duration>1</duration><type>quarter</type></note>"
    xml = _score(_part("P1", [_measure(1, notes, _attrs(key=0))]))
    _, val = _run(tmp_path, xml)
    assert val["stats"]["tuplets"] == 3
    assert "many-tuplets" in _codes(val)


def test_homr_engine_runs_whole_rest_heuristic(tmp_path, monkeypatch):
    import musicocr.engines.homr.fixes as hf
    monkeypatch.setattr(hf, "suspicious_whole_rests", lambda p: ["m.2 maybe multirest"])
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(key=0))]))
    _, val = _run(tmp_path, xml, engine="homr")
    f = _by_code(val, "untagged-whole-rest")
    assert [(x["severity"], x["message"]) for x in f] == [("info", "m.2 maybe multirest")]

    _, val = _run(tmp_path, xml, engine="audiveris")
    assert "untagged-whole-rest" not in _codes(val)


def test_homr_heuristic_failure_is_logged_not_fatal(tmp_path, monkeypatch):
    import musicocr.engines.homr.fixes as hf

    def boom(p):
        raise RuntimeError("nope")
    monkeypatch.setattr(hf, "suspicious_whole_rests", boom)
    xml = _score(_part("P1", [_measure(1, _note(), _attrs(key=0))]))
    res, val = _run(tmp_path, xml, engine="homr")
    assert res.status == "ok" and val["findings"] == []
