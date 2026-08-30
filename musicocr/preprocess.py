"""Rasterise a PDF page and clean it up for OMR.

Low-resolution / grey phone scans confuse Audiveris badly. Rendering the page at
~300 DPI and running autocontrast + an unsharp-mask pass makes note stems, beams
and staff lines crisp enough to track — on the test scan this recovered ~8x more
music. Tuned via ``scripts/experiment.py``; controlled by ``[preprocess]`` in
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

    @classmethod
    def from_dict(cls, d: dict) -> "PreprocessOpts":
        f = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**f)


def prepare_page(pdf: Path, page: int, dst_png: Path, opts: PreprocessOpts,
                 pdftoppm: str, log=None) -> Path | None:
    """Render ``page`` of ``pdf`` to ``dst_png`` with the configured clean-up.

    Returns the path on success, or None if rasterisation failed.
    """
    from PIL import Image, ImageOps, ImageFilter

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
    if opts.unsharp:
        im = im.filter(ImageFilter.UnsharpMask(
            radius=opts.unsharp_radius, percent=opts.unsharp_percent, threshold=2))
    im.save(dst_png)
    return dst_png
