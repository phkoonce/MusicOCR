"""Sweep Audiveris settings on a SINGLE page and compare the results.

Why: on a poor scan the default settings can miss almost everything. This runs
one page through a list of named configs (constants and/or image
pre-processing), then reports, per config:

  * whether MusicXML was exported at all
  * Audiveris's measured scale (interline / beam height) and staff/system counts
  * log symptoms: unrecognised header clefs, rhythm ("No timeOffset") failures,
    dangling clef/key alterations
  * music21 view of the export: parts, measures, notes, and how many measures
    have the wrong beat count
  * a MuseScore render (PNG) so you can eyeball it

Usage:
    python scripts/experiment.py "input/Frozen Bass Trombone 1.pdf" --page 1
    python scripts/experiment.py INPUT.pdf --page 1 --only baseline,poor-input

Edit EXPERIMENTS below to add/adjust configs. Results land in
    output/<book>/experiments/p<NN>/
with a summary.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from musicocr.config import load_config          # noqa: E402
from musicocr.util import run_cmd_lenient          # noqa: E402

SW = "org.audiveris.omr.sheet.ProcessingSwitches"
FILT = "org.audiveris.omr.image.FilterDescriptor"
ADAPT = "org.audiveris.omr.image.AdaptiveDescriptor"
GLOB = "org.audiveris.omr.image.GlobalDescriptor"


@dataclass
class Experiment:
    name: str
    constants: list[str] = field(default_factory=list)
    # image pre-processing (applied to a rasterised copy of the page):
    raster_dpi: int | None = None     # rasterise the PDF page at this DPI, feed the PNG
    upscale: float | None = None      # scale the rasterised page by this factor
    autocontrast: bool = False        # PIL autocontrast
    autocontrast_cutoff: int = 1      # percent clipped off each end
    unsharp: bool = False             # PIL UnsharpMask
    unsharp_radius: float = 2.0
    unsharp_percent: int = 150
    median: int | None = None         # median-filter window (denoise) before sharpen
    prebinarize: int | None = None    # hard threshold to 1-bit at this grey level
    deskew: bool = False              # projection-profile rotation to level staves
    max_skew: float = 5.0
    crop_margins: bool = False        # trim header/footer text to the staff band
    crop_pad_frac: float = 0.035
    note: str = ""


# ---------------------------------------------------------------------------
# The sweep. First entry is the reference. Keep each change small so a win is
# attributable. `note` is copied into the report.
# ---------------------------------------------------------------------------
EXPERIMENTS = [
    Experiment("baseline", note="current pipeline defaults (Audiveris on the PDF)"),

    # Round 1 established that autocontrast + unsharp mask on a rasterised page is
    # the big lever on this scan (8x more music recognised). Round 2 tunes it.
    Experiment("sharpen-ref", raster_dpi=300, autocontrast=True, unsharp=True,
               note="round-1 winner: 300 DPI, autocontrast + unsharp(r2,150%)"),

    # -- DPI around the sweet spot --
    Experiment("sharp-250", raster_dpi=250, autocontrast=True, unsharp=True,
               note="250 DPI + sharpen"),
    Experiment("sharp-350", raster_dpi=350, autocontrast=True, unsharp=True,
               note="350 DPI + sharpen"),

    # -- unsharp strength --
    Experiment("sharp-soft", raster_dpi=300, autocontrast=True, unsharp=True,
               unsharp_radius=1.5, unsharp_percent=100, note="gentle unsharp"),
    Experiment("sharp-hard", raster_dpi=300, autocontrast=True, unsharp=True,
               unsharp_radius=3, unsharp_percent=220, note="strong unsharp"),

    # -- contrast handling --
    Experiment("sharp-nocontrast", raster_dpi=300, unsharp=True,
               note="unsharp only, no autocontrast"),
    Experiment("sharp-hicontrast", raster_dpi=300, autocontrast=True, unsharp=True,
               autocontrast_cutoff=3, note="autocontrast cutoff 3 + unsharp"),

    # -- add denoise (median) before sharpen --
    Experiment("sharp-denoise", raster_dpi=300, autocontrast=True, unsharp=True,
               median=3, note="3px median denoise + sharpen"),

    # -- sharpen + Audiveris binarization tweaks (do the filter constants help now?) --
    Experiment("sharp-adaptive-03",
               [f"{ADAPT}.defaultMeanCoeff=0.3"],
               raster_dpi=300, autocontrast=True, unsharp=True,
               note="sharpen + very low adaptive mean coeff"),
    Experiment("sharp-global-150",
               [f"{FILT}.defaultKind=GLOBAL", f"{GLOB}.defaultThreshold=150"],
               raster_dpi=300, autocontrast=True, unsharp=True,
               note="sharpen + global threshold 150"),

    # -- upscale the tiny embedded image instead of rasterising the PDF page --
    Experiment("upscale-3x", upscale=3.0, autocontrast=True, unsharp=True,
               note="3x LANCZOS upscale of the page + sharpen"),

    # -- camera-scan geometry: level the staves, drop the header/footer text --
    Experiment("sharp-deskew", raster_dpi=300, autocontrast=True, unsharp=True,
               deskew=True, note="sharpen + projection-profile deskew"),
    Experiment("sharp-crop", raster_dpi=300, autocontrast=True, unsharp=True,
               crop_margins=True, note="sharpen + trim header/footer to staff band"),
    Experiment("sharp-deskew-crop", raster_dpi=300, autocontrast=True, unsharp=True,
               deskew=True, crop_margins=True, note="sharpen + deskew + crop"),
]


LOG_PATTERNS = {
    "no_header_clef": re.compile(r"no recognized header clef"),
    "no_timeoffset": re.compile(r"No timeOffset"),
    "no_effective": re.compile(r"No effective (clef|key)"),
    "spurious_octave": re.compile(r"No chord linked to OctaveShift"),
}
SCALE_RE = re.compile(r"Scale\{([^}]*)\}")
SYSTEMS_RE = re.compile(r"Page #\d+: (\d+) part.* along (\d+) system")


def _prep_image(cfg: Experiment, pdf: Path, page: int, tools, workdir: Path) -> Path | None:
    """Return a path to a pre-processed PNG, or None to use the PDF directly."""
    if not (cfg.raster_dpi or cfg.upscale or cfg.autocontrast or cfg.unsharp
            or cfg.prebinarize or cfg.deskew or cfg.crop_margins):
        return None
    from PIL import Image, ImageOps, ImageFilter

    from musicocr.preprocess import _estimate_skew, _staff_band

    dpi = cfg.raster_dpi or 300
    stem = workdir / "input"
    run_cmd_lenient([tools["pdftoppm"], "-png", "-r", str(dpi),
                     "-f", str(page), "-l", str(page), "-singlefile",
                     str(pdf), str(stem)], timeout=120)
    png = stem.with_suffix(".png")
    if not png.exists():
        return None

    im = Image.open(png).convert("L")
    if cfg.upscale:
        im = im.resize((int(im.width * cfg.upscale), int(im.height * cfg.upscale)),
                       Image.LANCZOS)
    if cfg.median:
        im = im.filter(ImageFilter.MedianFilter(size=cfg.median))
    if cfg.autocontrast:
        im = ImageOps.autocontrast(im, cutoff=cfg.autocontrast_cutoff)
    if cfg.deskew:
        angle = _estimate_skew(im, cfg.max_skew)
        if abs(angle) >= 0.2:
            im = im.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            print(f"    deskew {angle:+.2f}°")
    if cfg.crop_margins:
        band = _staff_band(im, cfg.crop_pad_frac)
        if band:
            im = im.crop((0, band[0], im.width, band[1]))
            print(f"    cropped to rows {band[0]}–{band[1]}")
    if cfg.unsharp:
        im = im.filter(ImageFilter.UnsharpMask(
            radius=cfg.unsharp_radius, percent=cfg.unsharp_percent, threshold=2))
    if cfg.prebinarize is not None:
        im = im.point(lambda p: 255 if p >= cfg.prebinarize else 0, mode="1")
    out = workdir / "input_prepped.png"
    im.save(out)
    return out


def _parse_log(log: Path) -> dict:
    if not log or not log.exists():
        return {}
    text = log.read_text(errors="replace")
    d = {k: len(p.findall(text)) for k, p in LOG_PATTERNS.items()}
    m = SCALE_RE.search(text)
    d["scale"] = m.group(1).strip() if m else "?"
    systems = SYSTEMS_RE.findall(text)
    d["pages_in_sheet"] = len(systems)
    d["systems"] = sum(int(s) for _, s in systems)
    return d


def _inspect_mxl(mxl: Path) -> dict:
    try:
        from music21 import converter, stream
    except Exception:  # noqa: BLE001
        return {}
    try:
        score = converter.parse(str(mxl))
    except Exception as exc:  # noqa: BLE001
        return {"parse_error": str(exc)[:80]}
    parts = list(score.getElementsByClass(stream.Part)) or [score]
    flat = score.flatten()
    bad = 0
    total_m = 0
    for p in parts:
        for m in p.getElementsByClass(stream.Measure):
            total_m += 1
            try:
                if abs(m.duration.quarterLength - m.barDuration.quarterLength) > 1e-6:
                    bad += 1
            except Exception:  # noqa: BLE001
                pass
    return {
        "parts": len(parts),
        "measures": total_m,
        "notes": len(flat.notes),
        "rests": len(flat.getElementsByClass("Rest")),
        "bad_measures": bad,
    }


def _render(mxl: Path, tools, workdir: Path) -> Path | None:
    pdf = workdir / "render.pdf"
    run_cmd_lenient([tools["musescore"], "-f", "-o", str(pdf), str(mxl)], timeout=180)
    if not pdf.exists():
        return None
    png = workdir / "render"
    run_cmd_lenient([tools["pdftoppm"], "-png", "-r", "130", "-singlefile",
                     str(pdf), str(png)], timeout=120)
    p = png.with_suffix(".png")
    return p if p.exists() else None


def run_experiment(cfg: Experiment, pdf: Path, page: int, tools, root: Path) -> dict:
    wd = root / cfg.name
    wd.mkdir(parents=True, exist_ok=True)
    for old in wd.glob("*"):
        if old.is_file():
            old.unlink()

    src = _prep_image(cfg, pdf, page, tools, wd) or pdf
    cmd = [tools["audiveris"], "-batch", "-transcribe", "-export", "-output", str(wd)]
    for c in cfg.constants:
        cmd += ["-constant", c]
    if src == pdf:
        cmd += ["-sheets", str(page)]
    cmd += ["--", str(src)]

    res = run_cmd_lenient(cmd, timeout=tools["timeout"], log=print)

    logs = sorted(wd.rglob("*.log"), key=lambda p: p.stat().st_mtime)
    mxls = sorted(wd.rglob("*.mxl"), key=lambda p: p.stat().st_mtime)
    row: dict = {
        "name": cfg.name, "note": cfg.note,
        "constants": " ".join(c.split(".")[-1] for c in cfg.constants),
        "export_ok": bool(mxls),
        "audiveris_rc": res.returncode if res else None,
    }
    row.update(_parse_log(logs[-1] if logs else None))
    if mxls:
        row.update(_inspect_mxl(mxls[-1]))
        row["render"] = _render(mxls[-1], tools, wd)
    return row


def write_summary(rows: list[dict], root: Path, pdf: Path, page: int) -> Path:
    cols = ["name", "export_ok", "notes", "measures", "bad_measures",
            "systems", "pages_in_sheet", "scale",
            "no_header_clef", "no_timeoffset", "no_effective"]
    base = next((r for r in rows if r["name"] == "baseline"), None)
    base_notes = base.get("notes", 0) if base else 0

    def verdict(r):
        if not r.get("export_ok"):
            return "✗ no export"
        n = r.get("notes", 0) or 0
        if base_notes and n >= base_notes * 1.5:
            return f"↑↑ {n} notes (+{n - base_notes} vs baseline)"
        if base_notes and n >= base_notes * 1.15:
            return f"↑ {n} notes"
        if base_notes and n <= base_notes * 0.7:
            return f"↓ {n} notes"
        return f"≈ {n} notes"

    L = [f"# Audiveris experiments — {pdf.name} p.{page}", "",
         "The embedded scan is low-resolution, so *notes / measures recognised* "
         "is the headline metric — more of the page captured is better. "
         "`bad_measures` = measures whose beats don't add up. Eyeball the renders "
         "below; the numbers only rank candidates.", "",
         "| " + " | ".join(cols) + " | verdict |",
         "|" + "|".join(["---"] * (len(cols) + 1)) + "|"]
    for r in rows:
        L.append("| " + " | ".join(str(r.get(c, "")) for c in cols)
                 + f" | {verdict(r)} |")
    L += ["", "## Configs", ""]
    for r in rows:
        L.append(f"- **{r['name']}** — {r['note']}"
                 + (f"  `{r['constants']}`" if r.get("constants") else ""))
    L += ["", "## Renders", ""]
    for r in rows:
        L.append(f"### {r['name']}")
        rp = r.get("render")
        if rp:
            L.append("")
            L.append(f"![{r['name']}]({Path(rp).relative_to(root)})")
            L.append("")
        else:
            L.append("\n_no render (export failed)_\n")
    out = root / "summary.md"
    out.write_text("\n".join(L) + "\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--only", help="comma-separated experiment names to run")
    ap.add_argument("--config", help="path to config.toml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.exists():
        print(f"not found: {pdf}", file=sys.stderr)
        return 1

    tools = {
        "audiveris": cfg.resolve_tool("audiveris"),
        "musescore": cfg.resolve_tool("musescore"),
        "pdftoppm": cfg.resolve_tool("pdftoppm"),
        "timeout": cfg.omr_page_timeout,
    }
    missing = [k for k in ("audiveris", "musescore", "pdftoppm") if not tools[k]]
    if missing:
        print(f"missing tools: {missing}", file=sys.stderr)
        return 1

    book = pdf.stem.replace(" ", "_")
    # Kept OUT of output/<book>/ so the pipeline never mistakes these for results.
    root = REPO / "experiments" / book / f"p{args.page:02d}"
    root.mkdir(parents=True, exist_ok=True)

    todo = EXPERIMENTS
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        todo = [e for e in EXPERIMENTS if e.name in want]

    rows = []
    for i, exp in enumerate(todo, 1):
        print(f"\n=== [{i}/{len(todo)}] {exp.name} ===")
        rows.append(run_experiment(exp, pdf, args.page, tools, root))

    summary = write_summary(rows, root, pdf, args.page)
    print(f"\nsummary: {summary}\n")
    print(f"{'name':22} export  notes  measures  bad  systems")
    for r in rows:
        print(f"{r['name']:22} {'yes' if r['export_ok'] else 'NO':6} "
              f"{str(r.get('notes','')):6} {str(r.get('measures','')):9} "
              f"{str(r.get('bad_measures','')):4} {r.get('systems','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
