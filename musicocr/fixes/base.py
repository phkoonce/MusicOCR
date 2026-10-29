"""The fix abstraction: one small, named, switchable repair of OMR output.

Every automatic repair of a MusicXML file (a bad time signature, a ghost bar, an inconsistent
tuplet, ...) is described by a :class:`Fix` and run by :func:`run_fixes`. That gives all of them
the same behaviour:

* a **name**, which is also the switch: ``[fixes] <name> = false`` in config.toml, or
  ``--no-fix <name>`` / ``--fix <name>`` on ``musicocr run`` (``musicocr fixes`` lists them);
* a **default** (and, for fixes that predate ``[fixes]``, the older config key that still works);
* one error policy: a fix that raises is logged and skipped, never fatal to the page;
* one log format: ``  <label>: <message with the count>``, printed only when the fix changed something;
* declared **ordering** (``after``): a fix that must run after another says so, and the registry
  refuses to load an order that breaks it.

A fix is a function ``apply(ctx) -> int`` returning how many things it changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

Log = Callable[[str], None]


@dataclass
class FixContext:
    """What a fix gets to work with."""
    paths: Sequence[Path]            # the file(s) to repair (one for scope "each", all for "all")
    config: object                   # musicocr.config.Config
    log: Log = print
    label: str = ""                  # prefix for log lines, e.g. "page 3" or "book.p003.musicxml"
    page: Optional[int] = None       # 1-based page number, when the files are OMR pages
    prev_path: Optional[Path] = None # the previous page's file, when there is one

    @property
    def path(self) -> Path:
        return self.paths[0]


@dataclass(frozen=True)
class Fix:
    name: str
    apply: Callable[[FixContext], int]
    summary: str                     # one line for ``musicocr fixes``: what it repairs
    message: str = ""                # logged as ``  <label>: <message>`` with ``{n}``; "" = apply() logs itself
    default: bool = True
    legacy: Optional[Callable[[object], bool]] = None    # older config switch, honoured when [fixes] is silent
    engines: frozenset = frozenset()                     # empty = any engine
    scope: str = "each"              # "each": run per file; "all": run once on all the files together
    when: Optional[Callable[[FixContext], bool]] = None  # extra applicability test (e.g. page 1 only)
    after: tuple = ()                # names of fixes that must run before this one
    needs: str = ""                  # note shown by ``musicocr fixes`` for non-obvious requirements


def fix_enabled(fix: Fix, config) -> bool:
    """``[fixes]`` / CLI override first, then the older config key, then the default."""
    override = config.fixes.overrides.get(fix.name)
    if override is not None:
        return override
    if fix.legacy is not None:
        return bool(fix.legacy(config))
    return fix.default


def applies_to_engine(fix: Fix, engine: str) -> bool:
    return not fix.engines or engine in fix.engines


def check_order(fixes: Sequence[Fix]) -> None:
    """Raise ValueError if a fix is listed before one it declares itself ``after``, or a name repeats."""
    seen: set = set()
    names = {f.name for f in fixes}
    for f in fixes:
        if f.name in seen:
            raise ValueError(f"fix {f.name!r} listed twice")
        for dep in f.after:
            if dep in names and dep not in seen:
                raise ValueError(f"fix {f.name!r} must run after {dep!r}")
        seen.add(f.name)


def run_fixes(fixes: Sequence[Fix], ctx: FixContext) -> dict:
    """Run the enabled fixes in order; returns ``{name: changes}`` for those that changed something.

    ``ctx.paths`` holds the files. Fixes of scope "each" run once per file (with ``ctx.label``
    defaulting to the file name), "all" fixes once on the whole list.
    """
    results: dict = {}
    engine = getattr(getattr(ctx.config, "omr", None), "engine", "")
    for fix in fixes:
        if not applies_to_engine(fix, engine) or not fix_enabled(fix, ctx.config):
            continue
        if fix.scope == "all":
            units = [ctx]
        else:
            units = [FixContext([p], ctx.config, ctx.log, ctx.label or p.name, ctx.page, ctx.prev_path)
                     for p in ctx.paths]
        for unit in units:
            if fix.when is not None and not fix.when(unit):
                continue
            try:
                n = fix.apply(unit)
            except Exception as exc:  # noqa: BLE001 - one broken fix must not lose the page
                unit.log(f"  {unit.label}: {fix.name} fix failed ({type(exc).__name__}: {exc})")
                continue
            if n:
                results[fix.name] = results.get(fix.name, 0) + n
                if fix.message:
                    unit.log(f"  {unit.label}: {fix.message.format(n=n)}")
    return results
