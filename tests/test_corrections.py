import json

from musicocr import corrections
from musicocr.scoreops.layout import read_mscz
from tests.test_review import BREAK, make_mscz, measure, note


def bar(*pitches, extra=""):
    return measure(*[note(p) for p in pitches], extra=extra)


A, B, C, D = bar(60, 62, 64, 65), bar(67, 67, 67, 67), bar(69, 71, 72, 74), bar(76, 76, 76, 76)
FIXED = bar(67, 69, 67, 67)


def read(path):
    return read_mscz(path)[0]


def test_correction_is_put_back_after_a_rerun_even_if_bars_moved(tmp_path):
    base, now, store = tmp_path / "base.mscz", tmp_path / "now.mscz", tmp_path / "store.json"
    make_mscz(base, [A, B, C, D])
    make_mscz(now, [A, FIXED, C, D])
    assert corrections.capture(base, now, store)["new"] == 1

    rerun = tmp_path / "rerun.mscz"          # an extra bar in front, and a line break on the wrong bar
    make_mscz(rerun, [D, A, bar(67, 67, 67, 67, extra=BREAK), C, D])
    result = corrections.apply(store, rerun)
    assert result["applied"] == ["staff 1 bar 2 (now bar 3)"] and not result["skipped"]
    assert "<pitch>69</pitch>" in read(rerun) and BREAK in read(rerun)
    assert (tmp_path / "rerun.before-corrections.mscz").exists()
    assert corrections.apply(store, rerun)["already"] == ["staff 1 bar 2"]


def test_a_bar_that_reads_differently_is_reported_not_overwritten(tmp_path):
    base, now, store = tmp_path / "base.mscz", tmp_path / "now.mscz", tmp_path / "store.json"
    make_mscz(base, [A, B, C])
    make_mscz(now, [A, FIXED, C])
    corrections.capture(base, now, store)
    rerun = tmp_path / "rerun.mscz"
    make_mscz(rerun, [A, D, C])
    before = read(rerun)
    assert corrections.apply(store, rerun)["skipped"] == ["staff 1 bar 2: the bar now reads differently"]
    assert read(rerun) == before


def test_recapture_updates_and_a_reverted_fix_is_dropped(tmp_path):
    base, now, store = tmp_path / "base.mscz", tmp_path / "now.mscz", tmp_path / "store.json"
    make_mscz(base, [A, B, C])
    make_mscz(now, [A, FIXED, C])
    corrections.capture(base, now, store)
    make_mscz(now, [A, D, C])
    assert corrections.capture(base, now, store)["stored"] == 1
    assert "<pitch>76</pitch>" in json.loads(store.read_text())["corrections"][0]["after"][0]
    make_mscz(now, [A, B, C])
    assert corrections.capture(base, now, store) == {"differences": 0, "new": 0, "dropped": 1, "stored": 0}


def test_added_bars_are_recorded_but_not_applied(tmp_path):
    base, now, store = tmp_path / "base.mscz", tmp_path / "now.mscz", tmp_path / "store.json"
    make_mscz(base, [A, C])
    make_mscz(now, [A, B, C])
    corrections.capture(base, now, store)
    assert "by hand" in corrections.apply(store, base)["skipped"][0]


def test_restyled_text_is_not_a_correction(tmp_path):
    text = "<StaffText><text>Var. 1</text></StaffText>"
    styled = "<StaffText><size>14</size><bold>1</bold><text><b/>Var. 1</text></StaffText>"
    make_mscz(tmp_path / "a.mscz", [bar(60, 62, 64, 65, extra=text)])
    make_mscz(tmp_path / "b.mscz", [bar(60, 62, 64, 65, extra=styled)])
    assert corrections.capture(tmp_path / "a.mscz", tmp_path / "b.mscz", tmp_path / "s.json")["differences"] == 0
