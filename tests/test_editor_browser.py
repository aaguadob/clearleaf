"""The editor in a real (headless) browser: every tool driven with the mouse
and keyboard, then the saved file checked. Needs Playwright
(``pip install playwright && playwright install chromium``); skipped otherwise."""

import threading
from http.server import ThreadingHTTPServer

import pytest

import clearleaf as pdf
from clearleaf.editor.server import make_handler
from clearleaf.editor.session import Session

from .pdfbuild import pages

sync_api = pytest.importorskip("playwright.sync_api")

LINES = [b"BT /F1 14 Tf 40 700 Td (Quarterly report for the board) Tj ET",
         b"BT /F1 12 Tf 40 660 Td (Confidential: account 12345678 belongs to Ana) Tj ET",
         b"BT /F1 12 Tf 40 620 Td (Total due: 100 EUR) Tj ET"]


def letter(content: bytes, size=(612, 792)) -> bytes:
    data = pages([content])
    return data.replace(b"/MediaBox [0 0 200 200]", b"/MediaBox [0 0 %d %d]" % size)


@pytest.fixture
def editor(tmp_path):
    path = tmp_path / "report.pdf"
    doc = pdf.open(letter(b"\n".join(LINES)))
    doc.insert_pdf(letter(b"BT /F1 12 Tf 40 700 Td (Second page) Tj ET"))
    doc.save(path)
    session = Session(path)
    holder = [0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session, "tok", holder))
    holder[0] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 800}, bypass_csp=True)  # lets the test evaluate its checks
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{holder[0]}/?t=tok")
        page.wait_for_selector(".page[data-index='0'] img[src]")
        yield page, session, tmp_path, errors
        browser.close()
    server.shutdown()
    server.server_close()


def at(page, x, y, index=0):
    """Screen position of page coordinates (points) on page ``index``."""
    box = None
    for _ in range(50):  # the page element is replaced when the document changes
        box = page.locator(f".page[data-index='{index}']").bounding_box()
        if box:
            break
        page.wait_for_timeout(50)
    scale = page.evaluate("ui.scale")
    return box["x"] + x * scale, box["y"] + y * scale


def drag(page, a, b, steps=8):
    page.mouse.move(*a)
    page.mouse.down()
    page.mouse.move(*b, steps=steps)
    page.mouse.up()


def settle(page, session, count):
    """Wait until the editor has sent ``count`` operations and is idle."""
    for _ in range(100):
        if len(session.ops) >= count and page.evaluate("ui.busy === 0"):
            break
        page.wait_for_timeout(100)
    assert len(session.ops) == count, [(o["op"], o.get("text")) for o in session.ops]


def test_every_tool_in_the_browser(editor):
    page, session, tmp_path, errors = editor
    n = 0

    # highlight across the first line (text-flow selection)
    page.keyboard.press("h")
    drag(page, at(page, 42, 88), at(page, 250, 90))
    n += 1; settle(page, session, n)

    # a note, then immediately add text while the note is still being saved
    page.keyboard.press("n")
    page.mouse.click(*at(page, 520, 60))
    page.keyboard.type("check the numbers")
    page.keyboard.press("Meta+Enter")
    page.keyboard.press("t")
    page.mouse.click(*at(page, 300, 250))
    page.keyboard.type("Approved by Ana Pérez")
    page.keyboard.press("Enter")
    n += 2; settle(page, session, n)
    assert page.evaluate("document.body.dataset.tool") == "text"  # no letters leaked into shortcuts

    # freehand drawing, rectangle, text box
    page.keyboard.press("d")
    drag(page, at(page, 60, 300), at(page, 200, 360))
    page.keyboard.press("r")
    drag(page, at(page, 300, 300), at(page, 400, 360))
    page.keyboard.press("b")
    drag(page, at(page, 40, 400), at(page, 300, 440))
    page.keyboard.type("Reviewed on Monday")
    page.keyboard.press("Meta+Enter")
    n += 3; settle(page, session, n)

    # select the rectangle and delete it with the keyboard
    page.keyboard.press("v")
    page.mouse.click(*at(page, 350, 301))
    page.keyboard.press("Delete")
    n += 1; settle(page, session, n)
    assert "Square" not in [a["type"] for a in session.state()["pages"][0]["annots"]]

    # edit a line of text
    page.keyboard.press("x")
    page.wait_for_selector(".textline")
    page.mouse.click(*at(page, 60, 167))
    page.keyboard.press("Meta+a")
    page.keyboard.type("Total due: 250 EUR")
    page.keyboard.press("Enter")
    n += 1; settle(page, session, n)

    # mark the account number for redaction and apply it
    page.keyboard.press("Shift+R")
    x0 = 40 + pdf.get_text_length("Confidential: account ", "helv", 12)
    x1 = 40 + pdf.get_text_length("Confidential: account 12345678", "helv", 12)
    drag(page, at(page, x0 - 1, 124), at(page, x1 + 1, 138))
    n += 1; settle(page, session, n)
    page.click("#redact-apply")
    page.click("#dialog-ok")
    n += 1; settle(page, session, n)

    # rotate page 2 from its thumbnail, then undo and redo
    page.hover(".thumb[data-index='1']")
    page.click(".thumb[data-index='1'] button[title='Rotate right']")
    n += 1; settle(page, session, n)
    with page.expect_response(lambda r: "/api/undo" in r.url) as resp:
        page.click("#undo")
    assert resp.value.status == 200, resp.value.text()
    assert session.state()["pages"][1]["rotation"] == 0
    with page.expect_response(lambda r: "/api/redo" in r.url):
        page.click("#redo")
    settle(page, session, n)
    assert session.state()["pages"][1]["rotation"] == 90

    # insert another PDF after page 1, add a blank page
    other = tmp_path / "other.pdf"
    other.write_bytes(letter(b"BT /F1 12 Tf 40 700 Td (Appendix) Tj ET"))
    page.click(".thumb[data-index='0']")
    with page.expect_file_chooser() as chooser:
        page.click("#insert-pdf")
    chooser.value.set_files(str(other))
    n += 1; settle(page, session, n)
    page.click("#add-blank")
    n += 1; settle(page, session, n)

    page.click("#save")
    page.wait_for_selector("#toast:not([hidden])")
    assert not errors, errors

    out = pdf.open(tmp_path / "report-edited.pdf")
    texts = [p.get_text() for p in out]
    assert len(out) == 4
    first = texts[0]
    assert "12345678" not in first and "Confidential: account" in first
    assert "Total due: 250 EUR" in first and "100 EUR" not in first
    assert "Approved by Ana Pérez" in first and "Reviewed on Monday" in first
    # both were added after the current page (1): the blank page first in line
    assert texts[1].strip() == "" and "Appendix" in texts[2] and "Second page" in texts[3]
    assert out[3].rotation == 90
    assert sorted(a.type for a in out[0].annots()) == ["FreeText", "Highlight", "Ink", "Text"]
    assert pdf.open(tmp_path / "report.pdf")[0].get_text().count("12345678") == 1  # original untouched


def test_form_fields_in_the_browser(editor, tmp_path):
    page, session, _, errors = editor
    # swap in a document with a text field and a check box
    from .test_forms import form

    session.original = form().tobytes()
    session._replay()
    page.reload()
    page.wait_for_selector(".field")
    page.fill("input.field[title='name']", "Ana Pérez")
    page.keyboard.press("Enter")
    page.wait_for_function("ui.busy === 0 && ui.state.pages[0].fields.find(f => f.name === 'name').value === 'Ana Pérez'")
    page.check("input.field[title='agree']")
    page.wait_for_function("ui.busy === 0 && ui.state.pages[0].fields.find(f => f.name === 'agree').value === true")
    page.select_option("select.field[title='size']", "Large")
    page.wait_for_function("ui.busy === 0 && ui.state.pages[0].fields.find(f => f.name === 'size').value === 'l'")
    assert page.is_disabled("input.field[title='locked']")
    values = {w.field_name: w.field_value for w in pdf.open(session.pdf_bytes())[0].widgets()}
    assert values["name"] == "Ana Pérez" and values["agree"] is True and values["size"] == "l"
    assert not errors, errors
