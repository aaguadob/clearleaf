# Verified expected texts

For most files the benchmark treats PyMuPDF's `get_text()` as the reference.
For the files below PyMuPDF's text is demonstrably wrong, so clearleaf's text
is scored against an expected text derived **without clearleaf** instead
(`corpus/expected/<id>.txt`, built by `benchmarks/make_expected.py`). The
results table still shows PyMuPDF's score next to it.

## pypdf-habibi, pypdf-habibi-oneline-cmap, pypdf-habibi-rotated

The page draws the Arabic word حَبيبي, "habibi", and the Arabic word again,
using two CID fonts whose `/ToUnicode` CMaps deliberately put each Arabic
word on a single glyph and map the other glyphs of the word to *nothing*:

```
font 1:  <004b> -> "حَبيبي h"    <0044> -> "a"  <0045> -> "b"  <004c> -> "i"
font 2:  <0003> <03f2> <0392> <03f4> <02f4> -> ""   <03a3> -> "حَبيبي "
content: font 1 <004b 0044 0045 004c 0045 004c>, font 2 <0003 03f2 0392 03f4 0392> <02f4> <03a3>
```

Concatenating the mappings in content order gives `حَبيبي habibiحَبيبي ` —
which is exactly what pypdf (an independent, BSD-licensed implementation)
extracts, and is used as the expectation. PyMuPDF instead prints the raw codes
of the empty-mapped glyphs as characters (`\x03ϲΒϴΒ ˴`), i.e. text that isn't
in the document.

## pdfjs-ZapfDingbats

A character-set test page: each ZapfDingbats glyph is followed by its glyph
name and the Unicode value it stands for, printed in the document itself
(e.g. `a1 [x2701]`). PyMuPDF prints the raw font codes for the glyphs
(`!`, `"`, control characters...). The expectation is PyMuPDF's own text with
each glyph replaced by the character the document names for it (U+2701 for
`a1`, and so on) — the standard ZapfDingbats glyph-to-Unicode mapping, as
confirmed by the document's labels.

## pdfjs-simpletype3font (no expectation needed)

A Type 3 font with glyphs named `square` and `triangle`, which are not in the
Adobe Glyph List. PyMuPDF reads them as □ and △, pypdf as □ and ∆. clearleaf
maps `square` → □ and `triangle` → △ (`_encodings.EXTRA_GLYPH_NAMES`) and
matches the reference, so this file is scored against PyMuPDF as usual.

## pypdf-unreadablemetadata (no reference)

The file's page tree is damaged. PyMuPDF and pypdf both find 0 pages;
clearleaf's repair recovers 3. With no reference to compare against, the file
is listed but left out of the percentages.
