"""Merge per-page MusicXML files into one continuous score (public API).

Used by the extract stage and by omr-eval/concat_pages.py.
"""
from __future__ import annotations

from pathlib import Path


def merge_pages(page_files: list[Path], dest: Path, log) -> tuple[Path | None, list[Path]]:
    """Concatenate the page/movement files into one continuous score.

    Each file is parsed on its own; a file music21 can't read (OMR sometimes
    emits malformed MusicXML — e.g. ``divisions=0``) is skipped rather than
    sinking the whole merge. Measures are appended onto part 0 and renumbered.

    Returns ``(merged_path_or_None, skipped_files)``.
    """
    try:
        from music21 import converter, stream
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
                for m in measures:
                    m_parts[idx].append(m)
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
    except Exception as exc:  # noqa: BLE001
        log(f"  merge: write failed ({exc})")
        return None, skipped
    kept = len(page_files) - len(skipped)
    log(f"  merged {kept}/{len(page_files)} page file(s) -> {dest.name}"
        + (f"; skipped {[p.name for p in skipped]}" if skipped else ""))
    return dest, skipped
