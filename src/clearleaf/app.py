"""clearleaf as a desktop app: the editor in a window of its own.

Each open PDF gets a window showing the same editor as ``clearleaf edit``,
served from 127.0.0.1 with a random token; nothing leaves the computer.
Saving uses the system's save dialog. Needs ``pip install clearleaf[app]``
(pywebview); the packaged Mac and Windows apps start here.
"""

from __future__ import annotations

import secrets
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .editor.server import make_handler
from .editor.session import Session
from .errors import PDFError

START_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>clearleaf</title><style>
:root { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; color: #16191d; }
body { margin: 0; height: 100vh; display: grid; place-items: center; background: #e4e6e9; -webkit-user-select: none; user-select: none; }
main { display: grid; justify-items: center; gap: 18px; text-align: center; }
.mark { width: 34px; height: 44px; background: #16191d; border-radius: 2px 20px 2px 20px; }
h1 { margin: 0; font-size: 22px; font-weight: 600; }
p { margin: 0; color: #6b727c; max-width: 340px; }
button { font: inherit; font-weight: 500; height: 38px; padding: 0 20px; border: 0; border-radius: 9px; background: #16191d; color: #fff; cursor: pointer; }
button:hover { background: #2d323a; }
button:focus-visible { outline: 2px solid #2f55ff; outline-offset: 2px; }
.error { color: #d93b30; min-height: 1.4em; }
</style></head><body><main>
<span class="mark" aria-hidden="true"></span>
<h1>clearleaf</h1>
<p>Edit, fill in, annotate and redact PDFs. Your files stay on this computer.</p>
<button id="open">Open a PDF…</button>
<p class="error" id="error" role="alert"></p>
</main><script>
const open = () => window.pywebview.api.open_dialog().then((err) => { document.getElementById("error").textContent = err || ""; });
document.getElementById("open").addEventListener("click", open);
document.addEventListener("keydown", (e) => {
  if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "o") || (e.key === "Enter" && e.target === document.body)) { e.preventDefault(); open(); }
});
</script></body></html>"""


def _webview():
    try:
        import webview
    except ImportError:
        raise SystemExit("The desktop app needs pywebview: pip install 'clearleaf[app]'") from None
    return webview


class _Editor:
    """One open PDF: its editing session, its local server and its window."""

    def __init__(self, app: "App", path: Path):
        self.app = app
        self.path = path
        self.session = Session(path)
        self.token = secrets.token_urlsafe(24)
        holder = [0]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.session, self.token, holder))
        holder[0] = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{holder[0]}/?t={self.token}&app=1"
        self.window = None

    def close(self) -> None:
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class _Api:
    """What the page in a window can ask the app to do (``window.pywebview.api``)."""

    def __init__(self, app: "App", editor: _Editor | None = None):
        self._app = app
        self._editor = editor

    def open_dialog(self):
        """Choose PDFs to open, each in a window of its own. Returns an error message or None."""
        webview = _webview()
        window = self._editor.window if self._editor else self._app.start_window
        chosen = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                           file_types=("PDF documents (*.pdf)", "All files (*.*)"))
        errors = []
        for name in chosen or ():
            try:
                self._app.open(Path(name))
            except (PDFError, OSError) as exc:
                errors.append(f"{Path(name).name}: {exc}")
        if chosen and not errors and self._editor is None and self._app.start_window is not None:
            self._app.start_window.destroy()
            self._app.start_window = None
        return "\n".join(errors) or None

    def save_as(self):
        """Save a copy where the person chooses. Returns the path, or None if cancelled."""
        return self._save_dialog(self._editor.path.stem + "-edited.pdf", self._editor.session.save)

    def save_pages(self, pages):
        """Save some of the pages as a new PDF. Returns the path, or None if cancelled."""
        session = self._editor.session

        def write(target):
            Path(target).write_bytes(session.pdf_bytes([int(p) for p in pages]))
            return target

        return self._save_dialog(self._editor.path.stem + "-pages.pdf", write)

    def _save_dialog(self, suggested: str, write):
        webview = _webview()
        target = self._editor.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(self._editor.path.parent), save_filename=suggested,
            file_types=("PDF documents (*.pdf)",))
        if not target:
            return None
        target = target[0] if isinstance(target, (list, tuple)) else target
        if not str(target).lower().endswith(".pdf"):
            target = f"{target}.pdf"
        if Path(target).resolve() == self._editor.path.resolve():
            raise PDFError("save the edited copy under a different name: the original stays as it was")
        return write(str(target))


class App:
    def __init__(self):
        self.editors: list = []
        self.start_window = None

    def open(self, path: Path) -> None:
        webview = _webview()
        editor = _Editor(self, path)
        self.editors.append(editor)
        api = _Api(self, editor)
        editor.window = webview.create_window(
            f"{path.name} — clearleaf", editor.url, js_api=api, width=1280, height=860, min_size=(720, 520),
            background_color="#e4e6e9", text_select=True)

        def closing():
            if len(editor.session.ops) != editor.session.saved_at and not editor.window.create_confirmation_dialog(
                    "Close without saving?", f"Your changes to {path.name} haven't been saved as a copy."):
                return False
            return True

        def closed():
            editor.close()
            if editor in self.editors:
                self.editors.remove(editor)

        editor.window.events.closing += closing
        editor.window.events.closed += closed

    def run(self, paths) -> None:
        webview = _webview()
        failures = []
        for p in paths:
            try:
                self.open(Path(p))
            except (PDFError, OSError) as exc:
                failures.append(f"{Path(p).name}: {exc}")
        if not self.editors:
            self.start_window = webview.create_window("clearleaf", html=START_PAGE, js_api=_Api(self),
                                                      width=560, height=420, min_size=(420, 340),
                                                      background_color="#e4e6e9")
            if failures:
                self.start_window.events.loaded += lambda: self.start_window.evaluate_js(
                    "document.getElementById('error').textContent = %r" % "\n".join(failures))
        webview.start(private_mode=True)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # macOS passes -psn_... when an app is launched from the Finder
    App().run([a for a in argv if not a.startswith("-psn_")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
