"""Leaf module: the stage failure exception and the per-stage result type.

Deliberately imports nothing from the rest of the package so that util, config,
preprocess, pipeline and every stage can depend on it without cycles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class StageError(RuntimeError):
    """Raised by a stage to signal a clean, reportable failure."""


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
