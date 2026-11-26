"""One score under review: its part-lines, their status, and the fixes queued for them.

A part-line (``unit``) is one staff of one line of the original: ``unit = line * staves + staff``.

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
from typing import List, Optional

from musicocr.review import claude, images, score
from musicocr.scoreops.layout import displayed_bars, read_mscz, without_rest_placeholders, write_mscz

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
        self.parts: List[dict] = []             # one per staff: {"staff": id, "name": ...}
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

    def units(self) -> int:
        return len(self.lines) * len(self.parts)

    def locate(self, u: int):
        """``(line, staff index)`` of a unit."""
        return divmod(u, len(self.parts))

    def key(self, u: int) -> str:
        line, s = self.locate(u)
        return str(line) if len(self.parts) == 1 else f"{line}.{self.parts[s]['staff']}"

    def entry(self, u: int) -> dict:
        return self.state["lines"].setdefault(self.key(u), {"status": "unchecked", "tags": []})

    def set_entry(self, u: int, value: dict) -> None:
        self.state["lines"][self.key(u)] = value

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
            # the bars each drawn bar stands for: several for a multi-measure rest
            shown = displayed_bars(self.mscz, len(bars), self.musescore, self.cache)
            names = score.part_names(mscx)
            parts_now = [{"staff": sid, "name": names.get(sid, f"staff {sid}")} for sid in ids]
            ours = images.systems(staves, len(ids))
            theirs = images.systems(self.orig_staves, len(ids))
            lines = images.rendered_lines(bars)
            assert sum(map(len, shown)) == bars_total
            if not (len(lines) == len(ours) == len(theirs)):
                raise ReviewError(
                    f"the original has {len(theirs)} lines, the score {len(lines)}. Give the score the "
                    "original's line breaks first (`musicocr layout`, or add them in MuseScore).")
            rev = self.rev + 1
            table = []
            for i, (mine, orig, drawn) in enumerate(zip(ours, theirs, lines)):
                cells = [shown[k] for k in drawn]
                numbers = [k for cell in cells for k in cell]
                parts = []
                for s, (o, r) in enumerate(zip(orig, mine)):
                    page = self.orig_pages[o.page]
                    here, there = images.bar_edges(page, o, r, [bars[k] for k in drawn])
                    orig_img = page.crop((0, o.crop_top, page.width, o.crop_bottom))
                    ours_img = images.warp(pages[r.page], page, o, r, here, there)
                    made = {"orig": orig_img, "ours": ours_img, "overlay": images.overlay(orig_img, ours_img),
                            "orig_red": images.tint(orig_img, (215, 30, 30)),
                            "ours_blue": images.tint(ours_img, (20, 70, 220))}
                    for kind in _KINDS:
                        made[kind].save(self.image_path(i, s, kind))
                    # what to cut off top and bottom (as fractions) to show the staves as one system
                    above = images.seam(page, orig[s - 1], o) - o.crop_top if s else 0
                    below = o.crop_bottom - images.seam(page, o, orig[s + 1]) if s + 1 < len(orig) else 0
                    trim = [max(0, above) / orig_img.height, max(0, below) / orig_img.height]
                    parts.append({"staff": ids[s], "width": orig_img.width, "height": orig_img.height,
                                  "edges": there, "trim": trim})
                for sid, part in zip(ids, parts):
                    part["hash"] = score.digest(mscx, spans, [(sid, k) for k in numbers])
                table.append({"page": orig[0].page, "bars": numbers, "cells": cells, "staves": parts})
            with self.lock:
                self.lines, self.parts, self.rev, self.stamp, self.problem = table, parts_now, rev, stamp, ""

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
            units = []
            for u in range(self.units()):
                n, s = self.locate(u)
                line = self.lines[n]
                part = line["staves"][s]
                e = self.entry(u)
                status = e["status"]
                if status == "done" and e.get("hash") != part["hash"]:
                    status = "changed"
                units.append({"i": u, "line": n, "s": s, "page": line["page"], "first": line["bars"][0] + 1,
                              "last": line["bars"][-1] + 1,
                              "rests": [[c[0] + 1, c[-1] + 1] for c in line["cells"] if len(c) > 1],
                              "width": part["width"], "height": part["height"], "trim": part["trim"],
                              "status": status,
                              "tags": e.get("tags", []), "summary": e.get("summary", ""),
                              "unresolved": e.get("unresolved", []), "error": e.get("error", ""),
                              "canUndo": bool(e.get("undo"))})
            return {"title": self.mscz.name, "rev": self.rev, "problem": self.problem,
                    "claude": self.claude_problem, "parts": [p["name"] for p in self.parts], "units": units}

    # --- what the page does --------------------------------------------------

    def _hash(self, u: int) -> str:
        n, s = self.locate(u)
        return self.lines[n]["staves"][s]["hash"]

    def done(self, u: int) -> None:
        with self.lock:
            self.set_entry(u, {"status": "done", "tags": [], "hash": self._hash(u)})
            self._save()

    def reset(self, u: int) -> None:
        with self.lock:
            self.set_entry(u, {"status": "unchecked", "tags": self.entry(u).get("tags", [])})
            self._save()

    def send(self, u: int, tags: List[dict]) -> None:
        """Queue a part-line for Claude. Without tags Claude looks for the differences itself."""
        with self.lock:
            if self.entry(u)["status"] in ("queued", "working"):
                raise ReviewError("that line is already with Claude")
            clean = [{"x": float(t["x"]), "y": float(t["y"]), "note": str(t.get("note", ""))[:500]} for t in tags]
            self.set_entry(u, {"status": "queued", "tags": clean, "queued": time.time()})
            self._save()

    def undo(self, u: int) -> None:
        """Put back the bars as they were before this part-line's fix."""
        with self.lock:
            e = self.entry(u)
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
            self._write(self._replaced(mscx, back, [c["after"] for c in changes]), style)
            self.set_entry(u, {"status": "unchecked", "tags": e.get("tags", [])})
            self._save()
        self.try_refresh()

    @staticmethod
    def _replaced(mscx: str, new: dict, old: List[str]) -> str:
        """``mscx`` with the new bars; when a bar that was or becomes empty changes, the multi-measure
        rests are left for MuseScore to make again."""
        rests = any("<Chord>" not in text for text in [*old, *new.values()])
        out = score.replace_measures(mscx, new)
        return without_rest_placeholders(out) if rests else out

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
            waiting = [(e.get("queued", 0), u) for u in range(self.units())
                       if (e := self.state["lines"].get(self.key(u))) and e["status"] in ("queued", "working")]
            return min(waiting)[1] if waiting else None

    def prepare(self, u: int) -> Path:
        """Write the job folder for a part-line (or keep the one already waiting)."""
        with self.lock:
            e = self.entry(u)
            n, s = self.locate(u)
            line = self.lines[n]
            part = line["staves"][s]
            sid = part["staff"]
            old = Path(e["job"]) if e.get("job") else None
            if old and (old / "prompt.md").exists() and e.get("hash") == part["hash"]:
                return old
            job = self.cache / "jobs" / f"line{n:03d}-{s}-{int(time.time() * 1000)}"
            job.mkdir(parents=True)
            mscx = read_mscz(self.mscz)[0]
            spans = score.measure_spans(mscx)
            numbers = line["bars"]
            total = len(next(iter(spans.values())))
            near = [k for k in range(numbers[0] - 1, numbers[-1] + 2) if 0 <= k < total]

            orig = images.Image.open(self.image_path(n, s, "orig"))
            ours = images.Image.open(self.image_path(n, s, "ours"))
            over = images.Image.open(self.image_path(n, s, "overlay"))
            images.stack([orig, ours, over]).save(job / "line.png")
            listing = ["- line.png: top the original, middle our version, bottom the overlay"]
            half = orig.width // 2
            for z, (a, b) in enumerate(((0, half + 40), (half - 40, orig.width)), 1):
                zoom = images.stack([im.crop((a, 0, b, orig.height)) for im in (orig, ours, over)])
                zoom.resize((zoom.width * 2, zoom.height * 2), images.Image.LANCZOS).save(job / f"zoom{z}.png")
                listing.append(f"- zoom{z}.png: the {'left' if z == 1 else 'right'} half, enlarged")
            if e["tags"]:
                images.marked(orig, [(t["x"], t["y"]) for t in e["tags"]]).save(job / "tags.png")
                listing.append("- tags.png: the original with the user's numbered marks")

            xml, sent = [], []
            for k in near:
                text = score.get_measure(mscx, spans, (sid, k))
                xml.append(f"<!-- staff {sid}, bar {k + 1} -->\n      {text}")
                sent.append({"staff": sid, "bar": k, "xml": text})
            (job / "measures.xml").write_text("\n".join(xml) + "\n", "utf-8")
            (job / "sent.json").write_text(json.dumps(sent), "utf-8")

            marks = []
            for m, t in enumerate(e["tags"], 1):
                cell, across = images.bar_at(t["x"], part["width"], part["edges"])
                bars = line["cells"][min(cell, len(line["cells"]) - 1)]
                where = (f"the multi-measure rest at bars {bars[0] + 1}-{bars[-1] + 1}" if len(bars) > 1
                         else f"bar {bars[0] + 1}")
                marks.append(f"- mark {m} on {where}, about {across}% across it: "
                             f"{t['note'] or '(no comment: find what differs there)'}")
            if marks:
                tags = ("The user marked these places as wrong. Fix them; also mention in 'unresolved' any other "
                        "difference you notice, without changing it.\n" + "\n".join(marks) + "\n")
            else:
                tags = "The user did not mark anything: find every difference yourself.\n"
            context = f"staff {sid} ({self.parts[s]['name']}): {score.context_at(mscx, spans, sid, numbers[0])}"
            rests = [f"bars {c[0] + 1}-{c[-1] + 1}" for c in line["cells"] if len(c) > 1]
            if rests:
                context += (". Drawn as multi-measure rests in our version (each a run of empty bars in the XML): "
                            + ", ".join(rests) + ". You can only replace bars, not add or remove them: if the "
                            "original's rest count differs, report it in 'unresolved'")
            (job / "prompt.md").write_text(claude.PROMPT.format(
                images="\n".join(listing), first=numbers[0] + 1, last=numbers[-1] + 1,
                context=context, tags=tags), "utf-8")
            e.update(job=str(job), hash=part["hash"])
            self._save()
            return job

    def apply(self, i: int, job: Path, reply: dict) -> None:
        """Check Claude's replacement bars and write the good ones into the score."""
        sent = {(m["staff"], m["bar"]): m["xml"] for m in json.loads((job / "sent.json").read_text("utf-8"))}
        with self.lock:
            mscx, style = read_mscz(self.mscz)
            spans = score.measure_spans(mscx)
            # bar lengths follow the time signatures as they will be: a fix may correct a misread one
            offered = {(c["staff"], c["bar"] - 1): c["measure"].strip() for c in reply["changes"]
                       if (c["staff"], c["bar"] - 1) in sent and score.is_measure(c["measure"])}
            after = score.replace_measures(mscx, offered)
            after_spans = score.measure_spans(after)
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
                                              score.time_at(after, after_spans, key[0], key[1]))
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
                self._write(self._replaced(mscx, new, [c["before"] for c in undo]), style)
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
        if i is None:
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
