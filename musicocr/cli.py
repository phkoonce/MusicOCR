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
    def g(name):  # `compare` has only a subset of the run flags
        return getattr(args, name, None)

    omr = config.omr
    if g("engine"):
        omr = replace(omr, engine=g("engine"))
    if g("profile"):
        omr = replace(omr, profile=g("profile"))
    if g("merge") is not None:
        omr = replace(omr, merge_pages=g("merge"))
    if g("constant"):
        omr = replace(omr, extra_constants=list(g("constant")))
    if g("small_graces") is not None:
        omr = replace(omr, copisteria=replace(omr.copisteria, small_graces=g("small_graces")))

    pp = config.preprocess
    if g("preprocess") is not None:
        pp = replace(pp, enabled=g("preprocess"))
    if g("deskew") is not None:
        pp = replace(pp, deskew=g("deskew"))
    if g("crop") is not None:
        pp = replace(pp, crop_margins=g("crop"))
    if (g("deskew") or g("crop")) and g("preprocess") is None and not pp.enabled:
        pp = replace(pp, enabled=True)
        print("note: --deskew/--crop turn on the [preprocess] pass")

    tr = config.stages.transpose
    if g("transpose_instrument") is not None:
        tr = replace(tr, enabled=True, instrument=g("transpose_instrument"), semitones=None)
    if g("transpose_semitones") is not None:
        tr = replace(tr, enabled=True, semitones=g("transpose_semitones"), instrument="")
    if g("transpose") is not None:
        tr = replace(tr, enabled=g("transpose"))

    rt = config.stages.retouch
    if g("old_bass_clef") is not None:
        rt = replace(rt, old_bass_clef=g("old_bass_clef"))
    if g("title"):
        rt = replace(rt, title=g("title"))
    if g("composer"):
        rt = replace(rt, composer=g("composer"))

    return replace(config, omr=omr, preprocess=pp,
                   stages=replace(config.stages, transpose=tr, retouch=rt))


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


def cmd_compare(args) -> int:
    from musicocr.compare import run_compare

    engines = list(dict.fromkeys(e.strip() for e in args.engines.split(",") if e.strip()))
    bad = [e for e in engines if e not in ENGINES]
    if not engines or bad:
        print(f"error: unknown engine(s) {bad or args.engines!r}; known: "
              f"{', '.join(sorted(ENGINES))}", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    config = apply_run_overrides(config, args)

    input_pdf = Path(args.input).expanduser().resolve()
    book = _book_name(input_pdf)
    out_root = Path(args.output).expanduser().resolve() if args.output \
        else Path(__file__).resolve().parent.parent / "output"
    ctx = PipelineContext(
        input_pdf=input_pdf, workdir=out_root / book, config=config, book_name=book,
        pages=args.pages.replace(",", " ") if args.pages else None, force=args.force)

    print(f"MusicOCR {__version__}  |  compare: {', '.join(engines)}  |  book: {book}  "
          f"|  workdir: {ctx.workdir}")
    out = run_compare(ctx, engines, to_stage="qa" if args.convert else "validate",
                      keep_going=args.keep_going)
    for n in out["notes"]:
        print(n)
    if not out["ok"]:
        print("\nno engine produced a score", file=sys.stderr)
        return 1
    print(f"\ncompare report: {ctx.workdir / 'compare.md'}")
    return 0


def cmd_combine(args) -> int:
    from musicocr import batch, combine_cli
    from musicocr.marks import MarksOptions

    try:
        config = load_config(args.config)
        spec = batch.load_spec(Path(args.batch))
    except (ConfigError, batch.BatchSpecError, OSError) as exc:
        print(f"combine error: {exc}", file=sys.stderr)
        return 2
    engine = args.engine or (spec.engines[0] if spec.engines else None)
    out_dir = Path(args.output).expanduser() if args.output else \
        spec.output_dir / "_combined" / (engine or "run")
    only = set(args.only.split(",")) if args.only else None
    try:
        marks = MarksOptions.parse(args.normalize_marks) if args.normalize_marks else None
    except ValueError as exc:
        print(f"combine error: {exc}", file=sys.stderr)
        return 2
    combine_cli.run(spec, engine, out_dir, only=only, marks=marks,
                    pdftotext=config.resolve_tool("pdftotext") or "pdftotext",
                    musescore=config.resolve_tool("musescore") if args.convert else None)
    return 0


def cmd_tuplets(args) -> int:
    from musicocr.tuplets import fix_tuplets_file
    total = 0
    for f in args.files:
        path = Path(f).expanduser()
        print(f"{path}:")
        total += fix_tuplets_file(path, flag=not args.no_flag)
    print(f"{total} tuplet run(s) rebuilt")
    return 0


def cmd_marks(args) -> int:
    from musicocr.marks import MarksOptions, normalize_file

    try:
        opts = MarksOptions.parse(args.rules)
    except ValueError as exc:
        print(f"marks error: {exc}", file=sys.stderr)
        return 2
    if args.no_colour:
        opts.mark = None
    src = Path(args.input).expanduser()
    dst = Path(args.output).expanduser() if args.output else src.with_suffix(".marks.musicxml")
    _, report = normalize_file(src, dst, opts)
    print("\n".join(report))
    print(f"wrote {dst}")
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
    r.add_argument("--small-graces", dest="small_graces", action="store_true", default=None,
                   help="copisteria: treat small noteheads as grace notes (timpani/drum ruffs and "
                        "flams; overrides [omr.copisteria] small_graces)")
    r.add_argument("--no-small-graces", dest="small_graces", action="store_false")
    r.add_argument("--old-bass-clef", dest="old_bass_clef", action="store_true", default=None,
                   help="old-notation horn edition: make bass clefs octave-up and raise their notes an octave")
    r.add_argument("--title", metavar="TEXT", help="piece title for the score metadata (drops the misread printed title)")
    r.add_argument("--composer", metavar="TEXT", help="composer for the score metadata (drops the misread printed line)")
    r.add_argument("--from", dest="from_stage", choices=STAGE_ORDER, help="start stage")
    r.add_argument("--to", dest="to_stage", choices=STAGE_ORDER, help="end stage (inclusive)")
    r.add_argument("--keep-going", action="store_true",
                   help="continue after a stage fails")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser(
        "compare", help="run several OMR engines on one PDF and compare them",
        description="Ingest (rasterise/deskew/crop) once, then run each engine through "
                    "extract/normalize/transpose/validate in <output>/<book>/engines/<engine>/ "
                    "and write compare.md/compare.json to <output>/<book>/.")
    c.add_argument("input", help="input PDF")
    c.add_argument("--engines", required=True, metavar="A,B",
                   help="comma-separated engines, e.g. homr,copisteria (unavailable ones "
                        "are skipped)")
    c.add_argument("--output", help="output root dir (default: ./output)")
    c.add_argument("--pages", help="page selection, e.g. '1,4-5'")
    c.add_argument("--force", action="store_true", help="re-run OMR even if output exists")
    c.add_argument("--keep-going", action="store_true", help="continue after a stage fails")
    c.add_argument("--convert", action="store_true",
                   help="also run convert and qa per engine (slow; default stops at validate)")
    c.add_argument("--preprocess", dest="preprocess", action="store_true", default=None,
                   help="force scan clean-up on")
    c.add_argument("--no-preprocess", dest="preprocess", action="store_false",
                   help="force scan clean-up off")
    c.add_argument("--deskew", dest="deskew", action="store_true", default=None)
    c.add_argument("--no-deskew", dest="deskew", action="store_false")
    c.add_argument("--crop", dest="crop", action="store_true", default=None)
    c.add_argument("--no-crop", dest="crop", action="store_false")
    c.add_argument("--transpose-instrument", metavar="NAME",
                   help="transpose each engine's score by this instrument's table entry")
    c.add_argument("--transpose-semitones", type=int, metavar="N")
    c.set_defaults(func=cmd_compare)

    cb = sub.add_parser(
        "combine", help="combine a batch's per-part results into one score aligned to the printed bars",
        description="Derives bar structure from the part PDFs' text layer and key/meter from the "
                    "parts themselves (no hand-written spec); see musicocr/combine.py.")
    cb.add_argument("--batch", required=True, help="batch TOML (parts, labels, instruments)")
    cb.add_argument("--engine", help="which engine's results to combine (default: the batch's first)")
    cb.add_argument("--output", help="directory for the combined score (default: <batch output>/_combined/<engine>)")
    cb.add_argument("--only", help="comma-separated part ids/keys to include")
    cb.add_argument("--convert", action="store_true", help="also write .mscz via MuseScore")
    cb.add_argument("--normalize-marks", nargs="?", const="dedupe,hairpins", metavar="RULES",
                    help="make dynamics/hairpins consistent across parts (musicocr/marks.py); RULES is a "
                         "comma list of dedupe, hairpins, fill, or all (default when given bare: "
                         "dedupe,hairpins; fill is opt-in)")
    cb.set_defaults(func=cmd_combine)

    mk = sub.add_parser(
        "marks", help="dedupe dynamics / extend truncated hairpins / fill section dynamics in a MusicXML file",
        description="The optional marks pass of `combine`, run on an existing partwise MusicXML file.")
    mk.add_argument("input", help="partwise MusicXML file")
    mk.add_argument("-o", "--output", help="output file (default: <input>.marks.musicxml)")
    mk.add_argument("--rules", default="dedupe,hairpins", help="comma list of dedupe, hairpins, fill, or all")
    mk.add_argument("--no-colour", action="store_true", help="do not colour inserted marks")
    mk.set_defaults(func=cmd_marks)

    tp = sub.add_parser(
        "tuplets", help="rebuild inconsistent tuplets (mixed ratios, misaligned brackets) in MusicXML files, in place",
        description="The tuplet clean-up of the OMR stages (musicocr/tuplets.py), run on existing files.")
    tp.add_argument("files", nargs="+", help="MusicXML files to rewrite in place")
    tp.add_argument("--no-flag", action="store_true", help="do not colour/annotate runs whose timing was guessed")
    tp.set_defaults(func=cmd_tuplets)

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
