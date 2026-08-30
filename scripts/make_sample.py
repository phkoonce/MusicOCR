"""Render a small known score to samples/sample.pdf for a no-scan smoke test.

Builds a short two-part piece with music21, then renders it to PDF with
MuseScore. Running the pipeline on this PDF exercises every stage without
needing a real scanned score.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from musicocr.config import load_config  # noqa: E402
from musicocr.util import run_cmd_lenient  # noqa: E402


def build_score(path: Path):
    from music21 import chord, clef, key, meter, note, stream, tempo

    sc = stream.Score()

    treble = stream.Part(id="treble")
    treble.append(clef.TrebleClef())
    treble.append(key.KeySignature(0))
    treble.append(meter.TimeSignature("4/4"))
    treble.append(tempo.MetronomeMark(number=96))
    for p in ["C5", "D5", "E5", "F5", "G5", "A5", "G5", "F5",
              "E5", "D5", "C5", "E5", "G5", "E5", "C5"]:
        treble.append(note.Note(p, quarterLength=1))
    treble.append(note.Note("C5", quarterLength=1))

    bass = stream.Part(id="bass")
    bass.append(clef.BassClef())
    bass.append(key.KeySignature(0))
    bass.append(meter.TimeSignature("4/4"))
    for _ in range(4):
        bass.append(chord.Chord(["C3", "G3"], quarterLength=2))
        bass.append(chord.Chord(["G2", "D3"], quarterLength=2))

    sc.insert(0, treble)
    sc.insert(0, bass)
    xml = path.with_suffix(".musicxml")
    sc.write("musicxml", fp=str(xml))
    return xml


def main() -> int:
    cfg = load_config()
    samples = REPO / "samples"
    samples.mkdir(exist_ok=True)
    xml = build_score(samples / "sample.pdf")

    mscore = cfg.resolve_tool("musescore")
    if not mscore:
        print(f"MuseScore not found at {cfg.musescore!r}", file=sys.stderr)
        return 1
    pdf = samples / "sample.pdf"
    if pdf.exists():
        pdf.unlink()
    # MuseScore 4 on macOS writes the file then often aborts on shutdown; judge
    # success by the output file rather than the exit code.
    run_cmd_lenient([mscore, "-f", "-o", str(pdf), str(xml)], timeout=120, log=print)
    if not (pdf.exists() and pdf.stat().st_size > 0):
        print("MuseScore failed to render sample.pdf", file=sys.stderr)
        return 1
    print(f"wrote {pdf}")
    print(f"now run:  python -m musicocr run {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
