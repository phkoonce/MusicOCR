"""Merge per-page MusicXML files into one continuous score (public API).

Used by the extract stage and by omr-eval/concat_pages.py.
"""
from __future__ import annotations

from pathlib import Path

from musicocr.musicxml.io import open_wedges


def _signature(el) -> tuple | None:
    """What a clef / key / meter says, to tell a restatement from a change."""
    from music21 import clef, key, meter
    if isinstance(el, clef.Clef):
        return "clef", el.sign, el.line, el.octaveChange
    if isinstance(el, key.KeySignature):
        return "key", el.sharps
    if isinstance(el, meter.TimeSignature):
        return "time", el.ratioString, el.symbol
    return None


def _in_force(measures) -> dict:
    out: dict = {}
    for m in measures:
        for el in m.recurse():
            sig = _signature(el)
            if sig:
                out[sig[0]] = sig
    return out


def _drop_restated(measure, current: dict) -> None:
    """A new page opens by restating the clef, key and meter that already apply; in one score they are
    not changes and would print again in the middle of a line."""
    for el in list(measure.getElementsByClass(("Clef", "KeySignature", "TimeSignature"))):
        sig = _signature(el)
        if el.offset == 0 and sig and current.get(sig[0]) == sig:
            measure.remove(el)


def merge_pages(page_files: list[Path], dest: Path, log) -> tuple[Path | None, list[Path]]:
    """Concatenate the page/movement files into one continuous score.

    Each file is parsed on its own; a file music21 can't read (OMR sometimes
    emits malformed MusicXML — e.g. ``divisions=0``) is skipped rather than
    sinking the whole merge. Measures are appended onto part 0 and renumbered.

    Returns ``(merged_path_or_None, skipped_files)``.
    """
    try:
        from music21 import converter, spanner, stream
    except Exception as exc:  # noqa: BLE001
        log(f"  music21 unavailable, cannot merge pages ({exc})")
        return None, []

    def _safe_parse(p: Path):
        try:
            return converter.parse(str(p))
        except Exception as exc:  # noqa: BLE001
            log(f"  merge: skipping unreadable {p.name} ({type(exc).__name__})")
            return None

    skipped: list[Path] = []
    master = None
    m_parts: list = []
    for pf in page_files:
        sc = _safe_parse(pf)
        if sc is None:
            skipped.append(pf)
            continue
        parts = list(sc.parts) or [sc]
        if master is None:
            master = sc
            m_parts = list(master.parts) or [master]
            continue
        for idx, part in enumerate(parts):
            measures = list(part.getElementsByClass(stream.Measure))
            if idx < len(m_parts):
                current = _in_force(m_parts[idx].getElementsByClass(stream.Measure))
                if measures:
                    _drop_restated(measures[0], current)
                # slurs, hairpins and text lines live on the part, not in its measures
                spanners = list(part.getElementsByClass(spanner.Spanner))
                for m in measures:
                    m_parts[idx].append(m)
                for sp in spanners:
                    m_parts[idx].insert(0, sp)
            else:
                master.insert(0, part)
                m_parts.append(part)

    if master is None:
        return None, skipped
    try:
        for part in master.parts:
            for i, m in enumerate(part.getElementsByClass(stream.Measure), start=1):
                m.number = i
        master.write("musicxml", fp=str(dest))
        open_wedges(dest)
    except Exception as exc:  # noqa: BLE001
        log(f"  merge: write failed ({exc})")
        return None, skipped
    kept = len(page_files) - len(skipped)
    log(f"  merged {kept}/{len(page_files)} page file(s) -> {dest.name}"
        + (f"; skipped {[p.name for p in skipped]}" if skipped else ""))
    return dest, skipped
