"""Hand-specified alignment: map homr measures to printed *units* (bars or multi-rests).

Why: each part is OMR'd separately, and homr (a) collapses multi-measure rests
to one measure, (b) loses time-signature changes, and (c) knows nothing about
the printed bar numbers.  The spec lists, per part, which printed bar ranges are
drawn as a multi-rest and which single bars are whole-measure rests.  Homr's
measure k is then mapped to the k-th printed unit; the mapping is verified
(every rest unit must land on a rest-only homr measure and vice versa) and every
note bar is checked against the true bar length.

Spec interface (in addition to the common attributes in
:mod:`musicocr.alignment.spec`):

* ``PARTS``: ``{part_id: (display_name, path_to_homr_musicxml)}``
* ``MULTIREST(part_id) -> [(first_bar, last_bar), ...]`` multi-rest ranges
* ``REST_BARS(part_id) -> set of bars`` printed as single-bar rests
* optional ``fix_bar(part_id, bar, homr_measure, spec, ctx) -> None | dict`` to
  repair a structurally wrong bar.  Return ``{"measure": build, "why": text}``
  where ``build(bar, first, prev_meter) -> <measure>``.  ``ctx`` is a
  :class:`FixContext` offering ``DIV``, ``_attributes(m, spec, bar, first,
  prev_meter, None)`` and ``clean_copy(measure, bar, spec, first, prev_meter,
  None)``.
* optional ``CLEF`` ``(sign, line)`` (default ``("G", "2")``), ``TRANSPOSE``
  ``(diatonic, chromatic)`` (default ``(-4, -7)``, Horn in F), ``PART_ABBR``
  (format string, default ``"Hn {id}"``) and ``MIDI_PROGRAM`` (default 61).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from . import AlignmentError
from .build import (add_attributes, build_score, check_length, copy_notes,
                    is_rest_only, rest_measure)
from .spec import DIV, bar_len

Unit = Tuple[int, int]


def units(spec: Any, part: Any) -> List[Unit]:
    """Printed units ``(first_bar, last_bar)`` for ``part`` in order, covering 1..TOTAL_BARS."""
    starts = {a: (a, b) for a, b in spec.MULTIREST(part)}
    out: List[Unit] = []
    b = 1
    while b <= spec.TOTAL_BARS:
        if b in starts:
            out.append(starts[b])
            b = starts[b][1] + 1
        else:
            out.append((b, b))
            b += 1
    return out


def _attributes(m: ET.Element, spec: Any, number: int, first: bool,
                prev_meter: Optional[Tuple[int, int]], part_ctx: Any = None) -> None:
    """Attributes for a combined-score measure: full set on the first, time on meter changes."""
    meter = spec.meter(number)
    if not first and meter == prev_meter:
        return
    add_attributes(
        m,
        divisions=DIV if first else None,
        key=0 if first else None,
        meter=meter,
        clef=getattr(spec, "CLEF", ("G", "2")) if first else None,
        transpose=getattr(spec, "TRANSPOSE", (-4, -7)) if first else None)


def whole_rest(number: int, spec: Any, first: bool, prev_meter: Any, part_ctx: Any = None) -> ET.Element:
    m = ET.Element("measure", number=str(number))
    _attributes(m, spec, number, first, prev_meter, part_ctx)
    n = rest_measure(number, bar_len(spec, number)).find("note")
    m.append(n)
    return m


def clean_copy(src: ET.Element, number: int, spec: Any, first: bool,
               prev_meter: Any, ctx: Any = None) -> ET.Element:
    """Copy a homr measure's notes into a fresh measure (homr's own
    attributes/print/barline are unreliable and dropped)."""
    m = ET.Element("measure", number=str(number))
    _attributes(m, spec, number, first, prev_meter, ctx)
    copy_notes(src, m)
    return m


class FixContext:
    """What a spec's ``fix_bar`` gets as ``ctx`` (kept stable for existing specs)."""
    DIV = DIV
    _attributes = staticmethod(_attributes)
    clean_copy = staticmethod(clean_copy)


def build_part(spec: Any, part: Any, homr_path: Union[str, Path], report: List[str]) -> List[ET.Element]:
    """Measures for one part, aligned to printed bars; problems are appended to ``report``."""
    src = ET.parse(homr_path).getroot().find("part").findall("measure")
    U = units(spec, part)
    if len(src) != len(U):
        report.append(f"  !! part {part}: homr has {len(src)} measures but the printed "
                      f"structure has {len(U)} units -- aligning by index would be wrong")
        raise AlignmentError("\n".join(report))
    rest_bars = spec.REST_BARS(part)
    override = getattr(spec, "fix_bar", None)
    out: List[ET.Element] = []
    prev_meter = None
    for k, ((a, b), sm) in enumerate(zip(U, src), 1):
        multi = a != b
        want_rest = multi or a in rest_bars
        got_rest = is_rest_only(sm)
        fixed = override(part, a, sm, spec, FixContext) if override and not multi else None
        if want_rest != got_rest and fixed is None:
            report.append(f"  part {part}: unit {a}{'-' + str(b) if multi else ''} <- homr#{k}: "
                          f"expected {'rest' if want_rest else 'notes'}, homr has "
                          f"{'rest' if got_rest else 'notes'}; using printed structure")
        for bar in range(a, b + 1):
            first = not out
            if fixed is not None and bar == a:
                m = fixed["measure"](bar, first, prev_meter)
                report.append(f"  part {part}: bar {bar} <- homr#{k}: repaired ({fixed['why']})")
            elif want_rest:
                m = whole_rest(bar, spec, first, prev_meter)
                if not multi and not got_rest:
                    report.append(f"  part {part}: bar {bar} <- homr#{k}: notes replaced by rest "
                                  f"(printed as a rest)")
            else:
                m = clean_copy(sm, bar, spec, first, prev_meter)
                bad = check_length(m, spec, bar)
                if bad:
                    report.append(f"  part {part}: bar {bar} <- homr#{k}: LENGTH {bad[0]:g} beats, "
                                  f"expected {bad[1]:g} -- NEEDS MANUAL FIX")
            prev_meter = spec.meter(bar)
            out.append(m)
    return out


def combine(spec: Any) -> Tuple[ET.Element, List[str]]:
    """Build the combined score for every part in ``spec.PARTS``; returns (score, report lines)."""
    report: List[str] = []
    parts, infos = {}, {}
    for p, (name, path) in spec.PARTS.items():
        infos[p] = dict(name=name, abbr=getattr(spec, "PART_ABBR", "Hn {id}").format(id=p),
                        program=getattr(spec, "MIDI_PROGRAM", 61))
        report.append(f"part {p} ({name}):")
        parts[p] = build_part(spec, p, path, report)
    return build_score(spec, parts, infos), report
