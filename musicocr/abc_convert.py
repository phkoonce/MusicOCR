"""ABC -> MusicXML for engines that emit ABC notation (LEGATO 2).

music21's ABC parser is tried first; LEGATO's output can use constructs it
rejects, in which case a clear error comes back rather than a half-written file.
"""
from __future__ import annotations

from pathlib import Path


class AbcConversionError(RuntimeError):
    pass


def abc_to_musicxml(abc_path: Path, xml_path: Path) -> None:
    """Parse ``abc_path`` and write MusicXML to ``xml_path``. Raises AbcConversionError."""
    text = Path(abc_path).read_text()
    if not text.strip():
        raise AbcConversionError("empty ABC output")
    if not any(line.startswith("X:") for line in text.splitlines()[:5]):
        # music21 needs a reference-number header line to recognise a tune.
        text = "X:1\n" + text
    try:
        from music21 import converter
        score = converter.parse(text, format="abc")
        score.write("musicxml", fp=str(xml_path))
    except Exception as exc:  # noqa: BLE001 -- music21 raises a wide variety
        raise AbcConversionError(f"music21 could not convert ABC: {type(exc).__name__}: {exc}") from exc
