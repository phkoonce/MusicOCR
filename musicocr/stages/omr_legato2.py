"""Stage 2 (LEGATO 2 engine): page image -> ABC -> MusicXML.

LEGATO 2 (Yang et al., arXiv:2607.05769; weights ``guangyangmusic/legato-2``,
Apache-2.0, gated on Hugging Face) is a full-page OMR model: a YOLO detector
finds the systems on a page and a 0.9B transcriber reads each in turn,
conditioned on the ABC already produced above it. It emits ABC notation, not
MusicXML, so each page is transcribed in the LEGATO 2 venv
(``musicocr/legato2_run.py`` -> ``pNNN.abc``) and converted back here with
music21 (``musicocr.abc_convert``) into ``pages/pNNN/pNNN.musicxml`` -- exactly
where the other raster engines leave theirs, so the rest of the pipeline and
``musicocr compare`` are engine-agnostic.

Set up with ``omr-eval/setup.sh`` (needs ``huggingface-cli login`` and accepting
the model's terms first). Its published benchmarks are on multi-staff scores
(quartets, lieder, piano), not monophonic wind/brass parts, and none of the
Copisteria-style post-fixes apply: what you see is the model's raw reading.
"""
from __future__ import annotations

import os
from pathlib import Path

from musicocr.abc_convert import AbcConversionError, abc_to_musicxml
from musicocr.errors import StageError, StageResult
from musicocr.pipeline import PipelineContext
from musicocr.stages._omr_common import ensure_page_png as _ensure_page_png, run_raster_pages
from musicocr.util import run_cmd_lenient

_RUNNER = Path(__file__).resolve().parent.parent / "legato2_run.py"


def run(ctx: PipelineContext) -> StageResult:
    py = ctx.config.resolve_tool("legato2_python")
    cfg = ctx.config.omr.legato2
    repo = Path(cfg.repo).expanduser()
    if not py:
        raise StageError(
            f"LEGATO 2 python not found ({ctx.config.tools.legato2_python!r}); run "
            "omr-eval/setup.sh or edit config.toml [tools] legato2_python"
        )

    def build_cmd(img: Path, pdir: Path):
        cmd = [py, str(_RUNNER), str(img), "--out", str(pdir / f"{img.stem}.abc"),
               "--model", cfg.model, "--device", cfg.device,
               "--beam-size", str(cfg.beam_size), "--yolo-conf", str(cfg.yolo_conf)]
        return cmd, None

    def runner(cmd, **kw):
        """Run the model, then convert its ABC beside the page as <stem>.musicxml."""
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(repo), os.environ.get("PYTHONPATH")]))}
        res = run_cmd_lenient(cmd, env=env, **kw)
        abc = Path(cmd[cmd.index("--out") + 1])
        if abc.exists():
            try:
                abc_to_musicxml(abc, abc.with_suffix(".musicxml"))
            except AbcConversionError as exc:
                ctx.log(f"  {abc.stem}: {exc}")
        return res

    return run_raster_pages(ctx, label="LEGATO 2", ensure_png=_ensure_page_png,
                            runner=runner, build_cmd=build_cmd)
