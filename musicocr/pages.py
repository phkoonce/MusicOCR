"""Page-spec helpers (engine-neutral).

A page spec is a string of page numbers and ranges separated by spaces and/or
commas, e.g. ``"1 4-5"`` or ``"1,4-5"``.
"""
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
    page_count = ctx.artifacts.page_count
    if page_count:
        return list(range(1, page_count + 1))
    return []
