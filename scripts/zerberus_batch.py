#!/usr/bin/env python3
"""Resumable batch: split the Zerberus score+parts PDF into per-part PDFs and run
each through the pipeline (homr). Safe to stop and re-run: finished parts are
skipped. Progress: docs/zerberus-progress.md (tracked) + output/zerberus_batch.log.

  .venv/bin/python scripts/zerberus_batch.py [--only Zerberus_hn01,...] [--redo]
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path.home() / "Library/CloudStorage/GoogleDrive-philip.koonce@gmail.com/My Drive/Music/Concerts/2026-10 LB/Zerberus_Lake_Brass_Partitur - Partitur und Stimmen.pdf"
IN = ROOT / "input/zerberus"
STATE = ROOT / "output/zerberus_state.json"
DOC = ROOT / "docs/zerberus-progress.md"
# (key, label, first page, last page) in the 77-page score+parts PDF
PARTS = [(f"Zerberus_hn{i:02d}", f"Horn in F {i}", a, b) for i, (a, b) in enumerate(
    [(19,21),(22,24),(25,27),(28,30),(31,34),(35,38),(39,41),(42,44),(45,47),(48,50),(51,53),(54,56)], 1)] + [
    ("Zerberus_eu1", "Euphonium 1", 57, 59), ("Zerberus_eu2", "Euphonium 2", 60, 62),
    ("Zerberus_eu3", "Euphonium 3", 63, 65), ("Zerberus_tuba", "Tuba", 66, 68),
    ("Zerberus_pauken", "Pauken", 69, 71), ("Zerberus_perc1", "Perkussion 1", 72, 73),
    ("Zerberus_perc2", "Perkussion 2", 74, 77)]

def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def write_doc(st):
    rows = ["| Part | Pages | Status | Notes |", "|---|---|---|---|"]
    for k, label, a, b in PARTS:
        s = st.get(k, {})
        rows.append(f"| {label} (`{k}`) | {a}-{b} | {s.get('status','pending')} | {s.get('note','')} |")
    DOC.write_text("# Zerberus OMR progress\n\nAuto-written by `scripts/zerberus_batch.py` "
                   f"(updated {time.strftime('%Y-%m-%d %H:%M')}). Outputs in `output/<key>/`; "
                   "per-part PDFs in `input/zerberus/`. Re-run the script to resume.\n\n" + "\n".join(rows) + "\n")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    st = load(); IN.mkdir(parents=True, exist_ok=True)
    for k, label, p1, p2 in PARTS:
        if only and k not in only: continue
        if st.get(k, {}).get("status") == "done" and not a.redo: continue
        pdf = IN / f"{k}.pdf"
        if not pdf.exists():
            tmp = IN / f"_{k}-%d.pdf"
            subprocess.run(["pdfseparate", "-f", str(p1), "-l", str(p2), str(SRC), str(tmp)], check=True)
            pieces = [str(IN / f"_{k}-{n}.pdf") for n in range(p1, p2 + 1)]
            subprocess.run(["pdfunite", *pieces, str(pdf)], check=True)
            for x in pieces: Path(x).unlink()
        st[k] = {"status": "running", "note": f"started {time.strftime('%H:%M')}"}; STATE.write_text(json.dumps(st)); write_doc(st)
        t = time.time()
        log = ROOT / "output" / f"{k}.run.log"
        with open(log, "w") as f:
            rc = subprocess.run([str(ROOT / "run.sh"), "run", str(pdf), "--keep-going"], stdout=f, stderr=subprocess.STDOUT).returncode
        out = ROOT / "output" / k / f"{k}.musicxml"
        ok = out.exists() and out.stat().st_size > 0
        st[k] = {"status": "done" if ok else "FAILED", "note": f"{(time.time()-t)/60:.1f} min, rc={rc}; see output/{k}/report.md" if ok else f"rc={rc}; see output/{k}.run.log"}
        STATE.write_text(json.dumps(st)); write_doc(st)
        print(k, st[k], flush=True)

if __name__ == "__main__":
    main()
