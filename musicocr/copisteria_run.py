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

Standalone on purpose (no musicocr imports). The texts are the page's ``<stem>.texts.json``.
"""
from __future__ import annotations

import sys

RATIO = {2: "2/3", 3: "3/2"}
MIN_TALL_SP = 5.0


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
    if "--no-text-tuplets" not in sys.argv:
        w._events = _events


def main() -> None:
    _install()
    argv = [a for a in sys.argv[1:] if a != "--no-text-tuplets"]
    from copisteria.pipeline import main as pipeline_main
    pipeline_main(argv)


if __name__ == "__main__":
    main()
