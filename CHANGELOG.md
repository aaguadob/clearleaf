# Changelog

## 0.1.0 — first release

A pure-Python, MIT-licensed PDF library with a PyMuPDF-style API, and an
editor that runs on your own computer.

### Reading
- Text extraction (`get_text`, including `"dict"` output with positions),
  rendering to images (`get_pixmap`), image extraction and merging.
- Encrypted files (RC4, AES-128, AES-256), CJK, Arabic and other scripts,
  embedded TrueType, Type 1 and CFF fonts, Type 3 fonts, transparency,
  shadings and patterns, JPEG, JBIG2 and CCITT images.
- Matches PyMuPDF on every check of the 66-file development benchmark and
  on text 16/17, rendering 17/17 and images 17/17 of a held-out set of
  real documents ([benchmarks/](benchmarks/)).
- Limits on decompressed size and nesting depth, so hostile files fail
  fast with a `PDFError` instead of exhausting memory.

### Editing
- Pages: delete, reorder, rotate, insert from other PDFs, add blank pages.
- Annotations: highlight, underline, strike out, comments, text boxes,
  ink, shapes and lines, each with its own appearance; annotations can
  be moved.
- Forms: read and fill text fields, check boxes, radio buttons and choices.
- Text: change a line of text in place (`edit_text_line`), keeping the
  document's font; move a line (`move_text_line`); add text and images.
- Redaction that removes text, image pixels, notes and fields from the file.
- Saving rewrites the file compactly (object streams), dropping anything
  no longer used.

### Editor and desktop app
- `clearleaf edit file.pdf`: an editor in your browser, served from
  127.0.0.1 only. Click into text to edit it, drag things to move them,
  fill in forms (and the blanks of forms that have no fields), annotate,
  rearrange pages, redact. Everything can be undone.
- `clearleaf-app` (`pip install "clearleaf[app]"`), and Mac and Windows apps
  attached to the GitHub release: the same editor in a window of its own.
  The apps are not signed yet: on a Mac, right-click → Open the first time;
  on Windows, "More info → Run anyway".

### Known limitations
- Right-to-left text can't be edited in place yet.
- Editing works line by line: paragraphs don't reflow.
- Pure Python is slower than MuPDF, particularly for rendering dense pages.
