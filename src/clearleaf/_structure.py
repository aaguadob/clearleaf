"""The logical structure tree (ISO 32000-1 section 14.7): replacement text.

Structure elements can carry /ActualText -- the text a reader should get
instead of what the covered content draws (a bullet drawn as an image, a
ligature, a hyphenated word). The content they cover is referenced by
marked-content ids (MCIDs) on a page; this module maps each such MCID to
the replacement text, which text extraction then substitutes.
"""

from __future__ import annotations

from ._annots import decode_text_string

MAX_DEPTH = 64


def actual_text_map(doc) -> dict:
    """{(id(page dict), mcid): (owner, text)} for content covered by an
    element with /ActualText; ``owner`` identifies that element, so text
    spread over several MCIDs is substituted once. Computed once per
    document."""
    cached = getattr(doc, "_actual_text_map", None)
    if cached is not None:
        return cached
    result: dict = {}
    root = doc.resolve(doc.root.get("StructTreeRoot"))
    if isinstance(root, dict):
        _walk(doc, doc.resolve(root.get("K")), None, None, result, 0, set())
    try:
        doc._actual_text_map = result
    except AttributeError:
        pass
    return result


def _walk(doc, node, page, text, out: dict, depth: int, seen: set) -> None:
    if depth > MAX_DEPTH or node is None:
        return
    if isinstance(node, list):
        for kid in node:
            _walk(doc, doc.resolve(kid), page, text, out, depth + 1, seen)
        return
    if isinstance(node, int) and not isinstance(node, bool):  # an MCID on the inherited page
        if text is not None and page is not None:
            out.setdefault((id(page), node), text)
        return
    if not isinstance(node, dict):
        return
    if id(node) in seen:
        return
    seen.add(id(node))
    pg = doc.resolve(node.get("Pg"))
    if isinstance(pg, dict):
        page = pg
    kind = str(doc.resolve(node.get("Type", "")))
    if kind == "MCR" or ("MCID" in node and "S" not in node):  # marked-content reference
        mcid = doc.resolve(node.get("MCID"))
        if isinstance(mcid, int) and text is not None and page is not None:
            out.setdefault((id(page), mcid), text)
        return
    if kind == "OBJR":
        return
    if text is None and "ActualText" in node:
        text = (id(node), decode_text_string(doc.resolve(node.get("ActualText"))))
    _walk(doc, doc.resolve(node.get("K")), page, text, out, depth + 1, seen)


def page_actual_text(doc, page_dict: dict) -> dict:
    """{mcid: (owner, text)} for one page."""
    key = id(page_dict)
    return {mcid: value for (page, mcid), value in actual_text_map(doc).items() if page == key}
