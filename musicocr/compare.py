"""Side-by-side comparison of OMR engines on the same input (``musicocr compare``).

Layout (see README "Comparing engines")::

    <output>/<book>/source.pdf, prepped/      shared ingest, run once
    <output>/<book>/engines/<engine>/         each engine's pages/, extract..validate
                                              artifacts, state file and report.md
    <output>/<book>/compare.md, compare.json  the comparison

Everything that reads scores is a pure function over MusicXML files / report
dicts (ElementTree only, no music21), so it is unit-testable with synthetic
input. Multi-rests are counted in the collapsed convention
(``musicocr.fixes.multirest.collapsed_units``) so engines that emit them expanded and
collapsed compare as equals. :func:`run_compare` is the thin runner.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from musicocr.artifacts import Artifacts
from musicocr.engines import ENGINES
from musicocr.fixes.multirest import collapsed_units

_PAGE_RE = re.compile(r"\.p(\d+)(?:\.\d+)?\.musicxml$")
_NORMALIZED_RE = re.compile(r"^(\d+) measure")
MAX_HUNKS = 60          # per engine pair in compare.md (all are kept in the JSON)


# ---- score reading -----------------------------------------------------------

@dataclass
class Unit:
    """One collapsed measure (a multi-rest is a single unit)."""
    page: int
    index: int                      # 1-based unit number within the page
    bar: int = 0                    # printed bar number at its start (running, from 1)
    bars: int = 1                   # printed bars it covers (multi-rest count)
    sig: tuple = ()
    notes: int = 0
    rests: int = 0
    tuplets: int = 0
    multirest: int = 0
    times: list = field(default_factory=list)
    keys: list = field(default_factory=list)

    def text(self, limit: int = 90) -> str:
        s = " ".join(f"{p}/{d}" if d != "" else p for p, d in self.sig) or "(empty)"
        return s if len(s) <= limit else s[:limit - 1] + "…"


def _frac_text(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _pitch_token(note: ET.Element) -> str:
    p = note.find("pitch")
    if p is None:
        return "?"
    alter = int(float(p.findtext("alter") or 0))
    acc = "#" * alter if alter > 0 else "b" * -alter
    return f"{p.findtext('step', '?')}{acc}{p.findtext('octave', '?')}"


def _unit_content(measure: ET.Element, divisions: int, bars: int) -> tuple:
    """Signature of a measure: (pitch-or-rest, duration-in-quarters) pairs.

    Chords become one token with sorted pitches; ties, articulations, grace notes
    and voices/layout are ignored. A whole-measure rest is ``('r','M')`` and a
    multi-rest ``('R', N)`` so differing bar lengths in divisions don't matter.
    """
    if bars >= 2:
        return (("R", str(bars)),)
    sig: list[list] = []   # [pitches(list)|None for rest, dur, measure_rest]
    for n in measure.findall("note"):
        if n.find("grace") is not None:
            continue
        rest = n.find("rest")
        if n.find("chord") is not None and sig and sig[-1][0] is not None and rest is None:
            sig[-1][0].append(_pitch_token(n))
            continue
        try:
            dur = Fraction(int(n.findtext("duration") or 0), max(divisions, 1))
        except ValueError:
            dur = Fraction(0)
        if rest is not None:
            sig.append([None, dur, rest.get("measure") == "yes"])
        else:
            sig.append([[_pitch_token(n)], dur, False])
    out = []
    for pitches, dur, whole in sig:
        if pitches is None:
            out.append(("r", "M" if whole else _frac_text(dur)))
        else:
            out.append(("+".join(sorted(pitches)), _frac_text(dur)))
    return tuple(out)


def read_units(path: Path, page: int = 0) -> list[Unit]:
    """Units of the first part of one MusicXML file, collapsed-multi-rest convention."""
    part = ET.parse(path).getroot().find("part")
    if part is None:
        return []
    # divisions / time / key in force at each measure (units may skip folded bars)
    state: dict[int, tuple] = {}
    div, time, key = 1, None, None
    for m in part.findall("measure"):
        for a in m.findall("attributes"):
            if a.findtext("divisions"):
                div = int(a.findtext("divisions"))
        t = m.find("attributes/time")
        t_new = key_new = None
        if t is not None and t.findtext("beats"):
            time = f"{t.findtext('beats')}/{t.findtext('beat-type')}"
            t_new = time
        k = m.find("attributes/key/fifths")
        if k is not None and k.text:
            key = int(k.text)
            key_new = key
        state[id(m)] = (div, t_new, key_new)
    units: list[Unit] = []
    for i, (m, bars) in enumerate(collapsed_units(part), 1):
        divisions, t_new, k_new = state[id(m)]
        notes = rests = tup = 0
        for n in m.findall("note"):
            if n.find("grace") is not None:
                continue
            if n.find("rest") is not None:
                rests += 1
            else:
                notes += 1
            if n.find("time-modification") is not None:
                tup += 1
        u = Unit(page=page, index=i, bars=bars, notes=notes, rests=rests, tuplets=tup,
                 multirest=bars if bars >= 2 else 0,
                 times=[t_new] if t_new else [],
                 keys=[k_new] if k_new is not None else [],
                 sig=_unit_content(m, divisions, bars))
        units.append(u)
    return units


def page_of(path: Path) -> int | None:
    m = _PAGE_RE.search(Path(path).name)
    return int(m.group(1)) if m else None


def load_sequence(page_files: list[Path]) -> list[Unit]:
    """All units of the score in page order, with running printed bar numbers."""
    seq: list[Unit] = []
    for f in sorted(page_files, key=lambda p: (page_of(p) or 0, str(p))):
        seq.extend(read_units(Path(f), page_of(f) or 0))
    bar = 1
    for u in seq:
        u.bar = bar
        bar += u.bars
    return seq


# ---- per-engine metrics ------------------------------------------------------

def page_metrics(units: list[Unit]) -> dict:
    keys: list = []
    times: list = []
    for u in units:
        times += [t for t in u.times if t not in times[-1:]]
        keys += [k for k in u.keys if k not in keys[-1:]]
    return {
        "measures": len(units),
        "bars": sum(u.bars for u in units),
        "notes": sum(u.notes for u in units),
        "rests": sum(u.rests for u in units),
        "tuplets": sum(u.tuplets for u in units),
        "multirests": [u.multirest for u in units if u.multirest],
        "times": times,
        "keys": keys,
    }


def collect_metrics(engine: str, report: dict) -> dict:
    """Metrics for one finished engine run, from its ``build_report`` dict."""
    files = [Path(p) for p in report.get("page_files") or []]
    by_page: dict[int, list[Unit]] = {}
    for u in load_sequence(files):
        by_page.setdefault(u.page, []).append(u)
    pages = {p: page_metrics(us) for p, us in sorted(by_page.items())}
    stages = {s["name"]: {"status": s["status"], "duration_s": s["duration_s"]}
              for s in report.get("stages", [])}
    val = report.get("validation") or {}
    by_code: dict[str, int] = {}
    for f in val.get("findings", []):
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    norm = 0
    for s in report.get("stages", []):
        if s["name"] == "normalize":
            m = _NORMALIZED_RE.match(s.get("detail", ""))
            norm = int(m.group(1)) if m else 0
    tot = lambda k: sum(p[k] for p in pages.values())  # noqa: E731
    return {
        "engine": engine, "status": "ran",
        "failed_stage": next((s["name"] for s in report.get("stages", [])
                              if s["status"] == "failed"), ""),
        "stages": stages,
        "total_s": round(sum(s["duration_s"] for s in stages.values()), 2),
        "failed_pages": report.get("omr_failed_pages") or [],
        "pages": pages,
        "totals": {"measures": tot("measures"), "bars": tot("bars"), "notes": tot("notes"),
                   "rests": tot("rests"), "tuplets": tot("tuplets"),
                   "multirests": sum(len(p["multirests"]) for p in pages.values())},
        "findings_by_code": dict(sorted(by_code.items())),
        "finding_counts": val.get("counts") or {},
        "normalize_fixed": norm,
        "report": report.get("workdir", ""),
    }


# ---- measure-level diff ------------------------------------------------------

def _where(seq: list[Unit], lo: int, hi: int) -> str:
    """Human locator for seq[lo:hi] (or the gap at lo when empty)."""
    if not seq:
        return "(no measures)"
    if lo >= hi:
        u = seq[min(lo, len(seq) - 1)]
        return f"before p{u.page} m.{u.index} (bar ~{u.bar})"
    a, b = seq[lo], seq[hi - 1]
    if lo == hi - 1:
        return f"p{a.page} m.{a.index} (bar ~{a.bar})"
    return (f"p{a.page} m.{a.index} – p{b.page} m.{b.index} "
            f"(bars ~{a.bar}–{b.bar + b.bars - 1})")


def diff_sequences(a: list[Unit], b: list[Unit]) -> dict:
    """Align two unit sequences on their signatures; list the disagreeing ranges."""
    sm = difflib.SequenceMatcher(None, [u.sig for u in a], [u.sig for u in b],
                                 autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append({
            "kind": tag,
            "a_where": _where(a, i1, i2), "b_where": _where(b, j1, j2),
            "a_count": i2 - i1, "b_count": j2 - j1,
            "a_content": [u.text() for u in a[i1:i2]],
            "b_content": [u.text() for u in b[j1:j2]],
        })
    matched = sum(bl.size for bl in sm.get_matching_blocks())
    return {"ratio": round(sm.ratio(), 4), "matched": matched,
            "a_len": len(a), "b_len": len(b), "hunks": hunks}


# ---- rendering ---------------------------------------------------------------

def _cell(v) -> str:
    return str(v) if v not in (None, "", []) else "—"


def render_markdown(book: str, metrics: list[dict], diffs: list[dict],
                    notes: list[str] | None = None) -> str:
    ran = [m for m in metrics if m["status"] == "ran"]
    names = [m["engine"] for m in metrics]
    L = [f"# Engine comparison — {book}", ""]
    for n in notes or []:
        L.append(f"- {n}")
    for m in metrics:
        if m["status"] != "ran":
            L.append(f"- **{m['engine']}** not run: {m.get('reason', '')}")
    L.append("")

    def table(head, rows):
        L.append("| " + " | ".join(head) + " |")
        L.append("|" + "---|" * len(head))
        for r in rows:
            L.append("| " + " | ".join(str(c) for c in r) + " |")
        L.append("")

    def row(label, fn):
        return [label] + [_cell(fn(m)) if m["status"] == "ran" else "—" for m in metrics]

    L.append("## Summary")
    L.append("")
    table(["metric"] + names, [
        row("measures (collapsed multi-rests)", lambda m: m["totals"]["measures"]),
        row("printed bars (multi-rest counts)", lambda m: m["totals"]["bars"]),
        row("notes", lambda m: m["totals"]["notes"]),
        row("rests", lambda m: m["totals"]["rests"]),
        row("tuplet notes", lambda m: m["totals"]["tuplets"]),
        row("multi-rest tags", lambda m: m["totals"]["multirests"]),
        row("failed pages", lambda m: ", ".join(map(str, m["failed_pages"])) or "none"),
        row("failed stage", lambda m: m["failed_stage"] or "none"),
        row("normalize fixes applied", lambda m: m["normalize_fixed"]),
        row("findings (error/warn/info)", lambda m: "/".join(
            str(m["finding_counts"].get(s, 0)) for s in ("error", "warn", "info"))),
        row("total time (s)", lambda m: m["total_s"]),
    ])

    L.append("## Wall-clock per stage (s)")
    L.append("")
    stage_names = []
    for m in ran:
        stage_names += [s for s in m["stages"] if s not in stage_names]
    table(["stage"] + names, [
        row(s, lambda m, s=s: (f"{m['stages'][s]['duration_s']:.1f}"
                               + ("" if m["stages"][s]["status"] == "ok"
                                  else f" ({m['stages'][s]['status']})"))
            if s in m["stages"] else "")
        for s in stage_names])

    L.append("## Validate findings by code")
    L.append("")
    codes = sorted({c for m in ran for c in m["findings_by_code"]})
    if codes:
        table(["code"] + names, [
            row(c, lambda m, c=c: m["findings_by_code"].get(c, 0)) for c in codes])
    else:
        L.extend(["_no findings_", ""])

    L.append("## Per page")
    L.append("")
    pages = sorted({p for m in ran for p in m["pages"]})
    if pages:
        def pg(m, p, f):
            d = m["pages"].get(p)
            return f(d) if d else "missing"
        L.append("Each cell: measures (printed bars) · notes/rests · tuplet notes.")
        L.append("")
        table(["page"] + names, [
            [f"p{p}"] + [pg(m, p, lambda d: f"{d['measures']} ({d['bars']}) · "
                                           f"{d['notes']}/{d['rests']} · {d['tuplets']}")
                         if m["status"] == "ran" else "—" for m in metrics]
            for p in pages])
        table(["page"] + [f"{n}: time / key / multi-rests" for n in names], [
            [f"p{p}"] + [pg(m, p, lambda d: " / ".join([
                ",".join(d["times"]) or "·", ",".join(map(str, d["keys"])) or "·",
                ",".join(map(str, d["multirests"])) or "·"]))
                if m["status"] == "ran" else "—" for m in metrics]
            for p in pages])
    else:
        L.extend(["_no pages_", ""])

    L.append("## Measure-level diff")
    L.append("")
    if not diffs:
        L.extend(["_needs at least two engines that produced a score_", ""])
    for d in diffs:
        a, b = d["a"], d["b"]
        L.append(f"### {a} vs {b}")
        L.append("")
        L.append(f"Similarity {d['ratio']:.3f} ({d['matched']} identical of "
                 f"{d['a_len']} / {d['b_len']} measures); {len(d['hunks'])} disagreeing "
                 "range(s). Locators are page, measure-on-page and a running printed-bar "
                 "estimate (no pickup assumed; each engine's own count).")
        L.append("")
        for h in d["hunks"][:MAX_HUNKS]:
            L.append(f"- **{h['kind']}** — {a}: {h['a_where']} [{h['a_count']}] · "
                     f"{b}: {h['b_where']} [{h['b_count']}]")
            for label, items in ((a, h["a_content"]), (b, h["b_content"])):
                shown = items[:6] + (["…"] if len(items) > 6 else [])
                L.append(f"    - {label}: " + (" ¦ ".join(f"`{x}`" if x != "…" else x
                                                          for x in shown) or "_nothing_"))
        if len(d["hunks"]) > MAX_HUNKS:
            L.append(f"- … {len(d['hunks']) - MAX_HUNKS} more in compare.json")
        L.append("")
    return "\n".join(L) + "\n"


# ---- running -----------------------------------------------------------------

def engine_unavailable(config, name: str) -> str | None:
    """Why ``name`` can't run here (None if it can). Doctor-style check."""
    eng = ENGINES[name]
    if not eng.available:
        return eng.note or "unavailable"
    if eng.tool and not config.resolve_tool(eng.tool):
        return f"tool {eng.tool!r} not found (see `musicocr doctor`)"
    if name == "copisteria" and not Path(config.omr.copisteria.repo).expanduser().is_dir():
        return f"Copisteria repo not found at {config.omr.copisteria.repo!r}"
    if name == "legato2" and not (Path(config.omr.legato2.repo).expanduser() / "legato").is_dir():
        return f"LEGATO 2 code not found at {config.omr.legato2.repo!r} (run omr-eval/setup.sh)"
    return None


def run_compare(ctx, engines: list[str], to_stage: str = "validate",
                keep_going: bool = False, log: Callable[[str], None] = print) -> dict:
    """Ingest once into ``ctx.workdir`` (the book dir), then run each engine in
    ``<book>/engines/<engine>/``. Returns ``{"metrics", "diffs", "notes", "ok"}``
    and writes compare.md / compare.json. ``ctx`` is a normal PipelineContext."""
    from musicocr.pipeline import run_pipeline
    from musicocr.report import build_report, write_reports

    book_dir = ctx.workdir
    notes: list[str] = []
    ing = run_pipeline(ctx, "ingest", "ingest")
    if not all(r.ok for r in ing):
        return {"metrics": [], "diffs": [], "ok": False,
                "notes": [f"ingest failed: {ing[-1].detail}"]}
    notes.append(f"Shared ingest ran once ({ing[0].duration_s:.1f}s): {ing[0].detail}")

    metrics: list[dict] = []
    seqs: dict[str, list[Unit]] = {}
    for name in engines:
        why = engine_unavailable(ctx.config, name)
        if why:
            log(f"skipping {name}: {why}")
            metrics.append({"engine": name, "status": "skipped", "reason": why})
            continue
        wd = book_dir / "engines" / name
        wd.mkdir(parents=True, exist_ok=True)
        ectx = replace(
            ctx, workdir=wd, shared_dir=book_dir,
            config=replace(ctx.config, omr=replace(ctx.config.omr, engine=name)),
            artifacts=Artifacts(
                source_pdf=ctx.artifacts.source_pdf, page_count=ctx.artifacts.page_count,
                prepped_pages=dict(ctx.artifacts.prepped_pages),
                preprocess_summary=ctx.artifacts.preprocess_summary))
        log(f"\n=== engine: {name}  ->  {wd} ===")
        results = run_pipeline(ectx, "omr", to_stage, keep_going)
        try:
            write_reports(ectx, results)
        except Exception as exc:  # noqa: BLE001
            log(f"(could not write report for {name}: {exc})")
        m = collect_metrics(name, build_report(ectx, results))
        if not m["pages"]:
            m["status"] = "failed"
            m["reason"] = (f"no score produced ({m['failed_stage'] or 'no stage'} failed)")
        metrics.append(m)
        if m["status"] == "ran":
            seqs[name] = load_sequence([Path(p) for p in ectx.artifacts.musicxml_pages])

    diffs = []
    names = list(seqs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            diffs.append({"a": a, "b": b, **diff_sequences(seqs[a], seqs[b])})

    (book_dir / "compare.md").write_text(render_markdown(ctx.book_name, metrics, diffs, notes))
    (book_dir / "compare.json").write_text(json.dumps(
        {"book": ctx.book_name, "notes": notes, "engines": metrics, "diffs": diffs}, indent=2))
    return {"metrics": metrics, "diffs": diffs, "notes": notes,
            "ok": any(m["status"] == "ran" for m in metrics)}
