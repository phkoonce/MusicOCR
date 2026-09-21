"""Command-line entry point: ``python -m musicocr <run|doctor>``."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from musicocr import __version__
from musicocr.config import ConfigError, load_config
from musicocr.pipeline import PipelineContext, STAGE_ORDER, run_pipeline
from musicocr.report import write_reports


def _book_name(pdf: Path) -> str:
    return pdf.stem.replace(" ", "_")


def cmd_run(args) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.engine:
        config.omr_engine = args.engine
    if args.profile:
        config.omr_profile = args.profile
    if args.merge is not None:
        config.omr_merge_pages = args.merge

    pp = dict(config.preprocess)
    if args.preprocess is not None:
        pp["enabled"] = args.preprocess
    if args.deskew is not None:
        pp["deskew"] = args.deskew
    if args.crop is not None:
        pp["crop_margins"] = args.crop
    if (args.deskew or args.crop) and args.preprocess is None and not pp.get("enabled"):
        pp["enabled"] = True
        print("note: --deskew/--crop turn on the [preprocess] pass")
    config.preprocess = pp
    try:
        config.constants_for_profile()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    input_pdf = Path(args.input).expanduser().resolve()
    book = _book_name(input_pdf)
    out_root = Path(args.output).expanduser().resolve() if args.output \
        else Path(__file__).resolve().parent.parent / "output"
    workdir = out_root / book

    ctx = PipelineContext(
        input_pdf=input_pdf,
        workdir=workdir,
        config=config,
        book_name=book,
        pages=args.pages.replace(",", " ") if args.pages else None,
        extra_constants=list(args.constant or []),
        force=args.force,
    )

    print(f"MusicOCR {__version__}  |  book: {book}  |  workdir: {workdir}")
    results = run_pipeline(ctx, args.from_stage, args.to_stage, args.keep_going)

    try:
        json_path, md_path = write_reports(ctx, results)
        print(f"\nreport: {md_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not write report: {exc})", file=sys.stderr)

    failed = [r for r in results if r.status == "failed"]
    paused = [r for r in results if r.status == "paused"]
    if failed:
        print(f"\nFAILED at: {', '.join(r.name for r in failed)}", file=sys.stderr)
        return 1
    if paused:
        print(f"\nPAUSED at: {', '.join(r.name for r in paused)}")
        return 3
    print("\nOK")
    return 0


def _check(label, path, version_args=None):
    if not path:
        print(f"  ✗ {label}: not found")
        return False
    ver = ""
    if version_args:
        try:
            out = subprocess.run([path, *version_args], capture_output=True,
                                 text=True, timeout=30)
            ver = (out.stdout or out.stderr).strip().splitlines()[0] if \
                (out.stdout or out.stderr).strip() else ""
        except Exception:  # noqa: BLE001
            ver = ""
    print(f"  ✓ {label}: {path}" + (f"  ({ver})" if ver else ""))
    return True


def cmd_doctor(args) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(f"MusicOCR {__version__}")
    print(f"config: {config.path}")
    print(f"OMR engine: {config.omr_engine}")
    print("\ntools:")
    ok = True
    engine_tool = "homr_python" if config.omr_engine == "homr" else "audiveris"
    ok &= _check("homr (venv python)", config.resolve_tool("homr_python"))
    ok &= _check("Audiveris", config.resolve_tool("audiveris"), ["-version"])
    ok &= _check("MuseScore", config.resolve_tool("musescore"), ["--version"])
    ok &= _check("pdfinfo", config.resolve_tool("pdfinfo"), ["-v"])
    ok &= _check("pdftoppm", config.resolve_tool("pdftoppm"), ["-v"])
    if not config.resolve_tool(engine_tool):
        print(f"  ✗ configured engine {config.omr_engine!r} is not available")
        ok = False

    print("\npython deps:")
    try:
        import music21  # noqa: F401
        print(f"  ✓ music21: {music21.__version__}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ music21: {exc}")
        ok = False

    print("\nprofiles:", ", ".join(sorted(config.profiles)))
    print("stages:  ", " -> ".join(STAGE_ORDER),
          f"\n         (correct: {'enabled' if config.correct_enabled else 'disabled'})")
    print("formats: ", ", ".join(config.formats))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="musicocr", description=__doc__)
    p.add_argument("--config", help="path to config.toml (default: repo config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the pipeline on a PDF")
    r.add_argument("input", help="input PDF")
    r.add_argument("--output", help="output root dir (default: ./output)")
    r.add_argument("--pages", help="page selection, e.g. '1,4-5' (Audiveris -sheets)")
    r.add_argument("--engine", choices=["homr", "audiveris"],
                   help="OMR engine (overrides config [omr] engine, default: homr)")
    r.add_argument("--profile", help="OMR profile from config.toml (Audiveris only)")
    r.add_argument("--constant", action="append", metavar="k=v",
                   help="extra Audiveris constant (repeatable, Audiveris only)")
    r.add_argument("--force", action="store_true", help="re-run OMR even if output exists")
    r.add_argument("--merge", dest="merge", action="store_true", default=None,
                   help="stitch pages into one score (overrides config [omr] merge_pages)")
    r.add_argument("--no-merge", dest="merge", action="store_false",
                   help="keep pages separate — for a book of independent parts or songs")
    r.add_argument("--preprocess", dest="preprocess", action="store_true", default=None,
                   help="force scan clean-up on (overrides config [preprocess])")
    r.add_argument("--no-preprocess", dest="preprocess", action="store_false",
                   help="force scan clean-up off")
    r.add_argument("--deskew", dest="deskew", action="store_true", default=None,
                   help="preprocess: rotate each page so the staves are level (camera scans)")
    r.add_argument("--no-deskew", dest="deskew", action="store_false",
                   help="preprocess: skip the deskew pass")
    r.add_argument("--crop", dest="crop", action="store_true", default=None,
                   help="preprocess: trim the header/footer text band down to the staves")
    r.add_argument("--no-crop", dest="crop", action="store_false",
                   help="preprocess: skip the margin crop")
    r.add_argument("--from", dest="from_stage", choices=STAGE_ORDER, help="start stage")
    r.add_argument("--to", dest="to_stage", choices=STAGE_ORDER, help="end stage (inclusive)")
    r.add_argument("--keep-going", action="store_true",
                   help="continue after a stage fails")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="check tools and dependencies")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
