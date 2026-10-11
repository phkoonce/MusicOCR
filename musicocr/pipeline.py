"""Pipeline context, stage protocol, and the ordered runner.

Each stage is a callable ``run(ctx) -> StageResult``. Stages are ordered; the
runner executes a contiguous slice (``--from`` / ``--to``) and stops on the
first failure unless ``keep_going`` is set. State flows between stages through
``ctx.artifacts`` (a typed :class:`Artifacts`) and files under ``ctx.workdir``.

Adding a stage later (e.g. a GUI-correction round-trip) means writing one module
with a ``run`` function and inserting its name into ``STAGE_ORDER`` (the stage
registry in ``musicocr/stages/__init__.py``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from musicocr.artifacts import Artifacts
from musicocr.config import Config
from musicocr.errors import StageError, StageResult
from musicocr.state import load_state, restore_missing, save_state
from musicocr.stages import STAGE_ORDER, load_stage

__all__ = ["PipelineContext", "STAGE_ORDER", "StageError", "StageResult",
           "resolve_slice", "run_pipeline"]


@dataclass
class PipelineContext:
    input_pdf: Path
    workdir: Path
    config: Config
    book_name: str
    pages: str | None = None          # page spec, e.g. "1 4-5" (see musicocr.pages)
    force: bool = False
    artifacts: Artifacts = field(default_factory=Artifacts)
    log: Callable[[str], None] = print


def resolve_slice(from_stage: str | None, to_stage: str | None) -> list[str]:
    start = STAGE_ORDER.index(from_stage) if from_stage else 0
    end = STAGE_ORDER.index(to_stage) + 1 if to_stage else len(STAGE_ORDER)
    if from_stage and from_stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {from_stage}")
    if to_stage and to_stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {to_stage}")
    if start >= end:
        raise ValueError(f"empty stage range: {from_stage!r}..{to_stage!r}")
    return STAGE_ORDER[start:end]


def run_pipeline(
    ctx: PipelineContext,
    from_stage: str | None = None,
    to_stage: str | None = None,
    keep_going: bool = False,
) -> list[StageResult]:
    slc = resolve_slice(from_stage, to_stage)
    # Re-entering mid-pipeline: reload artifacts saved by earlier stages.
    if slc and slc[0] != STAGE_ORDER[0] and ctx.workdir.exists():
        prior = load_state(ctx.workdir)
        restored = restore_missing(ctx.artifacts, prior)
        if restored:
            ctx.log(f"  (restored {restored} artifact(s) from a previous run)")

    results: list[StageResult] = []
    for name in slc:
        run = load_stage(name)
        ctx.log(f"[{name}] starting")
        t0 = time.monotonic()
        try:
            result = run(ctx)
        except StageError as exc:
            result = StageResult(name, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything as a failure
            result = StageResult(name, "failed", f"{type(exc).__name__}: {exc}")
        result.duration_s = time.monotonic() - t0
        results.append(result)
        ctx.log(f"[{name}] {result.status} ({result.duration_s:.1f}s) {result.detail}".rstrip())
        if result.ok and ctx.workdir.exists():
            save_state(ctx.workdir, ctx.artifacts)
        if not result.ok and not keep_going:
            break
    return results
