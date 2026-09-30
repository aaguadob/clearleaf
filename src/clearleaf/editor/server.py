"""Local web server for the editor: ``clearleaf edit file.pdf``.

It listens on 127.0.0.1 only, and every request must carry the random
token from the URL it opens, so neither other machines nor other web pages
open in the browser can reach the document. The file never leaves the
computer: rendering and editing run in this process.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..errors import PDFError
from .session import Session

MAX_UPLOAD = 200 << 20
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
          "/editor.css": ("editor.css", "text/css; charset=utf-8")}


def _static(name: str) -> bytes:
    return resources.files("clearleaf.editor").joinpath("static", name).read_bytes()


def make_handler(session: Session, token: str, port_holder: list):
    class Handler(BaseHTTPRequestHandler):
        server_version = "clearleaf"

        def log_message(self, *args):  # quiet
            pass

        # -- plumbing ----------------------------------------------------------------

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            port = port_holder[0]
            return host in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _authorised(self, query) -> bool:
            given = self.headers.get("X-Token") or query.get("t", [""])[0]
            return secrets.compare_digest(given, token)

        def _send(self, status, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=HTTPStatus.OK):
            self._send(status, json.dumps(obj).encode(), "application/json", {"Cache-Control": "no-store"})

        def _error(self, status, message: str):
            self._json({"error": message}, status)

        def _route(self, method: str):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "wrong host")
            if method == "GET" and url.path in STATIC:
                name, ctype = STATIC[url.path]
                return self._send(HTTPStatus.OK, _static(name), ctype, {"Cache-Control": "no-cache"})
            if not self._authorised(query):
                return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token: open the address the editor printed")
            try:
                handler = getattr(self, f"_{method.lower()}_{url.path.strip('/').replace('/', '_')}", None)
                if handler is None:
                    return self._error(HTTPStatus.NOT_FOUND, "no such endpoint")
                return handler(query)
            except PDFError as exc:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except (KeyError, ValueError, TypeError, IndexError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, f"bad request: {exc}")

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                raise PDFError("that file is larger than 200 MB")
            return self.rfile.read(length)

        # -- endpoints ---------------------------------------------------------------

        def _get_api_state(self, query):
            self._json(session.state())

        def _get_api_render(self, query):
            dpi = max(10, min(300, int(query.get("dpi", ["96"])[0])))
            png = session.render(int(query["page"][0]), dpi)
            self._send(HTTPStatus.OK, png, "image/png", {"Cache-Control": "private, max-age=3600"})

        def _get_api_text(self, query):
            self._json(session.text_lines(int(query["page"][0])))

        def _get_api_blanks(self, query):
            self._json(session.blanks(int(query["page"][0])))

        def _post_api_preview(self, query):
            body = json.loads(self._body())
            dpi = max(36, min(400, int(body.get("dpi", 144))))
            self._json(session.preview(int(body["page"]), body["rect"], body.get("old"), body.get("text", ""), dpi))

        def _get_api_download(self, query):
            pages = query.get("pages", [""])[0]
            selected = [int(p) for p in pages.split(",") if p != ""] or None
            data = session.pdf_bytes(selected)
            stem = session.path.stem + ("-pages" if selected else "-edited")
            self._send(HTTPStatus.OK, data, "application/pdf",
                       {"Content-Disposition": f'attachment; filename="{stem}.pdf"', "Cache-Control": "no-store"})

        def _post_api_op(self, query):
            session.apply(json.loads(self._body()))
            self._json(session.state())

        def _post_api_undo(self, query):
            session.undo()
            self._json(session.state())

        def _post_api_redo(self, query):
            session.redo()
            self._json(session.state())

        def _post_api_blob(self, query):
            data = self._body()
            if not data:
                raise PDFError("empty upload")
            self._json({"id": session.add_blob(data)})

        def _post_api_save(self, query):
            body = json.loads(self._body() or b"{}")
            path = session.path if body.get("overwrite") else None
            saved = session.save(path)
            self._json({"path": saved, **session.state()})

    return Handler


def serve(path: str, open_browser: bool = True, port: int = 0) -> None:
    session = Session(path)
    token = secrets.token_urlsafe(24)
    port_holder = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session, token, port_holder))
    port_holder[0] = server.server_address[1]
    url = f"http://127.0.0.1:{port_holder[0]}/?t={token}"
    print(f"Editing {Path(path).name}\nOpen {url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if len(session.ops) != session.saved_at:
            print("\nStopped. Unsaved changes were discarded.", file=sys.stderr)
    finally:
        server.server_close()
