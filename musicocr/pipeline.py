"""Pipeline context, stage protocol, and the ordered runner.

Each stage is a callable ``run(ctx) -> StageResult``. Stages are ordered; the
runner executes a contiguous slice (``--from`` / ``--to``) and stops on the
first failure unless ``keep_going`` is set. State flows between stages through
``ctx.artifacts`` (a plain dict) and files under ``ctx.workdir``.

Adding a stage later (e.g. a GUI-correction round-trip) means writing one module
with a ``run`` function and inserting its name into ``STAGE_ORDER``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from musicocr.config import Config
from musicocr.state import load_state, save_state

# Canonical order. `correct` sits between OMR and extract so a corrected .omr
# project feeds the rest of the pipeline unchanged.
STAGE_ORDER = ["ingest", "omr", "correct", "extract", "validate", "convert", "qa"]


@dataclass
class PipelineContext:
    input_pdf: Path
    workdir: Path
    config: Config
    book_name: str
    pages: str | None = None          # Audiveris -sheets spec, e.g. "1 4-5"
    extra_constants: list[str] = field(default_factory=list)
    force: bool = False
    artifacts: dict[str, Any] = field(default_factory=dict)
    log: Callable[[str], None] = print

    def constants(self) -> list[str]:
        return self.config.constants_for_profile() + list(self.extra_constants)


@dataclass
class StageResult:
    name: str
    status: str                       # "ok" | "skipped" | "paused" | "failed"
    detail: str = ""
    duration_s: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped")

    @property
    def paused(self) -> bool:
        return self.status == "paused"


class StageError(RuntimeError):
    """Raised by a stage to signal a clean, reportable failure."""


def _load_stage(name: str) -> Callable[[PipelineContext], StageResult]:
    mod = __import__(f"musicocr.stages.{name}", fromlist=["run"])
    return mod.run


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
        for k, v in prior.items():
            ctx.artifacts.setdefault(k, v)
        if prior:
            ctx.log(f"  (restored {len(prior)} artifact(s) from a previous run)")

    results: list[StageResult] = []
    for name in slc:
        run = _load_stage(name)
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
