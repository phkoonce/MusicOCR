"""Flag measures whose note+rest durations don't add up to the time signature.

This is the practical answer to homr's other main failure mode alongside
multi-rests: a note misclassified with the wrong duration (e.g. a half note
read as a whole note) doesn't get its own error message anywhere -- it just
makes that measure's total duration wrong. There's no way to know which note
is at fault without the scan, but *finding the measure* is fully mechanical:
sum each measure's content and compare it to what its time signature says it
should be.

This is exactly the check musicocr's own `validate` stage already does for
Audiveris output (see musicocr/stages/validate.py) -- reused here as-is
against homr's MusicXML rather than reimplemented, so it stays in sync with
whatever tolerances that stage already tunes (pickup measures, short final
measures, etc).

Needs the main project's venv (music21), not homr's:
    .venv/bin/python omr-eval/check_measure_duration.py PAGE.musicxml [PAGE2.musicxml ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    try:
        from music21 import converter, stream
    except ImportError:
        print(
            "music21 not importable -- run this with the main project's venv:\n"
            "  .venv/bin/python omr-eval/check_measure_duration.py ...",
            file=sys.stderr,
        )
        sys.exit(2)

    from musicocr.stages.validate import _check_part

    any_findings = False
    for path in args.files:
        print(f"{path}")
        score = converter.parse(str(path))
        parts = list(score.getElementsByClass(stream.Part)) or [score]
        findings: list[dict] = []
        for i, part in enumerate(parts):
            _check_part(part, i, findings)

        duration_findings = [f for f in findings if f["code"] == "measure-duration"]
        if not duration_findings:
            print("  no measure-duration mismatches")
            continue
        any_findings = True
        for f in duration_findings:
            print(f"  {f['severity']}: {f['message']}")

    if any_findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
