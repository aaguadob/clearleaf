"""The desktop app's bridge to the page: opening and saving go through
system dialogs, faked here. Needs pywebview (clearleaf[app]); skipped otherwise."""

import pytest

import clearleaf as pdf

from .pdfbuild import pages

webview = pytest.importorskip("webview")

from clearleaf import app as app_module  # noqa: E402


class FakeEvent:
    def __iadd__(self, handler):
        return self


class FakeEvents:
    closing = closed = loaded = FakeEvent()


class FakeWindow:
    def __init__(self, answer=None):
        self.answer = answer
        self.asked = []
        self.events = FakeEvents()

    def create_file_dialog(self, kind, **kw):
        self.asked.append((kind, kw))
        return self.answer


@pytest.fixture
def opened(tmp_path, monkeypatch):
    path = tmp_path / "letter.pdf"
    path.write_bytes(pages([b"BT /F1 12 Tf 20 150 Td (Draft) Tj ET", b"BT /F1 12 Tf 20 150 Td (Page two) Tj ET"]))
    monkeypatch.setattr(webview, "create_window", lambda *a, **k: FakeWindow())
    app = app_module.App()
    app.open(path)
    editor = app.editors[0]
    yield app, editor, tmp_path
    editor.close()


def test_save_as_writes_where_chosen_and_marks_saved(opened):
    app, editor, tmp = opened
    line = editor.session.text_lines(0)[0]
    editor.session.apply({"op": "edit_text", "page": 0, "rect": line["bbox"], "old": "Draft", "text": "Final"})
    editor.window = FakeWindow(answer=str(tmp / "out"))  # no extension typed
    path = app_module._Api(app, editor).save_as()
    assert path == str(tmp / "out.pdf")
    assert pdf.open(path)[0].get_text().strip() == "Final"
    assert editor.session.saved_at == len(editor.session.ops)
    kind, kw = editor.window.asked[0]
    assert kind == webview.FileDialog.SAVE and kw["save_filename"] == "letter-edited.pdf"


def test_cancelled_dialog_saves_nothing(opened):
    app, editor, tmp = opened
    editor.window = FakeWindow(answer=None)
    assert app_module._Api(app, editor).save_as() is None
    assert sorted(p.name for p in tmp.iterdir()) == ["letter.pdf"]


def test_the_original_is_never_overwritten(opened):
    app, editor, tmp = opened
    editor.window = FakeWindow(answer=str(tmp / "letter.pdf"))
    with pytest.raises(pdf.PDFError):
        app_module._Api(app, editor).save_as()


def test_save_pages(opened):
    app, editor, tmp = opened
    editor.window = FakeWindow(answer=str(tmp / "second.pdf"))
    path = app_module._Api(app, editor).save_pages([1])
    out = pdf.open(path)
    assert len(out) == 1 and out[0].get_text().strip() == "Page two"


def test_open_dialog_reports_files_it_cannot_open(opened, tmp_path):
    app, editor, _ = opened
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    editor.window = FakeWindow(answer=[str(bad)])
    message = app_module._Api(app, editor).open_dialog()
    assert message.startswith("broken.pdf:") and len(app.editors) == 1


def test_editor_page_is_served_in_app_mode(opened):
    import urllib.request

    _, editor, _ = opened
    html = urllib.request.urlopen(editor.url).read()
    assert b'id="open"' in html and b"app-only" in html
