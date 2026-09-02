"""Rasterise a PDF page and clean it up for OMR.

Low-resolution / grey phone scans confuse Audiveris badly. Rendering the page at
~300 DPI and running autocontrast + an unsharp-mask pass makes note stems, beams
and staff lines crisp enough to track — on the test scan this recovered ~8x more
music. Two further passes help camera scans specifically:

* **deskew** — a projection-profile search that rotates the page so the staff
  lines run truly horizontal. Audiveris tolerates only small skew; a phone photo
  is often 1–3 degrees off.
* **crop_margins** — trim the stray text lines above and below the music: the
  running-header line (``… - 3rd Trombone`` + page number) and the footer
  (copyright, URL, logos), which otherwise become stray text symbols in the
  export. Deliberately conservative — it leaves the big centred title frame on a
  first page alone rather than risk clipping the first system.

Tuned via ``scripts/experiment.py``; controlled by ``[preprocess]`` in
config.toml.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from musicocr.util import run_cmd_lenient


@dataclass
class PreprocessOpts:
    enabled: bool = False
    dpi: int = 300
    autocontrast: bool = True
    autocontrast_cutoff: int = 1
    unsharp: bool = True
    unsharp_radius: float = 2.0
    unsharp_percent: int = 150
    median: int = 0            # 0 = off; else median-filter window (denoise)
    deskew: bool = False       # projection-profile rotation to level the staves
    max_skew: float = 5.0      # only correct skew within +/- this many degrees
    crop_margins: bool = False  # trim the page down to the staff band
    crop_pad_frac: float = 0.035  # keep this much page-height whitespace around it

    @classmethod
    def from_dict(cls, d: dict) -> "PreprocessOpts":
        f = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**f)


# --- geometry helpers -------------------------------------------------------
#
# Both work on a boolean "ink" array (True = dark pixel) and are cheap because
# they run on a downscaled copy of the page, not the full 350-DPI raster.

def _ink_array(im, max_width: int = 1000):
    """Downscaled boolean array, True where the page is dark (ink)."""
    import numpy as np
    from PIL import Image

    g = im.convert("L")
    if g.width > max_width:
        h = max(1, round(g.height * max_width / g.width))
        g = g.resize((max_width, h), Image.BILINEAR)
    a = np.asarray(g, dtype=np.uint8)
    # Otsu-ish: midpoint between the two dominant grey levels is overkill here;
    # a fixed cut at 60% of the dynamic range is robust for autocontrasted scans.
    lo, hi = int(a.min()), int(a.max())
    cut = lo + 0.6 * (hi - lo) if hi > lo else 128
    return a < cut


def _estimate_skew(im, max_skew: float) -> float:
    """Angle (degrees) the page is rotated by, via horizontal-projection variance.

    Staff lines, beams and barlines make sheet music strongly horizontal. When
    the page is level, summing ink per row gives a spiky profile (big values on
    staff-line rows, near-zero between systems); tilt smears it flat. So the
    angle that maximises the variance of that row-sum is the deskew angle.
    """
    import numpy as np
    from PIL import Image

    ink = _ink_array(im, max_width=800)
    src = Image.fromarray((~ink).astype(np.uint8) * 255)  # white bg for rotate fill

    def score(angle: float) -> float:
        rot = src.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=255)
        dark = np.asarray(rot, dtype=np.int16) < 128
        rows = dark.sum(axis=1).astype(np.float64)
        return float(np.var(rows))

    coarse = np.arange(-max_skew, max_skew + 0.5, 0.5)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.5, best + 0.5 + 0.1, 0.1)
    best = max(fine, key=score)
    return round(float(best), 2)


def _staff_band(im, pad_frac: float) -> tuple[int, int] | None:
    """(top, bottom) pixel rows to keep, or None if there's nothing safe to trim.

    On a low-quality scan the staff lines themselves are too broken to detect,
    but the wide blank gaps around the running header and footer survive. Split
    the page into content blocks on those gaps and trim only the unambiguous,
    low-risk cases:

    * a **stranded top block** — short (a title / part-name / tempo line), high
      up, with clear blank space between it and the music below;
    * a **stranded footer block** — short, hard against the bottom edge.

    The big centred title *frame* on a first page is deliberately left alone:
    it often abuts the first system with no real gap, and clipping a system to
    save a ten-second manual delete is a bad trade. The cut is also hard-clamped
    so a misfire can't remove more than a sliver of music.
    """
    import numpy as np

    ink = _ink_array(im, max_width=1000)
    h = ink.shape[0]
    k = max(1, h // 120)
    cov = np.convolve(ink.mean(axis=1), np.ones(k) / k, mode="same")
    content = np.flatnonzero(cov > 0.015)
    if content.size < 20:
        return None

    parts = np.split(content, np.flatnonzero(np.diff(content) > round(h * 0.025)) + 1)
    blocks = [(int(p[0]), int(p[-1])) for p in parts if p.size]
    if len(blocks) < 2:
        return None

    pad = round(h * pad_frac)
    top, bot = 0, h
    # stranded top line: short block, ends in the top 15%, real gap to the music
    if (blocks[0][1] - blocks[0][0]) < h * 0.09 and blocks[0][1] < h * 0.15 \
            and (blocks[1][0] - blocks[0][1]) > h * 0.03:
        top = blocks[1][0] - pad
    # stranded footer: short block, starts in the bottom 10%, music above it
    if len(blocks) >= 3 and (blocks[-1][1] - blocks[-1][0]) < h * 0.06 \
            and blocks[-1][0] > h * 0.90:
        bot = blocks[-2][1] + pad

    top = max(0, min(top, round(h * 0.18)))       # never cut > 18% off the top
    bot = min(h, max(bot, round(h * 0.92)))       # never cut > 8% off the bottom
    if (bot - top) / h > 0.97:
        return None

    scale = im.height / h
    return round(top * scale), round(bot * scale)


def prepare_page(pdf: Path, page: int, dst_png: Path, opts: PreprocessOpts,
                 pdftoppm: str, log=None) -> Path | None:
    """Render ``page`` of ``pdf`` to ``dst_png`` with the configured clean-up.

    Returns the path on success, or None if rasterisation failed.
    """
    from PIL import Image, ImageOps, ImageFilter

    def _say(msg: str) -> None:
        if log:
            log(msg)

    dst_png.parent.mkdir(parents=True, exist_ok=True)
    stem = dst_png.with_suffix("")
    run_cmd_lenient([pdftoppm, "-png", "-r", str(opts.dpi),
                     "-f", str(page), "-l", str(page), "-singlefile",
                     str(pdf), str(stem)], timeout=120, log=log)
    raw = stem.with_suffix(".png")
    if not raw.exists():
        return None

    im = Image.open(raw).convert("L")
    if opts.median and opts.median > 1:
        im = im.filter(ImageFilter.MedianFilter(size=opts.median))
    if opts.autocontrast:
        im = ImageOps.autocontrast(im, cutoff=opts.autocontrast_cutoff)

    if opts.deskew:
        try:
            angle = _estimate_skew(im, opts.max_skew)
        except Exception as exc:  # noqa: BLE001
            angle = 0.0
            _say(f"  preprocess p{page}: deskew failed ({type(exc).__name__}), skipped")
        if abs(angle) >= 0.2:
            im = im.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
            _say(f"  preprocess p{page}: deskew {angle:+.2f}°")

    if opts.crop_margins:
        try:
            band = _staff_band(im, opts.crop_pad_frac)
        except Exception as exc:  # noqa: BLE001
            band = None
            _say(f"  preprocess p{page}: crop failed ({type(exc).__name__}), skipped")
        if band:
            top, bot = band
            kept = (bot - top) / im.height
            im = im.crop((0, top, im.width, bot))
            _say(f"  preprocess p{page}: cropped margins, kept rows {top}–{bot} "
                 f"({kept:.0%} of page height)")

    if opts.unsharp:
        im = im.filter(ImageFilter.UnsharpMask(
            radius=opts.unsharp_radius, percent=opts.unsharp_percent, threshold=2))
    im.save(dst_png)
    return dst_png
