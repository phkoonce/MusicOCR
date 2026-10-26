"""Bar capacity tracking shared by the MusicXML bar fixers (musicocr/util.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from musicocr.util import measures_with_capacity


def _attrs(div=None, beats=None, beat_type=None):
    div_x = f"<divisions>{div}</divisions>" if div else ""
    t = f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>" if beats else ""
    return f"<attributes>{div_x}{t}</attributes>"


def test_capacity_follows_meter_and_divisions():
    part = ET.fromstring(
        "<part><measure number='1'/>"                        # nothing declared yet
        f"<measure number='2'>{_attrs(4, 3, 4)}</measure>"
        "<measure number='3'/>"                              # meter carries over
        f"<measure number='4'>{_attrs(None, 6, 8)}</measure>"
        f"<measure number='5'>{_attrs(1, 6, 8)}</measure></part>")      # 3 divisions per 6/8 bar at div=1: whole
    got = [(m.get("number"), d, c) for m, d, c in measures_with_capacity(part)]
    assert got == [("1", 1, None), ("2", 4, 12), ("3", 4, 12), ("4", 4, 12), ("5", 1, 3)]


def test_fractional_capacity_is_none():
    part = ET.fromstring(f"<part><measure number='1'>{_attrs(1, 3, 8)}</measure></part>")   # 1.5 divisions
    assert [c for _, _, c in measures_with_capacity(part)] == [None]
