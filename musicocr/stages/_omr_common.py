"""Shared helpers for OMR engine stages (Audiveris, homr)."""
from __future__ import annotations

from musicocr.pipeline import PipelineContext


def expand_pages(spec: str) -> list[int]:
    out: list[int] = []
    for tok in spec.replace(",", " ").split():
        if "-" in tok:
            a, b = tok.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(tok))
    return sorted(set(out))


def resolve_pages(ctx: PipelineContext) -> list[int]:
    """Which page numbers to transcribe: ``ctx.pages`` if given, else 1..page_count."""
    if ctx.pages:
        return expand_pages(ctx.pages)
    page_count = ctx.artifacts.get("page_count")
    if page_count:
        return list(range(1, page_count + 1))
    return []
