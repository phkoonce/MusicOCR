"""Post-processing fixes for Copisteria's MusicXML.

Copisteria writes a bar it found no staff content for (e.g. a multi-measure
rest whose count it could not read, at a page's end) as a bare ``<forward>``
with no note or rest. MuseScore then shows an empty, un-rested bar and
music21 sees no duration for it. ``fill_empty_measures`` turns each such bar
into a whole-measure rest of the forwarded length -- the same remedy
``homr_fixes.fix_multirest_file`` applies to homr's empty multi-rest bars.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET


def fill_empty_measures(path: Path, log: Callable[[str], None] = print) -> int:
    """Rewrite ``path`` in place; return the number of bars given a rest."""
    tree = ET.parse(path)
    fixed = 0
    for part in tree.getroot().findall("part"):
        for measure in part.findall("measure"):
            if measure.find("note") is not None:
                continue
            fwd = measure.find("forward")
            dur = fwd.find("duration") if fwd is not None else None
            if dur is None or not dur.text:
                continue
            note = ET.Element("note")
            ET.SubElement(note, "rest", {"measure": "yes"})
            ET.SubElement(note, "duration").text = dur.text
            ET.SubElement(note, "voice").text = "1"
            measure.remove(fwd)
            measure.append(note)
            fixed += 1
            log(f"  measure {measure.get('number')}: empty bar given a whole-measure rest "
                f"(duration={dur.text})")
    if fixed:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return fixed


# --- ghost bars from cautionary key/time signatures --------------------------------
#
# A cautionary key + time signature printed after a double bar at the end of a system
# (repeated at the next system's start) can be read as an extra, note-bearing "measure":
# Zerberus Horn 1 p.1 gets a bar holding only a key change and a spurious whole note,
# followed by a bar that carries just the time change. Real music puts a key and a time
# change at the same barline, so the signature being split over two consecutive bars,
# right after a double barline, with a few plain notes in the first and no other
# content, is strong evidence the first bar is a misread signature.

_DOUBLE = {"light-light", "light-heavy", "heavy-light", "heavy-heavy"}


def _sig(measure: ET.Element) -> tuple[ET.Element | None, ET.Element | None]:
    attrs = measure.find("attributes")
    if attrs is None:
        return None, None
    return attrs.find("key"), attrs.find("time")


def _same(a: ET.Element | None, b: ET.Element | None) -> bool:
    return (a is not None and b is not None
            and ET.tostring(a) == ET.tostring(b))


def _ends_with_double_bar(measure: ET.Element) -> bool:
    for bl in measure.findall("barline"):
        if bl.get("location", "right") == "right" and bl.findtext("bar-style") in _DOUBLE:
            return True
    return False


def _is_ghost(prev: ET.Element, ghost: ET.Element, nxt: ET.Element) -> bool:
    if not _ends_with_double_bar(prev):
        return False
    # Only attributes + a few plain pitched notes; anything else is content we can't explain.
    kinds = [c.tag for c in ghost]
    if any(k not in ("attributes", "note") for k in kinds) or "attributes" not in kinds:
        return False
    attrs = ghost.find("attributes")
    if any(c.tag not in ("key", "time", "divisions") for c in attrs):
        return False
    notes = ghost.findall("note")
    if not 1 <= len(notes) <= 2:
        return False
    for n in notes:
        if n.find("pitch") is None or n.find("rest") is not None or n.find("notations") is not None \
                or n.find("tie") is not None or n.find("lyric") is not None:
            return False
    gk, gt = _sig(ghost)
    nk, nt = _sig(nxt)
    if gk is None and gt is None:
        return False
    # Complementary halves of one signature block: what the ghost declares, the next bar
    # does not contradict, and the next bar declares something the ghost does not.
    if gk is not None and nk is not None and not _same(gk, nk):
        return False
    if gt is not None and nt is not None and not _same(gt, nt):
        return False
    if (gk is None) == (gt is None):
        # ghost has both: only if the next bar repeats at least one of them identically
        return _same(gk, nk) or _same(gt, nt)
    return (gk is not None and nt is not None and nk is None) or \
           (gt is not None and nk is not None and nt is None)


def drop_ghost_bars(path: Path, log: Callable[[str], None] = print) -> int:
    """Remove ghost bars (see above) in place; return how many were dropped.

    The ghost's key/time declarations move onto the following bar so nothing is
    lost; measures are renumbered.
    """
    tree = ET.parse(path)
    dropped = 0
    for part in tree.getroot().findall("part"):
        measures = part.findall("measure")
        i = 1
        while i < len(measures) - 1:
            prev, ghost, nxt = measures[i - 1], measures[i], measures[i + 1]
            if not _is_ghost(prev, ghost, nxt):
                i += 1
                continue
            attrs = nxt.find("attributes")
            if attrs is None:
                attrs = ET.Element("attributes")
                nxt.insert(0, attrs)
            gk, gt = _sig(ghost)
            if gk is not None and attrs.find("key") is None:
                pos = 1 if attrs.find("divisions") is not None else 0
                attrs.insert(pos, gk)
            if gt is not None and attrs.find("time") is None:
                pos = [c.tag for c in attrs].index("key") + 1 if attrs.find("key") is not None \
                    else (1 if attrs.find("divisions") is not None else 0)
                attrs.insert(pos, gt)
            notes = [(n.findtext("pitch/step") or "?") + (n.findtext("pitch/octave") or "")
                     for n in ghost.findall("note")]
            log(f"  measure {ghost.get('number')}: dropped ghost bar after a double bar "
                f"(key/time-signature bar with note(s) {notes}; next bar carries the other half "
                f"of the signature)")
            part.remove(ghost)
            measures.remove(ghost)
            dropped += 1
        for n, m in enumerate(part.findall("measure"), int(measures[0].get("number", "1"))
                              if measures and measures[0].get("number", "").isdigit() else 1):
            m.set("number", str(n))
    if dropped:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return dropped


GAP_COLOR = "#E67E00"


def _is_hidden_rest(el: ET.Element) -> bool:
    return el.tag == "note" and el.get("print-object") == "no" and el.find("rest") is not None


def fix_opening_pickup(path: Path, log: Callable[[str], None] = print) -> int:
    """Make the first bar of the score's first page a real pickup bar.

    When Copisteria does not see the printed opening meter it treats the first bar
    as a full bar and pads a short one (an upbeat) with a trailing *hidden* rest,
    and it marks the time signature itself as not printed. A first page always
    prints its meter, so: drop that trailing padding (leaving a short pickup bar)
    and show the time signature. Returns the number of changes made.
    """
    tree = ET.parse(path)
    changed = 0
    for part in tree.getroot().findall("part"):
        first = part.find("measure")
        if first is None:
            continue
        for time in first.findall("./attributes/time"):
            if time.get("print-object") == "no":
                del time.attrib["print-object"]
                changed += 1
        kids = list(first)
        while kids and (kids[-1].tag == "forward" or _is_hidden_rest(kids[-1])):
            if not any(k.tag == "note" and not _is_hidden_rest(k) for k in kids[:-1]):
                break   # nothing real before the padding: leave it
            first.remove(kids.pop())
            changed += 1
            log(f"  measure {first.get('number')}: dropped trailing padding (pickup bar)")
    if changed:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return changed


def reveal_gaps(path: Path, color: str = GAP_COLOR,
                log: Callable[[str], None] = print) -> int:
    """Turn the gaps Copisteria leaves where it read nothing into visible, coloured rests.

    Copisteria places notes at the onsets the model read and draws nothing else, so
    a symbol it missed shows up as a gap (a ``<forward>``, or an explicit hidden
    rest). Written as-is those become invisible rests in MuseScore. Making them
    ordinary rests keeps the bar adding up and the colour marks every spot where
    a symbol was probably dropped, for hand-correction. Multi-voice bars (any
    ``<backup>``) are left alone. Returns the number of gaps revealed.
    """
    tree = ET.parse(path)
    revealed = 0
    for part in tree.getroot().findall("part"):
        for measure in part.findall("measure"):
            if measure.find("backup") is not None:
                continue
            kids = list(measure)
            for i, el in enumerate(kids):
                if el.tag == "forward":
                    if not any(k.tag == "note" for k in kids):
                        continue            # a wholly empty bar: fill_empty_measures' job
                    note = ET.Element("note")
                    ET.SubElement(note, "rest")
                    ET.SubElement(note, "duration").text = el.findtext("duration") or "0"
                    for tag in ("voice", "staff"):
                        child = el.find(tag)
                        if child is not None and child.text:
                            ET.SubElement(note, tag).text = child.text
                    note.set("color", color)
                    measure.insert(list(measure).index(el), note)
                    measure.remove(el)
                elif _is_hidden_rest(el):
                    for attr in ("print-object", "print-spacing"):
                        el.attrib.pop(attr, None)
                    el.set("color", color)
                else:
                    continue
                revealed += 1
                log(f"  measure {measure.get('number')}: gap revealed as a coloured rest")
    if revealed:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return revealed


# --- key signature that Copisteria never reads: C major / cancelled ------------------
#
# Every system of a printed part starts with its key signature -- except C major (or a
# key just cancelled), which prints nothing (the cancelling naturals are not a symbol
# Copisteria detects). Its measure head then says "key: not set" and the writer carries
# the previous system's key forward, so Zerberus Horn 6's last 16 bars (printed in C
# major after A major) come out with every C#/F#/G# of the old key signature.
# The first bar of each staff always shows a clef; a staff-first bar with a clef but no
# key reading therefore means "no accidentals in the key signature".

_SHARP_STEPS = "FCGDAEB"
_FLAT_STEPS = "BEADGCF"


def _key_alter(step: str, fifths: int) -> int:
    if fifths > 0:
        return 1 if step in _SHARP_STEPS[:fifths] else 0
    if fifths < 0:
        return -1 if step in _FLAT_STEPS[:-fifths] else 0
    return 0


def staff_first_without_key(reading_json: Path) -> list[int]:
    """0-based bar indices (in reading order = measure order) that open a staff (clef read)
    but carry no key reading."""
    import json
    tokens = json.loads(Path(reading_json).read_text()).get("tokens", {})
    bars = [t for t in tokens.values() if (t.get("cls") or {}).get("v") == "measure"]
    out = []
    for i, t in enumerate(bars):
        if (t.get("clef") or {}).get("v") is not None and (t.get("key") or {}).get("v") is None \
                and (t.get("key") or {}).get("p", 0) >= 0.9:
            out.append(i)
    return out


def fix_missing_key_cancel(path: Path, reading_json: Path,
                           log: Callable[[str], None] = print) -> int:
    """Reset the key to C major where a staff opens with no key signature printed but
    the writer carried a sharp/flat key over from the previous system.

    Notes whose alteration only came from the old key signature (no ``<accidental>``)
    lose it; explicitly accidental notes are kept. Returns the number of bars changed
    (the bar that gets the new ``<key>`` counts once)."""
    opens = set(staff_first_without_key(reading_json))
    if not opens:
        return 0
    tree = ET.parse(path)
    changes = 0
    for part in tree.getroot().findall("part"):
        measures = part.findall("measure")
        key = 0
        old: int | None = None          # the wrongly carried key being cancelled
        for i, m in enumerate(measures):
            attrs = m.find("attributes")
            declared = attrs.find("key") if attrs is not None else None
            if declared is not None:
                key = int(declared.findtext("fifths") or 0)
                old = None
            elif i in opens and key != 0:
                old = key
                key = 0
                if attrs is None:
                    attrs = ET.Element("attributes")
                    m.insert(0, attrs)
                k = ET.Element("key")
                ET.SubElement(k, "fifths").text = "0"
                pos = 1 if attrs.find("divisions") is not None else 0
                attrs.insert(pos, k)
                changes += 1
                log(f"  measure {m.get('number')}: staff opens with no key signature -> "
                    f"C major (was carrying {old:+d})")
            if old is None:
                continue
            for n in m.findall("note"):
                p = n.find("pitch")
                if p is None or n.find("accidental") is not None:
                    continue
                step = p.findtext("step") or ""
                alt = p.find("alter")
                if alt is not None and int(float(alt.text or 0)) == _key_alter(step, old) != 0:
                    p.remove(alt)
    if changes:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return changes


# --- time signature the page never printed --------------------------------------------

def _last_time(path: Path) -> tuple[int, int] | None:
    last = None
    for t in ET.parse(path).getroot().iter("time"):
        try:
            last = (int(t.findtext("beats")), int(t.findtext("beat-type")))
        except (TypeError, ValueError):
            pass
    return last


def inherit_unprinted_time(path: Path, prev_path: Path,
                           log: Callable[[str], None] = print) -> int:
    """A page that opens without a printed time signature continues the previous page's.

    Copisteria then guesses a meter from the bar lengths, and 6/8 and 3/4 (same length)
    are interchangeable to it (Zerberus Horn 6 got 3/4 on pages 2 and 4 of a 6/8
    piece). When the guess is flagged ``print-object="no"`` and has the same bar
    length as the previous page's last meter, that meter is used instead. Returns the
    number of signatures replaced."""
    prev = _last_time(prev_path) if prev_path.exists() else None
    if prev is None:
        return 0
    tree = ET.parse(path)
    done = 0
    for part in tree.getroot().findall("part"):
        first = part.find("measure")
        t = first.find("attributes/time") if first is not None else None
        if t is None or t.get("print-object") != "no":
            continue
        try:
            cur = (int(t.findtext("beats")), int(t.findtext("beat-type")))
        except (TypeError, ValueError):
            continue
        if cur == prev or cur[0] * prev[1] != prev[0] * cur[1]:
            continue
        t.find("beats").text, t.find("beat-type").text = str(prev[0]), str(prev[1])
        done += 1
        log(f"  time {cur[0]}/{cur[1]} (not printed) -> {prev[0]}/{prev[1]} "
            f"(previous page's meter, same bar length)")
    if done:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    return done
