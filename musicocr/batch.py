"""Resumable batch runner: split a score+parts PDF into per-part PDFs and run each
through the pipeline.

A batch is described by a small TOML spec (see ``scripts/batch.example.toml``).
Part outputs go to ``output/<name>/<key>/`` (mirroring ``input/<name>/``); state is kept in ``output/<name>/_state.json`` and a human-readable progress doc is
written to ``docs/<name>-progress.md``, where ``<name>`` is the spec file's stem.
Finished parts are skipped on re-run, so the batch is safe to stop and resume.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import tomllib

ROOT = Path(__file__).resolve().parent.parent

DONE = "done"
FAILED = "FAILED"
RUNNING = "running"
PENDING = "pending"

DOC_BEGIN = "<!-- batch:begin (auto-generated; text outside the markers is preserved) -->"
DOC_END = "<!-- batch:end -->"

State = Dict[str, Dict[str, str]]


class BatchSpecError(ValueError):
    """The batch spec file is missing a field or has an invalid value."""


@dataclass(frozen=True)
class Part:
    """One part inside the source PDF: pages are 1-based and inclusive."""

    id: str
    label: str
    first_page: int
    last_page: int
    instrument: str = ""  # transposition-table name; empty = leave pitches as written
    score: bool = True    # include in `musicocr combine` (False for e.g. percussion)
    small_graces: bool = False  # copisteria: small noteheads are ruff/flam grace notes (timpani, drums)


@dataclass(frozen=True)
class BatchSpec:
    """A parsed batch definition plus the paths derived from it."""

    name: str
    title: str
    source_pdf: Path
    key_prefix: str
    parts: List[Part]
    engines: Tuple[str, ...]
    convert: bool
    input_dir: Path
    output_dir: Path
    state_path: Path
    doc_path: Path
    root: Path = field(default=ROOT)

    def key(self, part: Part) -> str:
        """Pipeline key of a part (``<output_dir>/<key>/``, ``<key>.pdf``)."""
        return f"{self.key_prefix}{part.id}"


def _require(data: dict, field_name: str, kind: type):
    value = data.get(field_name)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise BatchSpecError(f"batch spec: '{field_name}' must be {kind.__name__}")
    return value


def parse_spec(data: dict, name: str, root: Path = ROOT) -> BatchSpec:
    """Build a BatchSpec from decoded TOML ``data``; ``name`` is the spec file stem."""
    source = Path(_require(data, "source_pdf", str)).expanduser()
    prefix = data.get("key_prefix", "")
    if not isinstance(prefix, str):
        raise BatchSpecError("batch spec: 'key_prefix' must be str")
    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise BatchSpecError("batch spec: need at least one [[parts]] entry")
    parts: List[Part] = []
    seen = set()
    for raw in raw_parts:
        part = Part(
            id=_require(raw, "id", str),
            label=raw.get("label") or raw["id"],
            first_page=_require(raw, "first_page", int),
            last_page=_require(raw, "last_page", int),
            instrument=raw.get("instrument", ""),
            score=bool(raw.get("score", True)),
            small_graces=bool(raw.get("small_graces", False)),
        )
        if part.first_page < 1 or part.last_page < part.first_page:
            raise BatchSpecError(f"batch spec: bad page range for part '{part.id}'")
        if part.id in seen:
            raise BatchSpecError(f"batch spec: duplicate part id '{part.id}'")
        seen.add(part.id)
        parts.append(part)

    engines = data.get("engines", [])
    if not isinstance(engines, list) or not all(isinstance(e, str) for e in engines):
        raise BatchSpecError("batch spec: 'engines' must be a list of engine names")
    from musicocr.engines import ENGINES
    unknown = sorted(set(engines) - set(ENGINES))
    if unknown:
        raise BatchSpecError(f"batch spec: unknown engine(s) {unknown}; known: {sorted(ENGINES)}")
    convert = data.get("convert", False)
    if not isinstance(convert, bool):
        raise BatchSpecError("batch spec: 'convert' must be true/false")

    def rel(key: str, default: str) -> Path:
        return root / data.get(key, default)

    output_dir = rel("output_dir", f"output/{name}")

    return BatchSpec(
        name=name,
        title=data.get("title", name),
        source_pdf=source,
        key_prefix=prefix,
        parts=parts,
        engines=tuple(engines),
        convert=convert,
        input_dir=rel("input_dir", f"input/{name}"),
        output_dir=output_dir,
        state_path=rel("state_file", f"output/{name}/_state.json"),
        doc_path=rel("progress_doc", f"docs/{name}-progress.md"),
        root=root,
    )


def load_spec(path: Path, root: Path = ROOT) -> BatchSpec:
    """Read and parse the TOML batch spec at ``path``."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return parse_spec(data, path.stem, root)


def load_state(path: Path) -> State:
    """Load the state file; a missing file means nothing has run yet."""
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path: Path, state: State) -> None:
    """Write the state file, creating its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def select_parts(
    spec: BatchSpec, state: State, only: Optional[Sequence[str]] = None, redo: bool = False
) -> List[Part]:
    """Parts still to run: honours ``only`` (part ids or full keys) and skips done parts."""
    wanted = set(only) if only else None
    todo = []
    for part in spec.parts:
        key = spec.key(part)
        if wanted is not None and key not in wanted and part.id not in wanted:
            continue
        if state.get(key, {}).get("status") == DONE and not redo:
            continue
        todo.append(part)
    return todo


def render_progress_doc(spec: BatchSpec, state: State, updated: str) -> str:
    """Markdown progress block (header + table) for the batch."""
    rows = ["| Part | Pages | Status | Notes |", "|---|---|---|---|"]
    for part in spec.parts:
        key = spec.key(part)
        entry = state.get(key, {})
        rows.append(
            f"| {part.label} (`{key}`) | {part.first_page}-{part.last_page} | "
            f"{entry.get('status', PENDING)} | {entry.get('note', '')} |"
        )
    return (
        f"# {spec.title} OMR progress\n\n"
        f"Auto-written by `musicocr.batch` from `{spec.name}.toml` (updated {updated}). "
        f"Outputs in `{_rel(spec.output_dir, spec.root)}/<key>/`; per-part PDFs in `{_rel(spec.input_dir, spec.root)}/`. "
        "Re-run the batch to resume.\n\n" + "\n".join(rows) + "\n"
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def merge_progress_doc(existing: Optional[str], block: str) -> str:
    """Put ``block`` between the markers of ``existing``, keeping hand-written text."""
    wrapped = f"{DOC_BEGIN}\n{block}{DOC_END}\n"
    if existing and DOC_BEGIN in existing and DOC_END in existing:
        head, rest = existing.split(DOC_BEGIN, 1)
        tail = rest.split(DOC_END, 1)[1].lstrip("\n")
        return head + wrapped + ("\n" + tail if tail else "")
    return wrapped


def write_progress_doc(spec: BatchSpec, state: State) -> None:
    """Refresh the progress doc on disk."""
    block = render_progress_doc(spec, state, time.strftime("%Y-%m-%d %H:%M"))
    existing = spec.doc_path.read_text() if spec.doc_path.exists() else None
    spec.doc_path.parent.mkdir(parents=True, exist_ok=True)
    spec.doc_path.write_text(merge_progress_doc(existing, block))


def split_pdf(spec: BatchSpec, part: Part) -> Path:
    """Extract the part's pages from the source PDF (poppler); reuses an existing file."""
    key = spec.key(part)
    pdf = spec.input_dir / f"{key}.pdf"
    if pdf.exists():
        return pdf
    spec.input_dir.mkdir(parents=True, exist_ok=True)
    template = spec.input_dir / f"_{key}-%d.pdf"
    subprocess.run(
        ["pdfseparate", "-f", str(part.first_page), "-l", str(part.last_page),
         str(spec.source_pdf), str(template)],
        check=True,
    )
    pieces = [spec.input_dir / f"_{key}-{n}.pdf" for n in range(part.first_page, part.last_page + 1)]
    subprocess.run(["pdfunite", *map(str, pieces), str(pdf)], check=True)
    for piece in pieces:
        piece.unlink()
    return pdf


def run_part(spec: BatchSpec, part: Part, pdf: Path) -> Dict[str, str]:
    """Run the pipeline CLI (``run.sh run``) on one part PDF; return its state entry."""
    key = spec.key(part)
    out_dir = spec.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if spec.engines:   # side-by-side: output/<name>/<key>/engines/<engine>/ + compare.md
        cmd = [str(spec.root / "run.sh"), "compare", str(pdf), "--engines", ",".join(spec.engines),
               "--output", str(out_dir), "--keep-going"]
        if spec.convert:
            cmd.append("--convert")
    else:
        cmd = [str(spec.root / "run.sh"), "run", str(pdf), "--output", str(out_dir), "--keep-going"]
    if part.instrument:
        cmd += ["--transpose-instrument", part.instrument]
    if part.small_graces and not spec.engines:
        cmd.append("--small-graces")
    with open(out_dir / f"{key}.run.log", "w") as log:
        rc = subprocess.run(
            cmd, stdout=log, stderr=subprocess.STDOUT,
        ).returncode
    produced = (out_dir / key / "compare.md") if spec.engines \
        else out_dir / key / f"{key}.musicxml"
    if produced.exists() and produced.stat().st_size > 0:
        minutes = (time.time() - started) / 60
        return {"status": DONE, "note": f"{minutes:.1f} min, rc={rc}; see {_rel(out_dir / key, spec.root)}/{"compare.md" if spec.engines else "report.md"}"}
    return {"status": FAILED, "note": f"rc={rc}; see {_rel(out_dir / (key + '.run.log'), spec.root)}"}


def run_batch(
    spec: BatchSpec,
    only: Optional[Sequence[str]] = None,
    redo: bool = False,
    runner: Callable[[BatchSpec, Part, Path], Dict[str, str]] = run_part,
    splitter: Callable[[BatchSpec, Part], Path] = split_pdf,
    jobs: int = 1,
) -> State:
    """Run every outstanding part, persisting state and the progress doc after each step.

    ``jobs`` parts run at a time (each part is its own process tree with its own output folder,
    so they do not share files); state and the progress doc are updated under a lock.
    """
    state = load_state(spec.state_path)
    lock = threading.Lock()

    def one(part: Part) -> None:
        key = spec.key(part)
        pdf = splitter(spec, part)
        with lock:
            state[key] = {"status": RUNNING, "note": f"started {time.strftime('%H:%M')}"}
            save_state(spec.state_path, state)
            write_progress_doc(spec, state)
        result = runner(spec, part, pdf)
        with lock:
            state[key] = result
            save_state(spec.state_path, state)
            write_progress_doc(spec, state)
            print(key, state[key], flush=True)

    todo = list(select_parts(spec, state, only, redo))
    if jobs <= 1:
        for part in todo:
            one(part)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for fut in [pool.submit(one, part) for part in todo]:
                fut.result()
    return state


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    ap = argparse.ArgumentParser(
        description="Resumable batch: split a score+parts PDF and run each part through the pipeline.",
    )
    ap.add_argument("--batch", required=True, type=Path, metavar="SPEC.toml",
                    help="batch spec file (see scripts/batch.example.toml)")
    ap.add_argument("--only", help="comma-separated part ids or keys to run")
    ap.add_argument("--redo", action="store_true", help="re-run parts already marked done")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="parts to run at the same time (default 1; each detector process uses about "
                         "one core, so 3-4 is a sensible start on a laptop -- watch the temperature)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_spec(args.batch)
    except (OSError, BatchSpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    only = args.only.split(",") if args.only else None
    run_batch(spec, only=only, redo=args.redo, jobs=max(args.jobs, 1))
    return 0
