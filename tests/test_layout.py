from pathlib import Path

import pytest

from musicocr.scoreops.layout import (LayoutError, PageStyle, Target, compare, fit, patch_mscx,
                                      patch_style, target_from_pages)

BAR = "<Measure>\n        <eid>e{}</eid>\n        <voice>\n          </voice>\n        </Measure>\n      "


def score(n, extra=""):
    bars = "".join(BAR.format(i) for i in range(n))
    return f'<Staff id="1">\n      {bars}{extra}</Staff>\n<Staff id="2">\n      {BAR.format(0)}</Staff>'


def page(path: Path, lines, mark=True):
    ms = ""
    for n in lines:
        for i in range(n):
            ms += "<measure>" + ("<print new-system='yes'/>" if mark and not i else "") + "</measure>"
    path.write_text(f"<score-partwise><part id='P1'>{ms}</part></score-partwise>")
    return path


def bars_of(text):
    return text.split('<Staff id="2">')[0].split("<Measure>")[1:]


def test_target_follows_system_marks_and_pages(tmp_path):
    t = target_from_pages([page(tmp_path / "b.p001.musicxml", [3, 2]), page(tmp_path / "b.p002.musicxml", [4])])
    assert (t.bars, t.starts, t.page_of, t.pages) == (9, [0, 3, 5], [0, 0, 1], 2)


def test_pages_without_system_marks_are_refused(tmp_path):
    with pytest.raises(LayoutError):
        target_from_pages([page(tmp_path / "b.p001.musicxml", [3], mark=False)])


def test_breaks_go_on_the_last_bar_of_each_line_and_page():
    out = patch_mscx(score(5), Target(5, [0, 2, 4], [0, 0, 1]), {})
    bars = bars_of(out)
    assert "<subtype>line</subtype>" in bars[1] and "<subtype>page</subtype>" in bars[3]
    assert sum("LayoutBreak" in b for b in bars) == 2
    assert "LayoutBreak" not in out.split('<Staff id="2">')[1]
    assert patch_mscx(out, Target(5, [0, 2, 4], [0, 0, 1]), {}).count("<LayoutBreak>") == 2


def test_section_break_is_kept_and_still_gets_its_page_break():
    text = score(4).replace("<eid>e1</eid>", "<eid>e1</eid><LayoutBreak><subtype>section</subtype></LayoutBreak>")
    line = patch_mscx(text, Target(4, [0, 2], [0, 0]), {})
    assert line.count("<LayoutBreak>") == 1
    paged = patch_mscx(text, Target(4, [0, 2], [0, 1]), {})
    assert "section" in bars_of(paged)[1] and "<subtype>page</subtype>" in bars_of(paged)[1]


def test_stretch_goes_on_the_squeezed_line_only():
    out = patch_mscx(score(4), Target(4, [0, 2], [0, 0]), {1: 0.85})
    assert [("<stretch>0.85</stretch>" in b) for b in bars_of(out)] == [False, False, True, True]
    assert bars_of(out)[2].index("<stretch>") < bars_of(out)[2].index("<voice>")


def test_wrong_bar_count_is_refused():
    with pytest.raises(LayoutError):
        patch_mscx(score(3), Target(4, [0], [0]), {})


def test_style_values_are_replaced_or_added():
    out = patch_style("<Style>\n <spatium>1.75</spatium>\n </Style>", PageStyle(210, 297, 10, 1.5), 1.5, None)
    assert "<spatium>1.5</spatium>" in out and "1.75" not in out and "<pagePrintableWidth>" in out


def test_compare_reports_wrapped_and_moved_lines():
    t = Target(6, [0, 2, 4], [0, 0, 1])
    assert compare(t, [(0, 0), (0, 2), (1, 4)]) == ([], [])
    assert compare(t, [(0, 0), (0, 2), (0, 3), (0, 4)]) == ([1], [2])


def test_fit_squeezes_the_line_that_wraps(tmp_path):
    import zipfile
    src, dest = tmp_path / "a.mscz", tmp_path / "b.mscz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.mscx", score(8))
        z.writestr("score_style.mss", "<Style>\n </Style>")

    def layout(path):                       # line 2 only fits once its stretch is down to 0.72
        text = zipfile.ZipFile(path).read("a.mscx").decode()
        extra = [] if "<stretch>0.72</stretch>" in text else [(0, 3)]
        return sorted([(0, 0), (0, 2), (0, 4), (0, 6)] + extra, key=lambda s: s[1])

    result = fit(src, dest, Target(8, [0, 2, 4, 6], [0, 0, 0, 0]), PageStyle(210, 297, 10, 1.6), layout,
                 lambda _: None)
    assert result.matches and result.squeezed == {1: 0.72} and result.spatium_mm == 1.6
    assert "<stretch>0.72</stretch>" in zipfile.ZipFile(dest).read("a.mscx").decode()
