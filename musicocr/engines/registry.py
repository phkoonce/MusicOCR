"""Registry of OMR engines.

One place that knows which engines exist, so config validation, the ``omr``
dispatcher, the ``--engine`` CLI choices and ``doctor`` all agree. Adding an
engine means: add an entry here, a module (or ``engines/<name>/stage.py``) exposing
``run(ctx)``, and (if it has its own interpreter) a ``Tools`` field.

Capabilities are declared per engine rather than tested by name at call sites:

* ``tool``         - ``[tools]`` key resolving the engine's executable/interpreter.
* ``checks``       - engine-specific validate heuristics to enable.
* ``available``    - False for placeholders whose model isn't released yet; they
  are accepted by config/CLI so the name is reserved, but running one fails with
  ``note``. (No engine is a placeholder at the moment.)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Engine:
    name: str
    module: str                 # dotted module exposing ``run(ctx)``
    label: str                  # human name for doctor/report
    tool: str                   # Tools field to resolve ("" if none)
    checks: frozenset = frozenset()
    available: bool = True
    note: str = ""


ENGINES: dict[str, Engine] = {e.name: e for e in (
    Engine("homr", "musicocr.engines.homr.stage", "homr", "homr_python",
           checks=frozenset({"dropped_multirest"})),
    Engine("audiveris", "musicocr.engines.audiveris", "Audiveris", "audiveris"),
    Engine("copisteria", "musicocr.engines.copisteria.stage", "Copisteria", "copisteria_python"),
    Engine("legato2", "musicocr.engines.legato2.stage", "LEGATO 2", "legato2_python"),
)}

VALID_ENGINES = frozenset(ENGINES)


def has_check(engine: str, check: str) -> bool:
    e = ENGINES.get(engine)
    return bool(e and check in e.checks)
