"""A local web page for a review session. Listens on this machine only."""
from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from musicocr.review.library import Library
from musicocr.review.session import ReviewError, Session

_PAGE = Path(__file__).with_name("page.html")
_IMAGE = re.compile(r"^/img/(\d+)/(\d+)/([a-z_]+)\.png$")
_ACTION = re.compile(r"^/api/line/(\d+)/(done|reset|send|undo)$")
_PIECE = re.compile(r"^/s/(\d+)(/.*)?$")
_MAX_BODY = 100_000


def make_server(source: Session | Library, port: int) -> ThreadingHTTPServer:
    """Serve one score at ``/``, or a library: its list at ``/`` and piece n under ``/s/n/``."""
    library = source if isinstance(source, Library) else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # the terminal stays quiet
            pass

        def _reply(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._reply(status, json.dumps(value).encode("utf-8"), "application/json")

        def _state(self, session: Session) -> None:
            view = session.view()
            if library:
                k = int(_PIECE.match(self.path).group(1))
                view.update(title=f"{k + 1}. {library.titles[k]}", piece=k, pieces=len(library.scores))
            self._json(view)

        def _local(self) -> bool:
            """Refuse requests addressed to another name (a web page rebinding its DNS to this port)."""
            host = self.headers.get("Host", "").rsplit(":", 1)[0]
            if host in ("127.0.0.1", "localhost"):
                return True
            self._json({"error": "forbidden"}, 403)
            return False

        def _piece(self, path: str):
            """``(session, path within it)``; replies itself and returns None when there is none."""
            if library is None:
                return source, path
            m = _PIECE.match(path)
            if not m or int(m.group(1)) >= len(library.scores):
                self._json({"error": "not found"}, 404)
                return None
            k, rest = int(m.group(1)), m.group(2)
            if rest is None:                    # relative links need the trailing slash
                self.send_response(301)
                self.send_header("Location", f"/s/{k}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if rest == "/":
                self._page()
                return None
            try:
                return library.session(k, retry=rest == "/api/state"), rest
            except ReviewError as exc:
                self._json({"error": str(exc)}, 400)
                return None

        def _page(self) -> None:
            self._reply(200, _PAGE.read_bytes(), "text/html; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            if not self._local():
                return
            path = self.path.split("?")[0]
            if library and path == "/":
                self._reply(200, library.index().encode("utf-8"), "text/html; charset=utf-8")
                return
            if not (found := self._piece(path)):
                return
            session, path = found
            if path == "/":
                self._page()
            elif path == "/api/state":
                self._state(session)
            elif m := _IMAGE.match(path):
                try:
                    data = session.image_path(int(m.group(1)), int(m.group(2)), m.group(3)).read_bytes()
                except (OSError, ReviewError):
                    self._json({"error": "no such image"}, 404)
                    return
                self._reply(200, data, "image/png")
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._local():
                return
            # other sites cannot set this header without a preflight, which we never answer
            if self.headers.get("X-Review") != "1":
                self._json({"error": "forbidden"}, 403)
                return
            if not (found := self._piece(self.path)):
                return
            session, path = found
            m = _ACTION.match(path)
            length = int(self.headers.get("Content-Length") or 0)
            if not m or length > _MAX_BODY:
                self._json({"error": "not found"}, 404)
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                line, action = int(m.group(1)), m.group(2)
                if line >= session.units():
                    raise ReviewError("no such line")
                if action == "send":
                    session.send(line, body.get("tags", []))
                else:
                    getattr(session, action)(line)
                self._state(session)
            except (ReviewError, ValueError, KeyError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(source: Session | Library, port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(source, port)
    stop = threading.Event()
    worker = threading.Thread(target=source.run_worker, args=(stop,), daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    what = f"{len(source.scores)} scores" if isinstance(source, Library) else source.mscz.name
    print(f"reviewing {what} at {url}  (Ctrl-C to stop)", flush=True)
    if isinstance(source, Session) and source.claude_problem:
        print(f"note: {source.claude_problem}; lines you send will wait until it is installed")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        server.server_close()
