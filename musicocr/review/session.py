"""One score under review: its lines, their status, and the fixes queued for them.

Progress lives in ``<score>.review.json`` beside the score. Images and job folders are regenerable and
go under ``ttl=30d/review/``. The score is changed only by applying a fix; the first time, a copy is
kept as ``<score>.before-review.mscz``.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from musicocr.review import claude, images, score
from musicocr.scoreops.layout import read_mscz, write_mscz

_KINDS = ("orig", "ours", "orig_red", "ours_blue", "overlay")
_RETRY = 60.0          # seconds before trying a missing or signed-out `claude` again


class ReviewError(Exception):
    pass


class Session:
    def __init__(self, mscz: Path, source: Optional[Path], musescore: str, pdftoppm: str = "pdftoppm",
                 claude_tool: Optional[str] = None, model: str = "", renderer=None):
        self.mscz = Path(mscz).resolve()
        self.state_path = self.mscz.with_suffix(".review.json")
        self.cache = self.mscz.parent / "ttl=30d" / "review" / self.mscz.stem
        self.musescore, self.pdftoppm = musescore, pdftoppm
        self.claude_tool, self.model = claude_tool, model
        self.renderer = renderer or images.render_score
        self.state = self._load()
        if source:
            self.state["source"] = str(Path(source).expanduser().resolve())
        if not self.state.get("source"):
            fallback = sorted(self.mscz.parent.glob("ttl=*/source.pdf"))
            if not fallback:
                raise ReviewError("give the original PDF with --source")
            self.state["source"] = str(fallback[0])
        if not Path(self.state["source"]).is_file():
            raise ReviewError(f"original not found: {self.state['source']}")

        self.lock = threading.RLock()          # guards state and the line table
        self.render_lock = threading.Lock()    # one MuseScore at a time
        self.lines: List[dict] = []
        self.rev = 0                           # changes whenever the images do
        self.stamp = 0.0
        self.problem = ""                      # why the images may be out of date
        self.claude_problem = "" if claude_tool else "the `claude` command was not found"
        self.retry_at = 0.0
        self.cache.mkdir(parents=True, exist_ok=True)
        self.orig_pages, self.orig_staves = images.render_original(
            Path(self.state["source"]), self.cache, pdftoppm)
        self.refresh()
        self._save()

    # --- state ---------------------------------------------------------------

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text("utf-8"))
        return {"version": 1, "lines": {}}

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=1), "utf-8")
        os.replace(tmp, self.state_path)

    def entry(self, i: int) -> dict:
        return self.state["lines"].setdefault(str(i), {"status": "unchecked", "tags": []})

    # --- images --------------------------------------------------------------

    def changed_on_disk(self) -> bool:
        try:
            return self.mscz.stat().st_mtime != self.stamp
        except OSError:
            return False

    def refresh(self) -> None:
        """Render the score and cut it into lines matching the original's."""
        with self.render_lock:
            stamp = self.mscz.stat().st_mtime
            mscx = read_mscz(self.mscz)[0]
            spans = score.measure_spans(mscx)
            ids = sorted(spans)
            bars_total = len(spans[ids[0]])
            pages, staves, bars = self.renderer(self.mscz, self.cache, self.musescore)
            if len(bars) != bars_total:
                raise ReviewError(f"MuseScore reports {len(bars)} bars for a score of {bars_total} "
                                  "(multi-measure rests are not supported yet)")
            ours = images.systems(staves, len(ids))
            theirs = images.systems(self.orig_staves, len(ids))
            lines = images.rendered_lines(bars)
            if not (len(lines) == len(ours) == len(theirs)):
                raise ReviewError(
                    f"the original has {len(theirs)} lines, the score {len(lines)}. Give the score the "
                    "original's line breaks first (`musicocr layout`, or add them in MuseScore).")
            rev = self.rev + 1
            table = []
            for i, (mine, orig, numbers) in enumerate(zip(ours, theirs, lines)):
                parts = []
                for s, (o, r) in enumerate(zip(orig, mine)):
                    page = self.orig_pages[o.page]
                    here, there = images.bar_edges(page, o, r, [bars[k] for k in numbers])
                    orig_img = page.crop((0, o.crop_top, page.width, o.crop_bottom))
                    ours_img = images.warp(pages[r.page], page, o, r, here, there)
                    made = {"orig": orig_img, "ours": ours_img, "overlay": images.overlay(orig_img, ours_img),
                            "orig_red": images.tint(orig_img, (215, 30, 30)),
                            "ours_blue": images.tint(ours_img, (20, 70, 220))}
                    for kind in _KINDS:
                        made[kind].save(self.image_path(i, s, kind))
                    parts.append({"staff": ids[s], "width": orig_img.width, "height": orig_img.height,
                                  "edges": there})
                keys = [(sid, k) for sid in ids for k in numbers]
                table.append({"page": orig[0].page, "bars": numbers, "staves": parts,
                              "hash": score.digest(mscx, spans, keys)})
            with self.lock:
                self.lines, self.rev, self.stamp, self.problem = table, rev, stamp, ""

    def image_path(self, line: int, staff: int, kind: str) -> Path:
        if kind not in _KINDS:
            raise ReviewError(f"no such image: {kind}")
        return self.cache / f"line{line:03d}-{staff}-{kind}.png"

    def try_refresh(self) -> None:
        stamp = self.mscz.stat().st_mtime if self.mscz.exists() else 0.0
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001 - shown on the page; the last good images stay
            with self.lock:
                self.problem, self.stamp = str(exc), stamp

    # --- what the page shows -------------------------------------------------

    def view(self) -> dict:
        with self.lock:
            lines = []
            for i, line in enumerate(self.lines):
                e = self.entry(i)
                status = e["status"]
                if status == "done" and e.get("hash") != line["hash"]:
                    status = "changed"
                lines.append({"i": i, "page": line["page"], "first": line["bars"][0] + 1,
                              "last": line["bars"][-1] + 1, "staves": line["staves"], "status": status,
                              "tags": e.get("tags", []), "summary": e.get("summary", ""),
                              "unresolved": e.get("unresolved", []), "error": e.get("error", ""),
                              "canUndo": bool(e.get("undo"))})
            return {"title": self.mscz.name, "rev": self.rev, "problem": self.problem,
                    "claude": self.claude_problem, "lines": lines}

    # --- what the page does --------------------------------------------------

    def done(self, i: int) -> None:
        with self.lock:
            self.state["lines"][str(i)] = {"status": "done", "tags": [], "hash": self.lines[i]["hash"]}
            self._save()

    def reset(self, i: int) -> None:
        with self.lock:
            e = self.entry(i)
            self.state["lines"][str(i)] = {"status": "unchecked", "tags": e.get("tags", [])}
            self._save()

    def send(self, i: int, tags: List[dict]) -> None:
        """Queue line ``i`` for Claude. Without tags Claude looks for the differences itself."""
        with self.lock:
            if self.entry(i)["status"] in ("queued", "working"):
                raise ReviewError("that line is already with Claude")
            clean = [{"staff": int(t.get("staff", 0)), "x": float(t["x"]), "y": float(t["y"]),
                      "note": str(t.get("note", ""))[:500]} for t in tags]
            self.state["lines"][str(i)] = {"status": "queued", "tags": clean, "queued": time.time()}
            self._save()

    def undo(self, i: int) -> None:
        """Put back the bars as they were before line ``i``'s fix."""
        with self.lock:
            e = self.entry(i)
            changes = e.get("undo")
            if not changes:
                raise ReviewError("nothing to undo")
            mscx, style = read_mscz(self.mscz)
            spans = score.measure_spans(mscx)
            back = {}
            for c in changes:
                key = c["staff"], c["bar"]
                if score.canonical(score.get_measure(mscx, spans, key)) != score.canonical(c["after"]):
                    raise ReviewError(f"bar {c['bar'] + 1} has changed since the fix; undo it in MuseScore")
                back[key] = c["before"]
            self._write(score.replace_measures(mscx, back), style)
            self.state["lines"][str(i)] = {"status": "unchecked", "tags": e.get("tags", [])}
            self._save()
        self.try_refresh()

    def _write(self, mscx: str, style: str) -> None:
        keep = self.mscz.with_suffix(".before-review.mscz")
        if not keep.exists():
            shutil.copy2(self.mscz, keep)
        tmp = self.cache / "writing.mscz"
        write_mscz(self.mscz, tmp, mscx, style)
        os.replace(tmp, self.mscz)

    # --- fixes ---------------------------------------------------------------

    def next_queued(self) -> Optional[int]:
        with self.lock:
            waiting = [(e.get("queued", 0), int(k)) for k, e in self.state["lines"].items()
                       if e["status"] in ("queued", "working")]
            return min(waiting)[1] if waiting else None

    def prepare(self, i: int) -> Path:
        """Write the job folder for line ``i`` (or keep the one already waiting)."""
        with self.lock:
            e = self.entry(i)
            line = self.lines[i]
            old = Path(e["job"]) if e.get("job") else None
            if old and (old / "prompt.md").exists() and e.get("hash") == line["hash"]:
                return old
            job = self.cache / "jobs" / f"line{i:03d}-{int(time.time() * 1000)}"
            job.mkdir(parents=True)
            mscx = read_mscz(self.mscz)[0]
            spans = score.measure_spans(mscx)
            numbers = line["bars"]
            total = len(next(iter(spans.values())))
            near = [k for k in range(numbers[0] - 1, numbers[-1] + 2) if 0 <= k < total]

            listing, sent = [], []
            for s, part in enumerate(line["staves"]):
                sid = part["staff"]
                orig = images.Image.open(self.image_path(i, s, "orig"))
                ours = images.Image.open(self.image_path(i, s, "ours"))
                over = images.Image.open(self.image_path(i, s, "overlay"))
                name = f"staff{sid}.png"
                images.stack([orig, ours, over]).save(job / name)
                listing.append(f"- {name}: staff {sid}; top the original, middle our version, bottom the overlay")
                half = orig.width // 2
                for n, (a, b) in enumerate(((0, half + 40), (half - 40, orig.width)), 1):
                    crops = [im.crop((a, 0, b, orig.height)) for im in (orig, ours, over)]
                    zoom = images.stack(crops)
                    zname = f"staff{sid}-zoom{n}.png"
                    zoom.resize((zoom.width * 2, zoom.height * 2), images.Image.LANCZOS).save(job / zname)
                    listing.append(f"- {zname}: the {'left' if n == 1 else 'right'} half, enlarged")
                points = [(t["x"], t["y"]) for t in e["tags"] if t["staff"] == s]
                if points:
                    mname = f"staff{sid}-tags.png"
                    images.marked(orig, points).save(job / mname)
                    listing.append(f"- {mname}: the original with the user's numbered marks")

            xml = []
            for part in line["staves"]:
                for k in near:
                    text = score.get_measure(mscx, spans, (part["staff"], k))
                    xml.append(f"<!-- staff {part['staff']}, bar {k + 1} -->\n      {text}")
                    sent.append({"staff": part["staff"], "bar": k, "xml": text})
            (job / "measures.xml").write_text("\n".join(xml) + "\n", "utf-8")
            (job / "sent.json").write_text(json.dumps(sent), "utf-8")

            marks = []
            counts: Dict[int, int] = {}
            for t in e["tags"]:
                part = line["staves"][t["staff"]]
                counts[t["staff"]] = n = counts.get(t["staff"], 0) + 1
                bar, across = images.bar_at(t["x"], part["width"], part["edges"])
                marks.append(f"- mark {n} on staff {part['staff']}, bar {numbers[min(bar, len(numbers) - 1)] + 1}, "
                             f"about {across}% across it: {t['note'] or '(no comment: find what differs there)'}")
            if marks:
                tags = ("The user marked these places as wrong. Fix them; also mention in 'unresolved' any other "
                        "difference you notice, without changing it.\n" + "\n".join(marks) + "\n")
            else:
                tags = "The user did not mark anything: find every difference yourself.\n"
            context = "; ".join(f"staff {p['staff']}: {score.context_at(mscx, spans, p['staff'], numbers[0])}"
                                for p in line["staves"])
            (job / "prompt.md").write_text(claude.PROMPT.format(
                images="\n".join(listing), first=numbers[0] + 1, last=numbers[-1] + 1,
                context=context, tags=tags), "utf-8")
            e.update(job=str(job), hash=line["hash"])
            self._save()
            return job

    def apply(self, i: int, job: Path, reply: dict) -> None:
        """Check Claude's replacement bars and write the good ones into the score."""
        sent = {(m["staff"], m["bar"]): m["xml"] for m in json.loads((job / "sent.json").read_text("utf-8"))}
        with self.lock:
            mscx, style = read_mscz(self.mscz)
            spans = score.measure_spans(mscx)
            new, refused = {}, []
            for c in reply["changes"]:
                key = c["staff"], c["bar"] - 1
                if key not in sent:
                    refused.append(f"bar {c['bar']} staff {c['staff']}: not one of the bars sent")
                    continue
                now = score.get_measure(mscx, spans, key)
                if score.canonical(now) != score.canonical(sent[key]):
                    raise ReviewError(f"bar {c['bar']} changed while Claude was working; send the line again")
                why = score.check_replacement(now, c["measure"].strip(),
                                              score.time_at(mscx, spans, key[0], key[1]))
                if why:
                    refused.append(f"bar {c['bar']}: {why}")
                elif score.canonical(c["measure"]) != score.canonical(now):
                    new[key] = c["measure"].strip()
            # A slur's two ends are in two bars: applying only one of them would corrupt the score.
            if refused:
                raise ReviewError("nothing applied: " + "; ".join(refused))
            undo = [{"staff": s, "bar": k, "before": score.get_measure(mscx, spans, (s, k)), "after": t}
                    for (s, k), t in new.items()]
            if new:
                self._write(score.replace_measures(mscx, new), style)
        # redraw before reporting the line, so "re-check" never shows the old pictures
        if new:
            self.try_refresh()
        with self.lock:
            e = self.entry(i)
            summary = str(reply.get("summary", ""))
            e.update(status="check", undo=undo, error="", tags=[],
                     summary=summary if new else "No change made. " + summary,
                     unresolved=[str(u) for u in reply.get("unresolved", [])])
            e.pop("job", None)
            self._save()

    def work_once(self) -> bool:
        """Do the oldest queued fix. False when there is nothing that can be done now."""
        i = self.next_queued()
        if i is None or i >= len(self.lines):
            return False
        try:
            job = self.prepare(i)
            if time.time() < self.retry_at and not (job / "result.json").exists():
                return False
            with self.lock:
                self.entry(i)["status"] = "working" if (self.claude_tool or (job / "result.json").exists()) \
                    else "queued"
                self._save()
            reply = claude.run_job(job, self.claude_tool, self.model)
            self.claude_problem = ""
            self.apply(i, job, reply)
        except claude.ClaudeUnavailable as exc:
            with self.lock:
                self.claude_problem = str(exc)
                self.retry_at = time.time() + _RETRY
                self.entry(i)["status"] = "queued"
                self._save()
            return False
        except Exception as exc:  # noqa: BLE001 - one line's failure is reported on that line
            with self.lock:
                e = self.entry(i)
                e.update(status="failed", error=str(exc))
                e.pop("job", None)
                self._save()
        return True

    def run_worker(self, stop: threading.Event, interval: float = 3.0) -> None:
        while not stop.is_set():
            if self.changed_on_disk():
                self.try_refresh()
            if not self.work_once():
                stop.wait(interval)
