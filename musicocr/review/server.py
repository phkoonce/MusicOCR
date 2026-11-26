"""A local web page for a review session. Listens on this machine only."""
from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from musicocr.review.session import ReviewError, Session

_PAGE = Path(__file__).with_name("page.html")
_IMAGE = re.compile(r"^/img/(\d+)/(\d+)/([a-z_]+)\.png$")
_ACTION = re.compile(r"^/api/line/(\d+)/(done|reset|send|undo)$")
_MAX_BODY = 100_000


def make_server(session: Session, port: int) -> ThreadingHTTPServer:
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

        def _local(self) -> bool:
            """Refuse requests addressed to another name (a web page rebinding its DNS to this port)."""
            host = self.headers.get("Host", "").rsplit(":", 1)[0]
            if host in ("127.0.0.1", "localhost"):
                return True
            self._json({"error": "forbidden"}, 403)
            return False

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            if not self._local():
                return
            path = self.path.split("?")[0]
            if path == "/":
                self._reply(200, _PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(session.view())
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
            m = _ACTION.match(self.path)
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
                self._json(session.view())
            except (ReviewError, ValueError, KeyError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(session: Session, port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(session, port)
    stop = threading.Event()
    worker = threading.Thread(target=session.run_worker, args=(stop,), daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"reviewing {session.mscz.name} at {url}  (Ctrl-C to stop)")
    if session.claude_problem:
        print(f"note: {session.claude_problem}; lines you send will wait until it is installed")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        server.server_close()
