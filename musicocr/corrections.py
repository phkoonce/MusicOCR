"""Keep hand-made and paid-for corrections of a score, and put them back after a rerun.

A correction is whatever differs between the score as the pipeline made it (the baseline) and the score
now, however it got there: MuseScore, the review page or a script.

    python -m musicocr.corrections capture BASELINE.mscz CURRENT.mscz STORE.json
    python -m musicocr.corrections apply STORE.json TARGET.mscz [--dry-run]

``apply`` changes a bar only where the old bar is still the one that was corrected, even if bar numbers
have moved. Everything else is listed, not guessed. Added or deleted bars are recorded and reported
but not applied.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from musicocr.review import score
from musicocr.scoreops.layout import read_mscz, without_rest_placeholders, write_mscz

# layout belongs to the run that made the score, not to a correction
_LAYOUT = re.compile(r"[ \t]*<(LayoutBreak|stretch)\b[^>]*?(?:/>|>.*?</\1>)\n?", re.S)


# MuseScore rewrites text styling when it saves: it says nothing about the music
_STYLING = re.compile(r"<(size|bold|italic)>[^<]*</\1>|</?(?:b|i)\s*/?>")


def _strip(measure: str) -> str:
    return _LAYOUT.sub("", measure)


def _hash(measure: Optional[str]) -> str:
    if measure is None:
        return ""
    text = _STYLING.sub("", score.canonical(_strip(measure)))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _bars(mscx: str) -> Dict[int, List[str]]:
    return {staff: [mscx[a:b] for a, b in spans] for staff, spans in score.measure_spans(mscx).items()}


def _at(items: list, i: int) -> Optional[str]:
    return items[i] if 0 <= i < len(items) else None


def _changed_blocks(base: List[str], now: List[str]):
    """``(i1, i2, j1, j2)``: bars ``base[i1:i2]`` are now ``now[j1:j2]``."""
    a, b = [_hash(m) for m in base], [_hash(m) for m in now]
    if len(a) != len(b):
        ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        return [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in ops if tag != "equal"]
    out, i = [], 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        j = i
        while j < len(a) and a[j] != b[j]:
            j += 1
        out.append((i, j, i, j))
        i = j
    return out


def _identity(entry: dict) -> tuple:
    return entry["staff"], tuple(entry["before"]), entry["prev"], entry["next"]


def capture(baseline: Path, current: Path, store: Path) -> dict:
    """Add the differences between the two scores to ``store``; returns counts."""
    old, new = _bars(read_mscz(baseline)[0]), _bars(read_mscz(current)[0])
    if old.keys() != new.keys():
        raise score.ScoreError("the two scores have different staves")
    found = []
    for staff, base in old.items():
        for i1, i2, j1, j2 in _changed_blocks(base, new[staff]):
            found.append({
                "staff": staff, "bar": i1 + 1,
                "before": [_hash(m) for m in base[i1:i2]],
                "after": [score._EID.sub("", _strip(m)) for m in new[staff][j1:j2]],
                "prev": _hash(_at(base, i1 - 1)), "next": _hash(_at(base, i2)),
            })
    data = json.loads(store.read_text("utf-8")) if store.exists() else {"version": 1, "corrections": []}
    kept = {_identity(e): e for e in data["corrections"]}
    reverted = 0
    for key, e in list(kept.items()):       # a bar this baseline has, now back to what it was
        base, n = old.get(e["staff"], []), len(e["before"])
        i = e["bar"] - 1
        unchanged = n and [_hash(m) for m in base[i:i + n]] == e["before"] \
            and [_hash(m) for m in new.get(e["staff"], [])[i:i + n]] == e["before"]
        if unchanged and len(base) == len(new[e["staff"]]):
            del kept[key]
            reverted += 1
    added = sum(_identity(e) not in kept for e in found)
    kept.update({_identity(e): e for e in found})
    data["corrections"] = sorted(kept.values(), key=lambda e: (e["bar"], e["staff"]))
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data, indent=1), "utf-8")
    return {"differences": len(found), "new": added, "dropped": reverted, "stored": len(data["corrections"])}


def _locate(entry: dict, bars: List[str]):
    """``(position, None)`` of the bars to replace, or ``(None, why not)``."""
    hashes = [_hash(m) for m in bars]
    n = len(entry["before"])
    if not n or len(entry["after"]) != n:
        return None, "adds or removes bars: do this one by hand"
    after = [_hash(m) for m in entry["after"]]

    def neighbours(i):
        return (_at(hashes, i - 1) or "") == entry["prev"], (_at(hashes, i + n) or "") == entry["next"]

    places = [i for i in range(len(hashes) - n + 1) if hashes[i:i + n] == entry["before"]
              # a common bar (a rest) must not be taken for another one far away
              and (i == entry["bar"] - 1 or any(neighbours(i)))]
    if places:
        best = max(sum(neighbours(i)) * 2 + (i == entry["bar"] - 1) for i in places)
        top = [i for i in places if sum(neighbours(i)) * 2 + (i == entry["bar"] - 1) == best]
        if len(top) == 1:
            return top[0], None
        return None, "several places look the same"
    done = [i for i in range(len(hashes) - n + 1) if hashes[i:i + n] == after and any(neighbours(i))]
    return None, "already applied" if done else "the bar now reads differently"


def apply(store: Path, target: Path, dry_run: bool = False) -> dict:
    """Put the stored corrections into ``target`` (a copy is kept as ``*.before-corrections.mscz``)."""
    mscx, style = read_mscz(target)
    spans = score.measure_spans(mscx)
    bars = _bars(mscx)
    new, applied, already, skipped = {}, [], [], []
    for e in json.loads(store.read_text("utf-8"))["corrections"]:
        where = f"staff {e['staff']} bar {e['bar']}"
        i, why = _locate(e, bars.get(e["staff"], []))
        if i is None:
            (already if why == "already applied" else skipped).append(where if why == "already applied"
                                                                      else f"{where}: {why}")
            continue
        keys = [(e["staff"], i + k) for k in range(len(e["after"]))]
        if any(k in new for k in keys):
            skipped.append(f"{where}: overlaps another correction")
            continue
        for key, text in zip(keys, e["after"]):
            layout = "".join(m.group() for m in _LAYOUT.finditer(score.get_measure(mscx, spans, key)))
            new[key] = text.replace("<voice>", layout + "<voice>", 1) if layout else text
        applied.append(where if i == e["bar"] - 1 else f"{where} (now bar {i + 1})")
    if new and not dry_run:
        rests = any("<Chord>" not in t for t in [*new.values(), *(score.get_measure(mscx, spans, k) for k in new)])
        out = score.replace_measures(mscx, new)
        keep = target.with_suffix(".before-corrections.mscz")
        if not keep.exists():
            shutil.copy2(target, keep)
        write_mscz(target, target.with_suffix(".writing"), without_rest_placeholders(out) if rests else out, style)
        target.with_suffix(".writing").replace(target)
    return {"applied": applied, "already": already, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m musicocr.corrections", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("baseline", type=Path)
    c.add_argument("current", type=Path)
    c.add_argument("store", type=Path)
    a = sub.add_parser("apply")
    a.add_argument("store", type=Path)
    a.add_argument("target", type=Path)
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        print(json.dumps(capture(args.baseline, args.current, args.store)))
        return 0
    result = apply(args.store, args.target, args.dry_run)
    print(f"{len(result['applied'])} applied, {len(result['already'])} already there, "
          f"{len(result['skipped'])} not applied")
    for line in result["skipped"]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
