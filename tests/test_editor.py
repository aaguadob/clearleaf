"""The browser editor's server: access control, operations, undo/redo,
saving. (The page itself is exercised in a real browser separately.)"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import clearleaf as pdf
from clearleaf.editor.server import make_handler
from clearleaf.editor.session import Session

from .pdfbuild import pages


@pytest.fixture
def editor(tmp_path):
    path = tmp_path / "letter.pdf"
    path.write_bytes(pages([b"BT /F1 12 Tf 20 150 Td (first page) Tj ET", b"BT /F1 12 Tf 20 150 Td (second page) Tj ET"]))
    session = Session(path)
    holder = [0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session, "secret-token", holder))
    holder[0] = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{holder[0]}"

    def call(path, body=None, token="secret-token", raw=None, host=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
        if token:
            req.add_header("X-Token", token)
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req) as res:
            payload = res.read()
            return json.loads(payload) if res.headers["Content-Type"] == "application/json" else payload

    yield session, call, path
    server.shutdown()
    server.server_close()


def status(fn):
    try:
        fn()
    except urllib.error.HTTPError as exc:
        return exc.code
    return 200


def test_page_loads_without_token_but_api_needs_it(editor):
    _, call, _ = editor
    assert b"clearleaf editor" in call("/", token=None)
    assert status(lambda: call("/api/state", token=None)) == 403
    assert status(lambda: call("/api/state", token="wrong")) == 403
    assert status(lambda: call("/api/state", host="evil.example:80")) == 403  # DNS rebinding
    assert call("/api/state")["name"] == "letter.pdf"


def test_operations_undo_redo_and_save(editor):
    session, call, path = editor
    state = call("/api/op", {"op": "highlight", "page": 0, "rects": [[20, 40, 90, 55]]})
    assert [a["type"] for a in state["pages"][0]["annots"]] == ["Highlight"] and state["dirty"]
    state = call("/api/op", {"op": "move_page", "from": 1, "to": 0})
    assert call("/api/text?page=0")[0]["text"] == "second page"
    state = call("/api/undo", {})
    assert call("/api/text?page=0")[0]["text"] == "first page" and state["can_redo"]
    state = call("/api/redo", {})
    assert call("/api/text?page=0")[0]["text"] == "second page"
    saved = call("/api/save", {})
    assert saved["path"].endswith("letter-edited.pdf") and not saved["dirty"]
    out = pdf.open(saved["path"])
    assert out[0].get_text().strip() == "second page" and [a.type for a in out[1].annots()] == ["Highlight"]
    assert pdf.open(path)[0].get_text().strip() == "first page"  # the original is untouched


def test_render_and_download(editor):
    _, call, _ = editor
    png = call("/api/render?page=0&dpi=36")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    data = call("/api/download?pages=1")
    assert pdf.open(data)[0].get_text().strip() == "second page" and len(pdf.open(data)) == 1


def test_errors_are_reported_and_leave_the_document_unchanged(editor):
    session, call, _ = editor
    before = session.doc.tobytes()
    with pytest.raises(urllib.error.HTTPError) as info:
        call("/api/op", {"op": "text", "page": 0, "point": [10, 10], "text": "Привет"})
    assert info.value.code == 422 and "built-in fonts" in json.loads(info.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as info:
        call("/api/op", {"op": "delete_pages", "pages": [0, 1]})
    assert info.value.code == 422
    assert session.doc.tobytes() == before and not session.ops


def test_uploads_insert_pdf_and_image(editor):
    _, call, _ = editor
    blob = call("/api/blob", raw=pages([b"BT /F1 12 Tf 20 150 Td (inserted) Tj ET"]))["id"]
    state = call("/api/op", {"op": "insert_pdf", "blob": blob, "after": 0})
    assert len(state["pages"]) == 3 and call("/api/text?page=1")[0]["text"] == "inserted"


def test_every_tool_operation(editor):
    _, call, _ = editor
    ops = [
        {"op": "underline", "page": 0, "rects": [[20, 40, 90, 55]], "color": [0, 0, 1]},
        {"op": "strikeout", "page": 0, "rects": [[20, 40, 90, 55]]},
        {"op": "note", "page": 0, "point": [150, 10], "text": "check this"},
        {"op": "freetext", "page": 0, "rect": [10, 80, 190, 120], "text": "boxed"},
        {"op": "ink", "page": 0, "strokes": [[[10, 150], [40, 170]]]},
        {"op": "rect", "page": 0, "rect": [10, 10, 50, 50]},
        {"op": "ellipse", "page": 0, "rect": [10, 10, 50, 50]},
        {"op": "line", "page": 0, "p1": [0, 0], "p2": [50, 50]},
        {"op": "text", "page": 1, "point": [20, 100], "text": "added"},
        {"op": "rotate", "page": 1, "delta": 90},
        {"op": "new_page", "after": 1},
        {"op": "replace_text", "page": 0, "rect": [19, 38, 90, 56], "text": "new words"},
        {"op": "redact_mark", "page": 1, "rect": [15, 35, 120, 55]},
    ]
    for o in ops:
        state = call("/api/op", o)
    assert state["pending_redactions"] == 1
    state = call("/api/op", {"op": "redact_apply"})
    assert state["pending_redactions"] == 0 and len(state["pages"]) == 3
    note = next(a for a in state["pages"][0]["annots"] if a["type"] == "Text")
    state = call("/api/op", {"op": "delete_annot", "page": 0, "id": note["id"]})
    assert "Text" not in [a["type"] for a in state["pages"][0]["annots"]]
    text = [l["text"] for l in call("/api/text?page=0")]
    assert "new words" in text and "first page" not in text
    assert "second page" not in [l["text"] for l in call("/api/text?page=1")]
    assert state["pages"][1]["rotation"] == 90


def test_undo_replays_deleting_an_annotation(editor):
    session, call, _ = editor
    state = call("/api/op", {"op": "note", "page": 0, "point": [10, 10], "text": "temp"})
    note_id = state["pages"][0]["annots"][0]["id"]
    call("/api/op", {"op": "delete_annot", "page": 0, "id": note_id})
    call("/api/op", {"op": "rotate", "page": 1})
    state = call("/api/undo", {})  # replays note + delete: the note must be found by its id
    assert state["pages"][0]["annots"] == [] and state["can_redo"]
    state = call("/api/undo", {})
    assert [a["id"] for a in state["pages"][0]["annots"]] == [note_id]
