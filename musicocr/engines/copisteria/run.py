"""Run inside the Copisteria venv (cwd = the Copisteria repo): ``copisteria.pipeline`` with two
text-driven corrections that Copisteria's own reader does not make.

    python copisteria_run.py <page.png> --out <dir> [--detector 2m] [--no-text-tuplets]

1. **Tuplet digits.** A bracketed 2:3 duplet in compound time (two eighths in the time of three,
   marked with a small "2") is read by Copisteria's evidence model per head, usually with only
   a weak or no ``2/3`` on the heads, and its rhythm decoder only considers ``none`` and ``3/2``
   anyway; the bar then comes out short and shows as a gap (a hidden ``<forward>`` / rest)
   with the two eighths written at plain length. The page's text layer carries the digit. A
   small digit 2 or 3 (a "2" is the duplet, a "3" the triplet) whose position falls between a
   run of that many consecutive heads of a bar (note/rest, first to last, one staff space of
   slack, and within 2.5 spaces of the run's middle) marks them as that tuplet: their ``tup``
   reading and distribution are set to it (``2/3`` is added to the rhythm decoder's choices).
2. **Multi-rest counts.** Copisteria's count lookup takes the *first* digit box that overlaps a
   multi-rest horizontally and sits within six spaces above it, which can be the small
   tuplet digit of the system above ("2") instead of the real count ("3"). Only big digit
   boxes (>= 5 staff spaces tall: counts are set large) are offered to it.

3. **Text layer** (tempo, expression, technique words). If ``<stem>.textlayer.json`` sits beside the
   page (written by ``musicocr/text/layer.py``: classified phrases with their page boxes), each phrase
   becomes a MusicXML ``<direction>`` in the staff nearest to it and the bar its left edge falls in.
   Only ``<direction>`` elements are added: notes, rests and timing are untouched.

Standalone on purpose (no musicocr imports). The texts are the page's ``<stem>.texts.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RATIO = {2: "2/3", 3: "3/2"}
MIN_TALL_SP = 3.5


def _install() -> None:
    import copisteria.write as w
    from copisteria.vocab import TUPLETS

    w.TUP_CHOICES = ["none", "3/2", "2/3"]
    orig_events, orig_near = w._events, w._texts_near

    def _events(rd, staff, bar, clef, key, texts):
        events, dirs, marks = orig_events(rd, staff, bar, clef, key, texts)
        if not texts:
            return events, dirs, marks
        st = rd.layout.staves[staff]
        sp = st.sp
        ev = sorted((e for e in events if e.kind in ("note", "rest") and e.grace == "none" and not e.chord),
                    key=lambda e: e.x)
        for t in texts:
            s = str(t.get("text", "")).strip()
            if not s.isdigit() or int(s) not in RATIO or "xyxy" not in t:
                continue
            n = int(s)
            x0, y0, x1, y1 = t["xyxy"]
            cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
            if (y1 - y0) >= MIN_TALL_SP * sp or not (st.y0 - 5 * sp < cy < st.y1 + 6 * sp):
                continue
            best = None
            for i in range(len(ev) - n + 1):
                grp = ev[i:i + n]
                mid = (grp[0].x + grp[-1].x) / 2
                if grp[0].x - sp <= cx <= grp[-1].x + sp and (best is None or abs(mid - cx) < best[0]):
                    best = (abs(mid - cx), grp)
            if best is None or best[0] > 2.5 * sp:
                continue
            grp = best[1]
            if any(e.tup not in ("none", RATIO[n]) for e in grp):
                continue
            for e in grp:
                e.tup = RATIO[n]
                if e.dist is not None:
                    d = [0.0] * len(TUPLETS)
                    d[TUPLETS.index(RATIO[n])] = 1.0
                    e.dist["tup"] = d
        return events, dirs, marks

    def _texts_near(texts, box, sp):
        if texts:
            texts = [t for t in texts if "xyxy" in t and (t["xyxy"][3] - t["xyxy"][1]) >= MIN_TALL_SP * sp]
        return orig_near(texts, box, sp)

    w._texts_near = _texts_near
    _mark_systems(w)
    _no_binding_systems()
    if "--no-text-tuplets" not in sys.argv:
        w._events = _events


def _no_binding_systems() -> None:  # also: a staff found twice is one
    """Two staves are one system only if bar lines (or a brace) join them, not the ink at their left alone.

    On a bound book photographed on a desk the spiral binding and the page's shadow run down the left edge
    and cross every gap between staves: copisteria then joins the staves of a page into a few systems and
    reads only the first staff of each (4 staves in 1 system: 5 of 22 bars read). A real system has bar
    lines through its staves, so a left-edge run counts only when they are there too.
    """
    from copisteria import front
    orig = front._ink_links

    def ink_links(gray, syms, a, b, page_left):
        left, bars = orig(gray, syms, a, b, page_left)
        return (left if bars >= 0.2 else min(left, front.INK_LINK - 0.01)), bars

    front._ink_links = ink_links

    systems = front._systems

    def one_staff_each(syms, staves, gray=None, ink=False):
        """A warped page's staff found twice (same lines, a few pixels apart) is one staff: the copy would
        be a staff of its own whose bars are read again (a bar too many on the page)."""
        keep = []
        for st in staves:
            if keep:
                prev = keep[-1]
                overlap = min(prev.y1, st.y1) - max(prev.y0, st.y0)
                if overlap > 0.5 * min(prev.y1 - prev.y0, st.y1 - st.y0):
                    if len(st.bars) > len(prev.bars):
                        keep[-1] = st
                    continue
            keep.append(st)
        staves[:] = keep
        return systems(syms, staves, gray, ink)

    front._systems = one_staff_each


def _single_staff() -> None:
    """A part has one staff per system: nothing joins two staves (not the model, a look-alike bar layout,
    a brace the detector imagined or the ink at the page's left edge)."""
    from copisteria import front, links
    links.load = lambda *a, **k: None
    front._aligned = lambda *a, **k: False
    front._braced = lambda *a, **k: False
    front._ink_links = lambda *a, **k: (0.0, 0.0)


def _mark_systems(w) -> None:
    """Make the writer put ``<print new-system="yes"/>`` on the first bar of every system.

    The page's MusicXML is one run of bars; which of them open a system is what lets ``combine`` line a
    page up with the bar numbers printed at the systems' starts. The writer builds its bars system by
    system (``columns``), so it is re-compiled from its own source with the system noted on each bar.
    """
    import inspect
    src = inspect.getsource(w.write)
    a = 'm = {"staves": [], "time": None, "key": None}'
    b = 'meas = _sub(part, "measure", number=number)\n'
    if src.count(a) != 1 or src.count(b) != 1:      # the library changed: leave the output as it was
        return
    src = src.replace(a, 'm = {"staves": [], "time": None, "key": None, "sys": s_i}')
    src = src.replace(b, b + '            if m_i == 0 or m.get("sys") != measures[m_i - 1].get("sys"):\n'
                      '                _sub(meas, "print", **{"new-system": "yes"})\n')
    exec(compile(src, w.__file__, "exec"), w.__dict__)


# --- text layer ------------------------------------------------------------------------------

CHECK_COLOR = "#7B1FA2"          # same colour the other "check this" marks use
ABOVE_SP, BELOW_SP = 9.0, 7.0     # a phrase more than this far from every staff is not attached


def place_items(items: list, staves: list) -> dict:
    """Assign classified phrases to ``(staff, bar)``.

    ``staves``: ``[{"y0", "y1", "sp", "bars": [(x0, x1), ...]}]`` in page pixels. A phrase goes to
    the staff its vertical centre is nearest to (distance to the staff's lines), unless it is
    farther than ``ABOVE_SP`` above / ``BELOW_SP`` below it; its bar is the last one that starts
    at or before its left edge (one staff space of slack), else the first. Returns
    ``{(staff, bar): [(item, below)]}`` sorted by x.
    """
    out: dict = {}
    for it in items:
        x0, y0, x1, y1 = it["xyxy"]
        cy = (y0 + y1) / 2
        best = None
        for si, st in enumerate(staves):
            if it.get("above") and cy > st["y0"]:      # tempo marks sit above their staff: not under the previous one
                continue
            if cy < st["y0"]:
                d, below = (st["y0"] - cy), False
            elif cy > st["y1"]:
                d, below = (cy - st["y1"]), True
            else:
                d, below = 0.0, cy > (st["y0"] + st["y1"]) / 2
            limit = (BELOW_SP if below else ABOVE_SP) * st["sp"]
            if d <= limit and (best is None or d < best[0]):
                best = (d, si, below)
        if best is None or not staves[best[1]]["bars"]:
            continue
        _, si, below = best
        st = staves[si]
        bar = 0
        for j, (bx0, _bx1) in enumerate(st["bars"]):
            if bx0 - st["sp"] <= x0:
                bar = j
        out.setdefault((si, bar), []).append((it, below))
    for v in out.values():
        v.sort(key=lambda p: p[0]["xyxy"][0])
    return out


def build_direction(parent, it: dict, below: bool, staff_no, sub) -> None:
    """Append the ``<direction>`` for phrase ``it`` to ``parent`` (``sub`` = ``ET.SubElement``-like)."""
    kind = it["kind"]
    de = sub(parent, "direction", placement="below" if below else "above")
    style = it.get("style", "")

    def words(text, **extra):
        attrs = {}
        if "italic" in style:
            attrs["font-style"] = "italic"
        if "bold" in style:
            attrs["font-weight"] = "bold"
        attrs.update(extra)
        w = sub(sub(de, "direction-type"), "words", **attrs)
        w.text = text

    if kind == "tempo":
        if it.get("text"):
            words(it["text"])
        if it.get("bpm"):
            met = sub(sub(de, "direction-type"), "metronome", **({"parentheses": "yes"} if it.get("parens") else {}))
            sub(met, "beat-unit").text = it.get("beat_unit", "quarter")
            if it.get("dotted"):
                sub(met, "beat-unit-dot")
            sub(met, "per-minute").text = str(it["bpm"])
    elif kind == "rehearsal":
        sub(sub(de, "direction-type"), "rehearsal").text = it["text"]
    elif kind == "unknown":
        words("check: " + it["text"], color=CHECK_COLOR)
    else:
        words(it["text"])
    if staff_no:
        sub(de, "staff").text = str(staff_no)
    if kind == "tempo" and it.get("bpm"):
        sub(de, "sound", tempo=str(it["bpm"]))


def _install_text_layer(png: Path) -> None:
    sidecar = png.with_name(f"{png.stem}.textlayer.json")
    if not sidecar.exists():
        return
    items = json.loads(sidecar.read_text())
    if not items:
        return
    import copisteria.write as w

    orig_events, orig_direction = w._events, w._direction
    cache: dict = {}

    def placed(rd) -> dict:
        key = id(rd.layout)
        if key not in cache:
            staves = []
            for si, st in enumerate(rd.layout.staves):
                staves.append({"y0": st.y0, "y1": st.y1, "sp": st.sp,
                               "bars": [tuple(rd.layout.bar_box(si, j)[0::2]) for j in range(len(st.bars))]})
            cache[key] = place_items(items, staves)
        return cache[key]

    def _events(rd, staff, bar, clef, key, texts):
        events, dirs, marks = orig_events(rd, staff, bar, clef, key, texts)
        for it, below in placed(rd).get((staff, bar), []):
            dirs.append((it["xyxy"][0], "tl", json.dumps(it, sort_keys=True), below))
        return events, dirs, marks

    def _direction(meas, d, staff_no):
        if d[1] == "tl":
            build_direction(meas, json.loads(d[2]), d[3], staff_no, w._sub)
        else:
            orig_direction(meas, d, staff_no)

    w._events, w._direction = _events, _direction


def main() -> None:
    _install()
    page = next((Path(a) for a in sys.argv[1:] if a.lower().endswith(".png")), None)
    if page is not None and "--no-text-layer" not in sys.argv:
        _install_text_layer(page)
    if "--single-staff" in sys.argv:
        _single_staff()
    argv = [a for a in sys.argv[1:] if a not in ("--no-text-tuplets", "--no-text-layer", "--single-staff")]
    from copisteria.pipeline import main as pipeline_main
    pipeline_main(argv)


if __name__ == "__main__":
    main()
