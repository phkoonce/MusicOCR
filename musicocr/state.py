"""Persist pipeline artifacts between stages so ``--from <stage>`` keeps context.

A single ``.musicocr-state.json`` in the work dir. Serialisation is derived from
the type annotations on :class:`musicocr.artifacts.Artifacts`, so adding a field
there needs no change here. ``Path`` values become strings, ``int`` dict keys
become JSON string keys; the layout matches earlier releases.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import typing
from pathlib import Path
from typing import Any

from musicocr.artifacts import Artifacts

STATE_FILE = ".musicocr-state.json"

log = logging.getLogger(__name__)


def _encode(v: Any, tp: Any) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if v is None:
        return None
    if origin is typing.Union:
        # Optional[X] / Union[Path, List[Path]]: pick the member matching the value.
        for a in args:
            ao = typing.get_origin(a) or a
            if a is not type(None) and isinstance(ao, type) and isinstance(v, ao):
                return _encode(v, a)
        return v
    if tp is Path:
        return str(v)
    if origin in (list, typing.List):
        return [_encode(x, args[0] if args else Any) for x in v]
    if origin in (dict, typing.Dict):
        kt, vt = args if args else (Any, Any)
        return {str(k): _encode(x, vt) for k, x in v.items()}
    return v


def _decode(v: Any, tp: Any) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if v is None:
        return None
    if origin is typing.Union:
        for a in args:
            if a is type(None):
                continue
            ao = typing.get_origin(a) or a
            if (ao is list and isinstance(v, list)) or (ao is dict and isinstance(v, dict)) \
                    or (ao is Path and isinstance(v, str)):
                return _decode(v, a)
        return v
    if tp is Path:
        return Path(v)
    if origin in (list, typing.List):
        return [_decode(x, args[0] if args else Any) for x in v]
    if origin in (dict, typing.Dict):
        kt, vt = args if args else (Any, Any)
        return {(int(k) if kt is int else k): _decode(x, vt) for k, x in v.items()}
    return v


def _fields() -> list:
    hints = typing.get_type_hints(Artifacts)
    return [(f, hints[f.name]) for f in dataclasses.fields(Artifacts)
            if f.metadata.get("persist", True)]


def artifacts_to_json(art: Artifacts) -> dict:
    """Return the JSON-ready dict for ``art`` (unset / None fields omitted)."""
    out: dict = {}
    for f, tp in _fields():
        v = getattr(art, f.name)
        if v is not None:
            out[f.name] = _encode(v, tp)
    return out


def artifacts_from_json(raw: dict) -> Artifacts:
    """Rebuild an :class:`Artifacts` from saved JSON; unknown keys are ignored."""
    kwargs = {}
    for f, tp in _fields():
        if f.name in raw:
            kwargs[f.name] = _decode(raw[f.name], tp)
    return Artifacts(**kwargs)


def save_state(workdir: Path, artifacts: Artifacts) -> None:
    # Serialisation bugs (TypeError) must surface; only I/O failure is tolerated.
    text = json.dumps(artifacts_to_json(artifacts), indent=2)
    try:
        (workdir / STATE_FILE).write_text(text)
    except OSError as exc:
        log.warning("could not write %s: %s", workdir / STATE_FILE, exc)


def load_state(workdir: Path) -> Artifacts:
    """Load saved artifacts; a missing or unreadable state file yields defaults."""
    fp = workdir / STATE_FILE
    if not fp.exists():
        return Artifacts()
    try:
        raw = json.loads(fp.read_text())
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", fp, exc)
        return Artifacts()
    return artifacts_from_json(raw)


def restore_missing(current: Artifacts, prior: Artifacts) -> int:
    """Copy ``prior`` values into ``current`` for fields still at their default.

    Returns how many fields were restored.
    """
    n = 0
    for f, _tp in _fields():
        default = Artifacts.__dataclass_fields__[f.name]
        dv = (default.default_factory() if default.default_factory is not dataclasses.MISSING
              else default.default)
        pv = getattr(prior, f.name)
        if getattr(current, f.name) == dv and pv != dv:
            setattr(current, f.name, pv)
            n += 1
    return n
