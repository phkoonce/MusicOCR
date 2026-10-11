"""Tests for musicocr.multirest (collapse/expand) and the Copisteria config option."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from musicocr.config import ConfigError, CopisteriaConfig
from musicocr.multirest import collapse_multirests, expand_multirests

ATTR = ("<attributes><divisions>2</divisions><time><beats>4</beats><beat-type>4</beat-type>"
        "</time></attributes>")
TAG = "<attributes><measure-style><multiple-rest>{n}</multiple-rest></measure-style></attributes>"
REST = '<note><rest measure="yes"/><duration>8</duration><voice>1</voice></note>'
NOTE = ("<note><pitch><step>C</step><octave>4</octave></pitch><duration>8</duration>"
        "<type>whole</type></note>")


def m(num, body):
    return f'<measure number="{num}">{body}</measure>'


def score(*parts):
    ps = "".join(f'<part id="P{i}">{p}</part>' for i, p in enumerate(parts, 1))
    return f'<?xml version="1.0"?><score-partwise version="4.0">{ps}</score-partwise>'


def write(tmp_path, xml):
    p = tmp_path / "x.musicxml"
    p.write_text(xml)
    return p


def nums(path, part=0):
    root = ET.parse(path).getroot()
    return [x.get("number") for x in root.findall("part")[part].findall("measure")]


def kinds(path, part=0):
    out = []
    for x in ET.parse(path).getroot().findall("part")[part].findall("measure"):
        tag = x.find("./attributes/measure-style/multiple-rest")
        out.append(("T" + tag.text) if tag is not None else
                   "R" if x.find("note/rest") is not None else "N")
    return out


EXPANDED = score(
    m(1, ATTR + NOTE) + m(2, TAG.format(n=3) + REST) + m(3, REST) + m(4, REST) + m(5, NOTE))
COLLAPSED = score(
    m(1, ATTR + NOTE) + m(2, TAG.format(n=3) + REST) + m(3, NOTE))


def test_collapse_basic_and_renumber(tmp_path):
    p = write(tmp_path, EXPANDED)
    assert collapse_multirests(p, log=lambda s: None) == 2
    assert kinds(p) == ["N", "T3", "N"]
    assert nums(p) == ["1", "2", "3"]


def test_collapse_noop_leaves_file_untouched(tmp_path):
    p = write(tmp_path, COLLAPSED)
    before = p.read_text()
    assert collapse_multirests(p, log=lambda s: None) == 0
    assert p.read_text() == before


def test_collapse_stops_at_non_rest_follower(tmp_path):
    xml = score(m(1, ATTR + TAG.format(n=4) + REST) + m(2, REST) + m(3, NOTE) + m(4, REST))
    p = write(tmp_path, xml)
    logs = []
    assert collapse_multirests(p, log=logs.append) == 1
    assert kinds(p) == ["T4", "N", "R"]
    assert any("WARNING" in s for s in logs)


def test_collapse_does_not_drop_ordinary_rests_or_bars_with_attributes(tmp_path):
    plain = '<note><rest/><duration>8</duration></note>'  # not measure="yes"
    xml = score(m(1, ATTR + TAG.format(n=3) + REST) + m(2, plain) + m(3, REST))
    p = write(tmp_path, xml)
    assert collapse_multirests(p, log=lambda s: None) == 0
    xml2 = score(m(1, ATTR + TAG.format(n=2) + REST) + m(2, ATTR + REST))
    p2 = write(tmp_path, xml2)
    assert collapse_multirests(p2, log=lambda s: None) == 0


def test_multiple_multirests_in_one_part_and_parts_independent(tmp_path):
    part1 = (m(1, ATTR + TAG.format(n=2) + REST) + m(2, REST) + m(3, NOTE)
             + m(4, TAG.format(n=3) + REST) + m(5, REST) + m(6, REST) + m(7, NOTE))
    part2 = m(1, ATTR + NOTE) + m(2, NOTE)
    p = write(tmp_path, score(part1, part2))
    assert collapse_multirests(p, log=lambda s: None) == 3
    assert kinds(p) == ["T2", "N", "T3", "N"]
    assert nums(p) == ["1", "2", "3", "4"]
    assert nums(p, 1) == ["1", "2"]


def test_pickup_numbering_preserved(tmp_path):
    xml = score(m(0, ATTR + NOTE) + m(1, TAG.format(n=2) + REST) + m(2, REST) + m(3, NOTE))
    p = write(tmp_path, xml)
    collapse_multirests(p, log=lambda s: None)
    assert nums(p) == ["0", "1", "2"]


def test_expand_basic(tmp_path):
    p = write(tmp_path, COLLAPSED)
    assert expand_multirests(p, log=lambda s: None) == 2
    assert kinds(p) == ["N", "T3", "R", "R", "N"]
    assert nums(p) == ["1", "2", "3", "4", "5"]
    durs = [x.findtext("note/duration") for x in ET.parse(p).getroot().iter("measure")]
    assert durs[2] == durs[3] == "8"  # 4/4 at divisions 2


def test_expand_uses_time_signature_and_fills_empty_tagged_bar(tmp_path):
    attr = ("<attributes><divisions>4</divisions><time><beats>6</beats><beat-type>8</beat-type>"
            "</time></attributes>")
    p = write(tmp_path, score(m(1, attr + TAG.format(n=2))))  # tagged bar has no note
    assert expand_multirests(p, log=lambda s: None) == 1
    root = ET.parse(p).getroot()
    last = root.findall("part/measure")[1]
    assert last.find("note/rest").get("measure") == "yes"
    assert last.findtext("note/duration") == "12"


def test_expand_without_time_signature_warns(tmp_path):
    p = write(tmp_path, score(m(1, TAG.format(n=3) + REST)))
    logs = []
    assert expand_multirests(p, log=logs.append) == 0
    assert any("WARNING" in s for s in logs)


def test_round_trip(tmp_path):
    p = write(tmp_path, EXPANDED)
    collapse_multirests(p, log=lambda s: None)
    expand_multirests(p, log=lambda s: None)
    assert kinds(p) == ["N", "T3", "R", "R", "N"]
    assert nums(p) == ["1", "2", "3", "4", "5"]
    collapse_multirests(p, log=lambda s: None)
    assert kinds(p) == ["N", "T3", "N"]


def test_barline_moves_with_collapse_and_expand(tmp_path):
    bar = '<barline location="right"><bar-style>light-heavy</bar-style></barline>'
    xml = score(m(1, ATTR + TAG.format(n=2) + REST) + m(2, REST + bar))
    p = write(tmp_path, xml)
    collapse_multirests(p, log=lambda s: None)
    assert ET.parse(p).getroot().find("part/measure/barline") is not None
    expand_multirests(p, log=lambda s: None)
    ms = ET.parse(p).getroot().findall("part/measure")
    assert ms[0].find("barline") is None and ms[1].find("barline") is not None


def test_config_option():
    assert CopisteriaConfig.from_dict({}).multirest_form == "collapse"
    assert CopisteriaConfig.from_dict({"multirest_form": "keep"}).multirest_form == "keep"
    with pytest.raises(ConfigError):
        CopisteriaConfig.from_dict({"multirest_form": "bogus"})
