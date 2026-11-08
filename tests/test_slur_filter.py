from musicocr.engines.copisteria.run import stray_curve


def rec(**kw):
    base = {"cross": True, "pitch": ["D5", "E5"], "h": 3.0, "notes": 2, "da": 0.5}
    base.update(kw)
    return base


def test_tall_curve_across_a_bar_line_is_stray():
    assert stray_curve(rec())
    assert stray_curve(rec(h=9.4, notes=3))


def test_flat_slur_across_a_bar_line_is_kept():
    assert not stray_curve(rec(h=1.3))
    assert not stray_curve(rec(h=2.4))


def test_inside_a_bar_a_tie_or_a_long_slur_is_kept():
    assert not stray_curve(rec(cross=False))
    assert not stray_curve(rec(pitch=["G4", "G4"]))      # a tie: slur_ties' business
    assert not stray_curve(rec(notes=4, h=8.5))          # a phrase slur
    assert not stray_curve(rec(da=3.1))                  # not attached to a note at its start
