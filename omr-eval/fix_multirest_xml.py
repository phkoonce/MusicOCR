"""CLI wrapper around ``musicocr.homr_fixes.fix_multirest_file``.

The fix itself now also runs automatically inside the main pipeline's homr
stage (``[omr.homr] fix_multirest``, default on) -- see
``musicocr/stages/omr_homr.py``. Kept here as a standalone entry point for
ad-hoc eval work against files outside a pipeline run (e.g. output from a
homr version/config not yet wired into the pipeline). See musicocr/homr_fixes.py
for the full explanation of the bug this works around, and omr-eval/README.md
for the failure-mode writeup.

Usage:
    python3 omr-eval/fix_multirest_xml.py PAGE.musicxml [PAGE2.musicxml ...]

Rewrites each file in place by default; pass --suffix to write PAGE.fixed.musicxml
instead. Only handles single-staff parts (this eval track is scoped to
monophonic brass/woodwind parts) -- prints a warning and skips any part with
more than one <staff> value rather than guessing.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musicocr.homr_fixes import fix_multirest_file  # noqa: E402


def fix_file(path: Path, suffix: bool) -> None:
    print(f"{path}")
    out_path = path.with_suffix(".fixed.musicxml") if suffix else path
    if suffix:
        shutil.copyfile(path, out_path)
        fixed = fix_multirest_file(out_path, log=lambda m: print(m))
    else:
        fixed = fix_multirest_file(path, log=lambda m: print(m))

    if fixed:
        print(f"  wrote {out_path} ({fixed} measure(s) fixed)")
    else:
        print("  nothing to fix")
        if suffix:
            out_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--suffix",
        action="store_true",
        help="write to PAGE.fixed.musicxml instead of overwriting the input",
    )
    args = parser.parse_args()

    for path in args.files:
        fix_file(path, args.suffix)


if __name__ == "__main__":
    main()
