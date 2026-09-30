"""Page-level PDF merging: concatenate the pages of several PDFs into one.

This is structural concatenation, not general editing: each page's content
stream(s) and resources (fonts, images, etc.) are copied byte-for-byte
(still-encoded, via `PDFStream.raw_data`) into a fresh document, so nothing
is re-rendered or re-compressed. Page tree inheritance (MediaBox/Rotate/
Resources) is flattened via the existing `Page` properties rather than
carrying /Parent chains across, which sidesteps having to also decide what
to do with the rest of each source document's (otherwise irrelevant) page
tree.

Not carried over: document metadata (/Info), outlines/bookmarks, form
fields (/AcroForm), and annotations (/Annots) -- none of those are needed
for concatenating simple content-only PDFs (e.g. scanned receipts), and
adding them is future scope, not something this function attempts.
"""

from __future__ import annotations

import os
from typing import Iterable, Union

from ._objects import PDFReference, PDFStream
from .errors import public
from ._writer import PDFWriter
from .document import PDFDocument

_PathOrDoc = Union[str, "os.PathLike[str]", bytes, bytearray, PDFDocument]
_Item = Union[_PathOrDoc, "tuple[_PathOrDoc, Iterable[int]]"]


def _copy_value(doc: PDFDocument, obj, memo: dict, writer: PDFWriter):
    if isinstance(obj, PDFReference):
        key = (id(doc), obj.num, obj.gen)
        existing = memo.get(key)
        if existing is not None:
            return existing
        num = writer.reserve()
        new_ref = PDFReference(num, 0)
        memo[key] = new_ref
        target = doc.get_object(obj.num, obj.gen)
        writer.set(num, _copy_value(doc, target, memo, writer))
        return new_ref
    if isinstance(obj, dict):
        return {k: _copy_value(doc, v, memo, writer) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_copy_value(doc, v, memo, writer) for v in obj]
    if isinstance(obj, PDFStream):
        new_dict = {k: _copy_value(doc, v, memo, writer) for k, v in obj.dict.items()}
        return PDFStream(new_dict, obj.raw_data)
    return obj


@public
def merge(inputs: Iterable[_Item], output: Union[str, "os.PathLike[str]"]) -> None:
    """Concatenate the pages of `inputs`, in order, into one PDF written to
    `output`.

    Each item in `inputs` is either a source (a file path, raw PDF bytes, or
    an already-open `PDFDocument`; documents this function itself opened are
    closed again before returning, ones passed in already-open are left for
    the caller to close), or a `(source, page_indices)` tuple to take only
    specific 0-based pages from that source, in the given order -- e.g.
    `(doc, [0, 2])` keeps just the first and third pages, `(doc, [1, 0])`
    keeps the first two pages reversed.
    """
    writer = PDFWriter()
    pages_num = writer.reserve()
    pages_ref = PDFReference(pages_num, 0)
    catalog_ref = writer.add({"Type": "Catalog", "Pages": pages_ref})

    page_refs = []
    for item in inputs:
        if isinstance(item, tuple):
            source, page_indices = item
        else:
            source, page_indices = item, None
        owns_doc = not isinstance(source, PDFDocument)
        doc = PDFDocument.open(source) if owns_doc else source
        try:
            memo: dict = {}
            pages = doc if page_indices is None else (doc[i] for i in page_indices)
            for page in pages:
                resources_copy = _copy_value(doc, page.resources, memo, writer)
                contents_copy = _copy_value(
                    doc, page.dict.get("Contents"), memo, writer
                )
                page_dict = {
                    "Type": "Page",
                    "Parent": pages_ref,
                    "MediaBox": list(page.mediabox),
                    "Resources": resources_copy,
                }
                if contents_copy is not None:
                    page_dict["Contents"] = contents_copy
                if page.cropbox != page.mediabox:
                    page_dict["CropBox"] = list(page.cropbox)
                if page.rotation:
                    page_dict["Rotate"] = page.rotation
                page_refs.append(writer.add(page_dict))
        finally:
            if owns_doc:
                doc.close()

    writer.set(pages_num, {"Type": "Pages", "Kids": page_refs, "Count": len(page_refs)})
    writer.write(output, catalog_ref)
