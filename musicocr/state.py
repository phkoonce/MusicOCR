"""Persist pipeline artifacts between stages so ``--from <stage>`` keeps context.

A single ``.musicocr-state.json`` in the work dir. Only a whitelist of keys is
saved, with enough type info to rebuild ``Path`` objects on load.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_FILE = ".musicocr-state.json"

_PATH_KEYS = {"source_pdf", "omr_log", "omr_project", "musicxml", "render_pdf"}
_PATH_LIST_KEYS = {"mxl_files", "musicxml_pages", "render_pdfs"}
_PATH_DICT_KEYS = {"outputs"}            # {str: path | [path, ...]}
_PAGE_MAP_KEYS = {"mxl_by_page"}         # {int: [path, ...]}
_INT_PATH_MAP_KEYS = {"prepped_pages"}   # {int: path}
_PLAIN_KEYS = {"page_count", "omr_failed_pages", "validation", "qa_pages",
               "merge_skipped", "extract_merged"}

_ALL = (_PATH_KEYS | _PATH_LIST_KEYS | _PATH_DICT_KEYS | _PAGE_MAP_KEYS
        | _INT_PATH_MAP_KEYS | _PLAIN_KEYS)


def save_state(workdir: Path, artifacts: dict) -> None:
    out: dict = {}
    for k in _ALL:
        if k not in artifacts or artifacts[k] is None:
            continue
        v = artifacts[k]
        if k in _PATH_KEYS:
            out[k] = str(v)
        elif k in _PATH_LIST_KEYS:
            out[k] = [str(p) for p in v]
        elif k in _PATH_DICT_KEYS:
            out[k] = {kk: ([str(p) for p in vv] if isinstance(vv, list) else str(vv))
                      for kk, vv in v.items()}
        elif k in _PAGE_MAP_KEYS:
            out[k] = {str(pg): [str(p) for p in paths] for pg, paths in v.items()}
        elif k in _INT_PATH_MAP_KEYS:
            out[k] = {str(pg): str(p) for pg, p in v.items()}
        else:
            out[k] = v
    try:
        (workdir / STATE_FILE).write_text(json.dumps(out, indent=2))
    except (OSError, TypeError):
        pass


def load_state(workdir: Path) -> dict:
    fp = workdir / STATE_FILE
    if not fp.exists():
        return {}
    try:
        raw = json.loads(fp.read_text())
    except (OSError, ValueError):
        return {}
    art: dict = {}
    for k, v in raw.items():
        if k in _PATH_KEYS:
            art[k] = Path(v)
        elif k in _PATH_LIST_KEYS:
            art[k] = [Path(p) for p in v]
        elif k in _PATH_DICT_KEYS:
            art[k] = {kk: ([Path(p) for p in vv] if isinstance(vv, list) else Path(vv))
                      for kk, vv in v.items()}
        elif k in _PAGE_MAP_KEYS:
            art[k] = {int(pg): [Path(p) for p in paths] for pg, paths in v.items()}
        elif k in _INT_PATH_MAP_KEYS:
            art[k] = {int(pg): Path(p) for pg, p in v.items()}
        else:
            art[k] = v
    return art
