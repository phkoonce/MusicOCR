"""Registry of OMR engines.

One place that knows which engines exist, so config validation, the ``omr``
dispatcher, the ``--engine`` CLI choices and ``doctor`` all agree. Adding an
engine means: add an entry here, a ``musicocr/stages/omr_<name>.py`` exposing
``run(ctx)``, and (if it has its own interpreter) a ``Tools`` field.

Capabilities are declared per engine rather than tested by name at call sites:

* ``tool``         - ``[tools]`` key resolving the engine's executable/interpreter.
* ``checks``       - engine-specific validate heuristics to enable.
* ``available``    - False for placeholders whose model isn't released yet; they
  are accepted by config/CLI so the name is reserved, but running one fails with
  ``note``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Engine:
    name: str
    module: str                 # musicocr.stages.<module>
    label: str                  # human name for doctor/report
    tool: str                   # Tools field to resolve ("" if none)
    checks: frozenset = frozenset()
    available: bool = True
    note: str = ""


ENGINES: dict[str, Engine] = {e.name: e for e in (
    Engine("homr", "omr_homr", "homr", "homr_python",
           checks=frozenset({"dropped_multirest"})),
    Engine("audiveris", "omr_audiveris", "Audiveris", "audiveris"),
    Engine("copisteria", "omr_copisteria", "Copisteria", "copisteria_python"),
    Engine("legato2", "omr_legato2", "LEGATO 2", "",
           available=False,
           note="LEGATO 2 (arXiv:2607.05769) has no released code or weights yet"),
)}

VALID_ENGINES = frozenset(ENGINES)


def has_check(engine: str, check: str) -> bool:
    e = ENGINES.get(engine)
    return bool(e and check in e.checks)
