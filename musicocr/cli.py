"""Command-line entry point: ``python -m musicocr <run|transpose|instruments|doctor>``."""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from musicocr import __version__
from musicocr.config import Config, ConfigError, load_config
from musicocr.engines import ENGINES
from musicocr.pipeline import PipelineContext, STAGE_ORDER, run_pipeline
from musicocr.report import write_reports


def _book_name(pdf: Path) -> str:
    return pdf.stem.replace(" ", "_")


def apply_run_overrides(config: Config, args) -> Config:
    """Return a copy of ``config`` with the ``run`` CLI flags applied."""
    omr = config.omr
    if args.engine:
        omr = replace(omr, engine=args.engine)
    if args.profile:
        omr = replace(omr, profile=args.profile)
    if args.merge is not None:
        omr = replace(omr, merge_pages=args.merge)
    if args.constant:
        omr = replace(omr, extra_constants=list(args.constant))

    pp = config.preprocess
    if args.preprocess is not None:
        pp = replace(pp, enabled=args.preprocess)
    if args.deskew is not None:
        pp = replace(pp, deskew=args.deskew)
    if args.crop is not None:
        pp = replace(pp, crop_margins=args.crop)
    if (args.deskew or args.crop) and args.preprocess is None and not pp.enabled:
        pp = replace(pp, enabled=True)
        print("note: --deskew/--crop turn on the [preprocess] pass")

    tr = config.stages.transpose
    if args.transpose_instrument is not None:
        tr = replace(tr, enabled=True, instrument=args.transpose_instrument, semitones=None)
    if args.transpose_semitones is not None:
        tr = replace(tr, enabled=True, semitones=args.transpose_semitones, instrument="")
    if args.transpose is not None:
        tr = replace(tr, enabled=args.transpose)

    return replace(config, omr=omr, preprocess=pp,
                   stages=replace(config.stages, transpose=tr))


def cmd_run(args) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    config = apply_run_overrides(config, args)
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


def cmd_transpose(args) -> int:
    from musicocr.transpose import transpose_file

    src = Path(args.input).expanduser().resolve()
    dst = Path(args.output).expanduser().resolve() if args.output \
        else src.with_name(src.stem + ".transposed.musicxml")
    try:
        transpose_file(src, dst, semitones=args.semitones,
                       instrument=args.instrument, clef=args.clef)
    except (ValueError, ET.ParseError, OSError) as exc:
        print(f"transpose error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {dst}")
    return 0


def cmd_instruments(args) -> int:
    from musicocr.instruments import ALIASES, TRANSPOSITIONS

    for name, spec in sorted(TRANSPOSITIONS.items()):
        shown = f"{spec:+d}" if isinstance(spec, int) else \
            ", ".join(f"{clef} {n:+d}" for clef, n in spec.items())
        aliases = sorted(a for a, c in ALIASES.items() if c == name)
        print(f"{name:16} {shown}" + (f"   (also: {', '.join(aliases)})" if aliases else ""))
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
    print(f"OMR engine: {config.omr.engine}")
    print("\ntools:")
    ok = True
    current = ENGINES[config.omr.engine]
    for eng in ENGINES.values():
        if not eng.tool:
            print(f"  - {eng.label}: {eng.note or 'unavailable'}")
            continue
        found = _check(eng.label, config.resolve_tool(eng.tool),
                       ["-version"] if eng.name == "audiveris" else None)
        if eng is current:
            ok &= found   # only the configured engine must be present
    ok &= _check("MuseScore", config.resolve_tool("musescore"), ["--version"])
    ok &= _check("pdfinfo", config.resolve_tool("pdfinfo"), ["-v"])
    ok &= _check("pdftoppm", config.resolve_tool("pdftoppm"), ["-v"])
    if not current.available:
        print(f"  ✗ configured engine {current.name!r} is not available: {current.note}")
        ok = False

    print("\npython deps:")
    try:
        import music21  # noqa: F401
        print(f"  ✓ music21: {music21.__version__}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ music21: {exc}")
        ok = False

    print("\nprofiles:", ", ".join(sorted(config.omr.profiles)))
    print("stages:  ", " -> ".join(STAGE_ORDER),
          f"\n         (correct: {'enabled' if config.stages.correct_enabled else 'disabled'}"
          f", normalize: {'enabled' if config.stages.normalize_enabled else 'disabled'}"
          f", transpose: {'enabled' if config.stages.transpose.enabled else 'disabled'})")
    if config.omr.engine == "homr":
        print(f"homr:     fix_multirest={'on' if config.omr.homr.fix_multirest else 'off'}")
    print("formats: ", ", ".join(config.output.formats))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="musicocr", description=__doc__)
    p.add_argument("--config", help="path to config.toml (default: repo config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the pipeline on a PDF")
    r.add_argument("input", help="input PDF")
    r.add_argument("--output", help="output root dir (default: ./output)")
    r.add_argument("--pages", help="page selection, e.g. '1,4-5' (Audiveris -sheets)")
    r.add_argument("--engine", choices=sorted(ENGINES),
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
    r.add_argument("--transpose", dest="transpose", action="store_true", default=None,
                   help="enable the transpose stage (config [stages.transpose])")
    r.add_argument("--no-transpose", dest="transpose", action="store_false",
                   help="disable the transpose stage")
    r.add_argument("--transpose-instrument", metavar="NAME",
                   help="transpose by this instrument's table entry (implies --transpose)")
    r.add_argument("--transpose-semitones", type=int, metavar="N",
                   help="transpose by N semitones (implies --transpose)")
    r.add_argument("--from", dest="from_stage", choices=STAGE_ORDER, help="start stage")
    r.add_argument("--to", dest="to_stage", choices=STAGE_ORDER, help="end stage (inclusive)")
    r.add_argument("--keep-going", action="store_true",
                   help="continue after a stage fails")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser(
        "transpose",
        help="transpose a MusicXML file (written -> concert pitch), keys included",
        description="Shift pitches and key signatures. Negative is down; OMR'd parts are "
                    "written pitch, so e.g. a horn in F is -7. With neither --semitones nor "
                    "--instrument, each part is identified from its <part-name>.")
    t.add_argument("input", help="input MusicXML")
    t.add_argument("-o", "--output", help="output path (default: <input>.transposed.musicxml)")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--semitones", type=int, help="shift every part by this many semitones")
    g.add_argument("--instrument", help="look up the shift in musicocr/instruments.py")
    t.add_argument("--clef", choices=["treble", "bass", "c"],
                   help="clef family for clef-dependent instruments (default: each part's first clef)")
    t.set_defaults(func=cmd_transpose)

    i = sub.add_parser("instruments", help="list the instrument transposition table")
    i.set_defaults(func=cmd_instruments)

    d = sub.add_parser("doctor", help="check tools and dependencies")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
