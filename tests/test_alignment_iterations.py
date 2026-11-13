"""Page-start sanity (`_increasing` with page lengths), total consensus (`_agreeing_totals`) and mark rematching."""
from musicocr.assemble.anchored import rematch_marks, raw_marks
from musicocr.assemble.combine import _agreeing_totals, _increasing
from tests.test_anchored import bar, run


def test_start_far_behind_the_bars_read_is_ignored():
    # pages of 47, 40 and 49 bars; page 3 "starts at 52" although 87 bars were read before it
    starts, report = [1, None, 52], []
    _increasing("p", starts, [None] * 3, report, [47, 40, 49])
    assert starts == [1, None, None]
    assert "reach past" in report[0]


def test_start_near_the_bars_read_is_kept():
    starts, report = [1, 45, 90], []
    _increasing("p", starts, [None] * 3, report, [47, 44, 40])      # a bar lost or two on the way is normal
    assert starts == [1, 45, 90] and not report
    starts = [1, 50]
    _increasing("p", starts, [None] * 2, [], [47, 40])              # starts ahead of the bars read: lost bars, kept
    assert starts == [1, 50]


def test_increasing_without_lengths_is_unchanged():
    starts = [1, 30, 20]
    _increasing("p", starts, [None] * 3, [])
    assert starts == [1, 30, None]


def test_lone_far_total_loses_to_a_cluster():
    assert sorted(_agreeing_totals([85, 80, 144, 79])) == [79, 80, 85]
    assert _agreeing_totals([60, 60, 90]) == [60, 60]


def test_total_tie_goes_to_the_commoner_total():
    assert _agreeing_totals([16, 16, 21, 20]) == [16, 16]


def test_total_single_cluster_unchanged():
    assert sorted(_agreeing_totals([60, 61, 59, 60])) == [59, 60, 60, 61]


def test_rematch_labels_a_new_movement_whose_first_marks_are_unread():
    # the reference has movement 1 (1..5) and movement 2 (1..5); the part misses 1-3 of movement 2
    ref = {(1, k): 10 * k for k in range(1, 6)}
    ref.update({(2, k): 100 + 10 * k for k in range(1, 6)})
    mine = {5: "#1", 15: "#2", 25: "#3", 35: "#4", 45: "#5", 135: "#4", 145: "#5"}
    got = rematch_marks(raw_marks(run(mine, 160), "number"), ref)
    assert got[(2, 4)] == 135 and got[(2, 5)] == 145 and got[(1, 5)] == 45
    assert (1, 4) in got and len(got) == 7


def test_rematch_drops_a_misplaced_mark():
    ref = {(1, k): 10 * k for k in range(1, 6)}
    mine = {10: "#1", 20: "#2", 90: "#3", 40: "#4", 50: "#5"}       # the 3 sits 60 bars from where it belongs
    got = rematch_marks(raw_marks(run(mine, 100), "number"), ref)
    assert (1, 3) not in got and got[(1, 4)] == 40


def _src(xml: str):
    import xml.etree.ElementTree as ET
    return (ET.fromstring(f"<measure number='1'>{xml}</measure>"), 4)


def test_dropped_bar_hands_its_meter_and_key_on():
    from musicocr.assemble.combine import _declared, _hand_on_declarations
    pg = [_src("<attributes><divisions>4</divisions><key><fifths>-1</fifths></key><time><beats>3</beats>"
               "<beat-type>4</beat-type></time></attributes><note><rest/><duration>4</duration></note>"),
          _src("<note><rest/><duration>12</duration></note>"),
          _src("<note><rest/><duration>12</duration></note>")]
    _hand_on_declarations(pg, [1, 2])             # bar 0 was dropped
    assert _declared(pg[1][0]) == (-1, (3, 4))
    assert _declared(pg[2][0]) == (None, None)


def test_dropped_bar_does_not_overwrite_the_next_bars_own_declaration():
    from musicocr.assemble.combine import _declared, _hand_on_declarations
    pg = [_src("<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"
               "<note><rest/><duration>4</duration></note>"),
          _src("<attributes><time><beats>6</beats><beat-type>8</beat-type></time></attributes>"
               "<note><rest/><duration>4</duration></note>")]
    _hand_on_declarations(pg, [1])
    assert _declared(pg[1][0]) == (None, (6, 8))


def test_plausible_lasts_drops_a_lone_misread_number():
    from musicocr.assemble.combine import _plausible_lasts
    # paxman p24: the scan read "99" for 66; no other part or total comes near
    assert _plausible_lasts([33, 99, 72, 53, 33, 99, 72, 53], [64, 75, 74, 69]) == [33, 72, 53, 33, 72, 53]
    # a number the totals support stays
    assert _plausible_lasts([30, 99, 72, 30], [100, 98, 97]) == [30, 99, 72, 30]
    # too few distinct numbers to call one an outlier
    assert _plausible_lasts([20, 99], [60]) == [20, 99]


def test_far_ahead_drops_numbers_beyond_anything_read():
    from musicocr.assemble.combine import _far_ahead
    def part(starts, bars, anchors=None):
        return dict(pages=[[0] * b for b in bars], starts=list(starts), sysinfo=[dict(anchors=dict(a)) for a in (anchors or [{}] * len(bars))])
    data = {"a": part([1, 63, 776], [60, 45, 40], [{0: 1}, {0: 63}, {0: 776, 1: 836}]),
            "b": part([1, 64], [62, 50])}
    rep: list = []
    _far_ahead(data, rep)
    assert data["a"]["starts"] == [1, 63, None] and data["a"]["sysinfo"][2]["anchors"] == {}
    assert data["b"]["starts"] == [1, 64] and rep      # the real starts stay


def test_common_left_survives_staves_without_start_columns():
    from musicocr.assemble.alignment.systems import _common_left
    assert _common_left([[], [], []], 600) is None
    assert _common_left([[40], [41], [42]], 600) == 40
