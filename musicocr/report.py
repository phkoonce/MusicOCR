"""Write report.json and report.md from the pipeline context + stage results."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from musicocr.pipeline import PipelineContext, StageResult

_SEV_ICON = {"error": "🔴", "warn": "🟡", "info": "🔵"}


def _rel(p, base: Path) -> str:
    try:
        return str(Path(p).relative_to(base))
    except (ValueError, TypeError):
        return str(p)


def build_report(ctx: PipelineContext, results: list[StageResult]) -> dict:
    a = ctx.artifacts
    val = a.get("validation", {})
    outputs = {k: str(v) for k, v in a.get("outputs", {}).items()}
    if a.get("musicxml"):
        outputs["musicxml"] = str(a["musicxml"])
    return {
        "book": ctx.book_name,
        "input_pdf": str(ctx.input_pdf),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "workdir": str(ctx.workdir),
        "page_count": a.get("page_count"),
        "profile": ctx.config.omr_profile,
        "constants": ctx.constants(),
        "stages": [
            {"name": r.name, "status": r.status, "detail": r.detail,
             "duration_s": round(r.duration_s, 2)}
            for r in results
        ],
        "outputs": outputs,
        "omr_project": str(a.get("omr_project") or ""),
        "validation": val,
        "qa_pages": a.get("qa_pages", {}),
    }


def _md(report: dict, base: Path) -> str:
    L: list[str] = []
    L.append(f"# MusicOCR report — {report['book']}")
    L.append("")
    L.append(f"- **Input:** `{report['input_pdf']}`")
    L.append(f"- **Pages:** {report.get('page_count', '?')}")
    L.append(f"- **Generated:** {report['generated']}")
    L.append(f"- **OMR profile:** `{report['profile']}`"
             + (f" + constants `{report['constants']}`" if report["constants"] else ""))
    if report.get("omr_project"):
        L.append(f"- **Audiveris project (for GUI correction):** "
                 f"`{_rel(report['omr_project'], base)}`")
    L.append("")

    L.append("## Stages")
    L.append("")
    L.append("| stage | status | detail | time |")
    L.append("|---|---|---|---|")
    for s in report["stages"]:
        L.append(f"| {s['name']} | {s['status']} | {s['detail']} | {s['duration_s']}s |")
    L.append("")

    L.append("## Outputs")
    L.append("")
    if report["outputs"]:
        for k, v in report["outputs"].items():
            L.append(f"- **{k}:** `{_rel(v, base)}`")
    else:
        L.append("_none produced_")
    L.append("")

    val = report.get("validation") or {}
    stats = val.get("stats", {})
    counts = val.get("counts", {})
    L.append("## Validation")
    L.append("")
    if stats:
        L.append(f"- Parts: {stats.get('parts')}, measures/part: "
                 f"{stats.get('measures_per_part')}")
        L.append(f"- Notes: {stats.get('notes')}, rests: {stats.get('rests')}, "
                 f"tuplets: {stats.get('tuplets')}, accidentals: {stats.get('accidentals')}")
    L.append(f"- Findings: {counts.get('error', 0)} error, "
             f"{counts.get('warn', 0)} warn, {counts.get('info', 0)} info")
    L.append("")
    findings = val.get("findings", [])
    if findings:
        L.append("| sev | code | where | message |")
        L.append("|---|---|---|---|")
        order = {"error": 0, "warn": 1, "info": 2}
        for f in sorted(findings, key=lambda f: (order.get(f["severity"], 9),
                                                 f.get("part", ""), f.get("measure", 0))):
            where = f.get("part", "")
            if f.get("measure") is not None and "measure" in f:
                where += f" m.{f['measure']}"
            icon = _SEV_ICON.get(f["severity"], "")
            L.append(f"| {icon} {f['severity']} | {f['code']} | {where.strip()} | {f['message']} |")
    else:
        L.append("_no findings_")
    L.append("")

    qa = report.get("qa_pages") or {}
    src, omr = qa.get("src", []), qa.get("omr", [])
    if src or omr:
        L.append("## QA — source scan vs rendered score")
        L.append("")
        for i in range(max(len(src), len(omr))):
            L.append(f"### Page {i + 1}")
            L.append("")
            if i < len(src):
                L.append(f"Source: `{_rel(src[i], base)}`")
                L.append("")
                L.append(f"![source page {i + 1}]({_rel(src[i], base)})")
                L.append("")
            if i < len(omr):
                L.append(f"Rendered: `{_rel(omr[i], base)}`")
                L.append("")
                L.append(f"![rendered page {i + 1}]({_rel(omr[i], base)})")
                L.append("")
    return "\n".join(L) + "\n"


def write_reports(ctx: PipelineContext, results: list[StageResult]) -> tuple[Path, Path]:
    report = build_report(ctx, results)
    json_path = ctx.workdir / "report.json"
    md_path = ctx.workdir / "report.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(_md(report, ctx.workdir))
    return json_path, md_path
