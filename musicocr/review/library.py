"""Several scores under review from one server: a list of pieces, each opened as a ``Session``.

Opening a score renders it, so sessions are made when first asked for and, in the background, one
after another. One lock covers all of it: only one MuseScore runs at a time.
"""
from __future__ import annotations

import html
import json
import re
import threading
import zipfile
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List

from musicocr.review.session import ReviewError, Session


def _title(mscz: Path) -> str:
    try:
        with zipfile.ZipFile(mscz) as z:
            name = next(n for n in z.namelist() if n.endswith(".mscx"))
            found = re.search(r'<metaTag name="workTitle">([^<]+)<', z.read(name).decode("utf-8"))
    except (OSError, StopIteration, zipfile.BadZipFile):
        found = None
    return html.unescape(found.group(1)) if found else mscz.stem


class Library:
    def __init__(self, scores: List[Path], open_session: Callable[[Path], Session]):
        self.scores = [Path(p).resolve() for p in scores]
        self.titles = [_title(p) for p in self.scores]
        self._open = open_session
        self.sessions: Dict[int, Session] = {}
        self.errors: Dict[int, str] = {}
        self.lock = threading.RLock()          # one score is opened or redrawn at a time

    def session(self, k: int, retry: bool = False) -> Session:
        if k in self.sessions:                 # never wait behind another score being opened
            return self.sessions[k]
        with self.lock:
            if k not in self.sessions:
                if k in self.errors and not retry:
                    raise ReviewError(self.errors[k])
                try:
                    made = self._open(self.scores[k])
                except Exception as exc:  # noqa: BLE001 - shown beside the piece
                    self.errors[k] = str(exc)
                    raise ReviewError(str(exc)) from exc
                made.render_lock = self.lock
                self.errors.pop(k, None)
                self.sessions[k] = made
            return self.sessions[k]

    def index(self) -> str:
        """The list of pieces, with progress as saved beside each score."""
        rows = []
        for k, (path, title) in enumerate(zip(self.scores, self.titles)):
            try:
                saved = json.loads(path.with_suffix(".review.json").read_text("utf-8"))["lines"]
            except (OSError, ValueError, KeyError):
                saved = {}
            count = Counter(e.get("status") for e in saved.values())
            done = [f"{count[s]} {label}" for s, label in
                    (("done", "done"), ("check", "to re-check"), ("queued", "waiting"),
                     ("working", "with Claude"), ("failed", "failed")) if count[s]]
            session = self.sessions.get(k)
            if session:
                done = [f"{sum(u['status'] == 'done' for u in session.view()['units'])}/{session.units()} done",
                        *[d for d in done if not d.endswith(" done")]]
            note = self.errors.get(k, ", ".join(done))
            rows.append(f'<tr><td>{k + 1}</td><td><a href="/s/{k}/">{html.escape(title)}</a></td>'
                        f'<td{" class=bad" if k in self.errors else ""}>{html.escape(note)}</td></tr>')
        return ('<!doctype html><meta charset="utf-8"><title>Score review</title><style>'
                "body{font:15px/1.5 -apple-system,system-ui,sans-serif;margin:24px}td{padding:2px 14px 2px 0}"
                ".bad{color:#b00}</style>"
                f"<h1>{len(self.scores)} pieces</h1><table>{''.join(rows)}</table>")

    def _preload(self, stop: threading.Event) -> None:
        for k in range(len(self.scores)):
            if stop.is_set():
                return
            try:
                self.session(k)
            except ReviewError:
                pass

    def run_worker(self, stop: threading.Event, interval: float = 3.0) -> None:
        """Open every score, redrawing changed ones and doing queued fixes (one at a time) meanwhile."""
        threading.Thread(target=self._preload, args=(stop,), daemon=True).start()
        while not stop.is_set():
            busy = False
            for session in list(self.sessions.values()):
                if session.changed_on_disk():
                    session.try_refresh()
                busy = session.work_once() or busy
            if not busy:
                stop.wait(interval)
