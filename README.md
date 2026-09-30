# clearleaf

A permissively licensed (MIT), clean-room PDF library for Python, with a
PyMuPDF-style API and a PDF editor that runs in your browser. Pure Python,
no dependencies.

```python
import clearleaf as pdf

doc = pdf.open("file.pdf")
page = doc[0]
print(page.get_text())
page.get_pixmap(dpi=150).save("page1.png")

page.add_highlight_annot([(72, 100, 300, 114)])
page.insert_text((72, 700), "Approved", fontsize=14, color=(0, 0.5, 0))
doc.save("file-edited.pdf")
```

> **Status: alpha (0.1.0).** Reading (text, rendering, images) matches
> PyMuPDF on every file of the development benchmark and on 16/17 text,
> 17/17 render and 17/17 image checks of a held-out set of real PDFs (see
> [Fidelity](#fidelity-vs-pymupdf)). Editing is new: saved files are checked
> by reading them back with clearleaf and PyMuPDF, but expect rough edges.

## Install

```bash
pip install clearleaf
```

Python 3.9+. No runtime dependencies: parsing, fonts, JPEG decoding, PNG
encoding, encryption and rendering are implemented in this package, using
only the standard library (`zlib`, `hashlib`).

**It is pure Python, so it is slower than PyMuPDF (which is C):** text
extraction takes about 10x as long (a 142-page document: 8 s against
0.7 s), rendering about 100x (a page at 150 dpi: 2.4 s against 0.02 s).
Fine for scripts, batch jobs and editing; not for rendering thousands of
pages interactively.

## Edit a PDF in your browser

```bash
clearleaf edit form.pdf
```

opens an editor in your browser. It runs on your computer: the file is
never uploaded anywhere (the editor only listens on 127.0.0.1 and needs
the one-time key in the address it opens). You can:

- **Pages:** reorder by dragging thumbnails, rotate, delete, add blank
  pages, insert pages from another PDF, download a selection of pages.
- **Annotate:** highlight, underline and strike out text; sticky notes,
  text boxes, freehand drawing, rectangles, ellipses, lines.
- **Fill in forms:** text fields, check boxes, radio buttons, drop-downs.
- **Edit content:** add text and images, change a line of existing text
  (the new text is set in Helvetica/Times/Courier, covering Western
  European characters), and **redact**: mark areas, then apply, and the
  text, image pixels, notes and form fields under them are removed from
  the file, not just covered.

Every change can be undone. "Save a copy" writes `form-edited.pdf` next to
the original; the original is never changed.

## Editing API

Coordinates are points from the top-left corner of the page, as in
PyMuPDF (and as `get_text("dict")` reports them).

| | |
|---|---|
| Pages | `doc.delete_page(n)`, `doc.delete_pages([...])`, `doc.move_page(n, to)`, `doc.select([...])`, `doc.insert_pdf(other, from_page, to_page, start_at)`, `doc.new_page()`, `page.set_rotation(90)` |
| Annotations | `page.add_highlight_annot(rects)`, `add_underline_annot`, `add_strikeout_annot`, `add_squiggly_annot`, `add_text_annot(point, text)`, `add_freetext_annot(rect, text)`, `add_ink_annot(strokes)`, `add_rect_annot`, `add_circle_annot`, `add_line_annot`; `page.annots()`, `page.delete_annot(a)` |
| Forms | `page.widgets()`; `w.field_value = ...; w.update()` |
| Content | `page.insert_text(point, text)`, `page.insert_image(rect, filename=...)`, `page.replace_text(rect, text)`, `clearleaf.get_text_length(text)` |
| Redaction | `page.add_redact_annot(rect)`, `page.apply_redactions()` |
| Saving | `doc.save(path)`, `doc.tobytes()` |

Saving writes a new, compact file (compressed object streams) containing
only what the edited document still uses: deleted pages and redacted
content are gone from it.

Known limits of editing: text added or replaced uses the standard 14
fonts, so non-Latin scripts can't be written yet (they are read fine).
Redaction removes text, image pixels, inline images, annotations and form
fields under the marks; vector graphics are covered by the fill box, not
removed, and the outline, metadata and structure-tree alternate text are
not searched. Encrypted files are saved decrypted.

## Reading untrusted files

Every failure to read a file raises `clearleaf.PDFError` (a `ValueError`).
Decompression bombs and deeply nested objects are stopped by
`clearleaf.LIMITS` (256 MB per decoded stream, nesting depth 256; the
stream limit can also be set with `CLEARLEAF_MAX_STREAM_BYTES`): a 2 MB
file that inflates to 2 GB fails in 0.1 s.

## Why this exists

PyMuPDF wraps MuPDF, which is AGPL-3.0-licensed (commercial licenses
available from Artifex for closed-source use). `clearleaf` targets the same
public Python API shape, but is implemented independently: strictly from
the public PDF specification (ISO 32000-1/2) and permissively licensed
reference material, with no code read from or copied out of MuPDF/PyMuPDF.
See [clearleaf-project-brief.md](clearleaf-project-brief.md) for the full
clean-room policy.

## Feature coverage

| Phase | Feature | Status |
|---|---|---|
| 1 | Header / xref table (classic) parsing | Done |
| 1 | Cross-reference streams (PDF 1.5+) | Done |
| 1 | Object streams (compressed objects) | Done |
| 1 | Object model (dict/array/stream/name/ref/string/number) | Done |
| 1 | Filters: FlateDecode (+ PNG/TIFF predictors), ASCII85Decode, LZWDecode | Done |
| 1 | DCTDecode (JPEG passthrough), CCITTFaxDecode (G3 1-D/2-D, G4), JBIG2Decode | Done (JBIG2: no Huffman/refinement/halftone yet) |
| 1 | Encryption: standard security handler, RC4 40-128, AES-128, AES-256 (R2-R6), user/owner passwords, crypt filters — `doc.needs_pass` / `doc.authenticate(pw)` | Done (public-key handlers not supported) |
| 1 | Damaged files: rebuild the object index by scanning when xref data is missing/wrong; verify each object's number; recover pages if the page tree is gone | Done (`doc.repaired` reports it) |
| 2 | Content stream tokenizer/interpreter | Done |
| 2 | Font handling: simple fonts (Type1/TrueType), Type0/CID + Identity-H | Done |
| 2 | Encoding: WinAnsi/MacRoman/Standard + /Differences, ToUnicode CMaps | Done |
| 2 | `page.get_text()` — plain text, reading order; annotation/form-field text, `/ActualText`, clipped text dropped | Done |
| 2 | `page.get_text("dict")` — blocks/lines/spans with bbox metadata | Done (approximate — see below) |
| 3 | Path construction/painting (fill, stroke, clip), CTM stack | Done |
| 3 | Color spaces: Gray/RGB/CMYK, Indexed, ICCBased, Lab, Separation/DeviceN | Done |
| 3 | Text rendering via real glyph outlines (embedded TrueType, Type 1 and CFF; non-embedded standard fonts from the system's installed copies, else bundled Liberation substitutes) | Done (CFF-flavoured OpenType not yet) |
| 3 | Image XObjects: raw/Flate samples, /Decode, ImageMask, /SMask and stencil /Mask, Form XObjects; area-averaged downscaling | Done |
| 3 | Shadings (all 7 types) and tiling/shading patterns | Done |
| 3 | Transparency: constant alpha, all 16 blend modes, luminosity/alpha soft masks | Done (groups are not isolated/knocked out) |
| 3 | Annotation appearance streams, incl. regenerated form fields (`/NeedAppearances`) | Done |
| 3 | DCTDecode (JPEG) rendering — from-scratch baseline + progressive decoder | Done |
| 3 | `page.get_pixmap()` / PNG output (from-scratch encoder) | Done |
| 4 | `page.get_images()` — enumerate image XObjects, including those inside Form XObjects (`full=True` adds the referencer) | Done |
| 4 | `doc.extract_image(xref)` — JPEG passthrough, everything else re-encoded to PNG | Done (approximate — see below) |
| 5 | `clearleaf.merge(paths, output)` — concatenate pages from multiple PDFs into one | Done |
| 6 | Editing: page operations, annotations, form filling, inserted text/images, redaction, `save()` | Done (alpha — see "Editing API") |
| 6 | Browser editor: `clearleaf edit file.pdf` | Done (alpha) |

### Phase 2 known limitations

- Type0/CID fonts without `/ToUnicode` get text from, in order: the codes
  themselves for Unicode-based predefined CMaps (`Uni*-UCS2/UTF16`), Adobe's
  CID-to-Unicode tables for the Japan1/GB1/CNS1/Korea1/KR collections
  (bundled compactly in `_data/`, ~280 KB, BSD-3 — built by
  `scripts/build_glyph_data.py`), or the embedded TrueType's own cmap.
  Unknown collections without any of these still decode to empty text.
- Glyph names resolve through the full Adobe Glyph List (bundled, BSD-3)
  with its suffix/ligature/`uniXXXX` rules. StandardEncoding is the real
  Annex D table, and it's the default for Type 1 fonts without `/Encoding`.
- Symbolic simple fonts without a `/ToUnicode` CMap resolve via the
  embedded font program's own `cmap` table when there is one (crossing its
  (3,0)/(1,0) code->glyph subtable against its (3,1) Unicode subtable —
  see `_truetype.py`); only fonts with *no* embedded TrueType/OpenType
  program (or a bare Type1/CFF program, not yet parsed) fall back to a
  Latin-1-ish guess.
- Symbol and ZapfDingbats text uses their built-in encodings and glyph
  lists (ZapfDingbats' `a1` -> U+2701 and so on, as the fonts' own
  documentation lists them).
- `get_text("dict")`'s block/line grouping is a y-position/paragraph-gap
  heuristic, not full layout analysis (no column detection, reading-order
  inference, etc.); span `flags` (bold/italic/serif/monospaced) are a
  best-effort read of `/FontDescriptor`, not verified bit-for-bit against
  PyMuPDF's own (unpublished) convention.
- Word breaks that producers express only through positioning (pdfTeX's
  `TJ` offsets, words placed separately with `Td`) are recovered by a gap
  heuristic: a forward gap of more than 0.15 em between two runs on the
  same line becomes a space — after Latin/Greek/Cyrillic letters and
  punctuation only, not after math symbols, arrows or CJK (measured against
  PyMuPDF). Runs 0.8 em or more apart on one baseline (a label and its form
  field, table cells) become separate lines. Right-to-left scripts (Arabic, Hebrew) are put
  into reading order from glyph positions (simplified visual-to-logical
  bidi per line), whether the file stores them in visual or logical order.
  Line breaks are measured perpendicular to the text direction (rotated
  text stays on its line), and text entirely outside the CropBox is
  ignored, as in PyMuPDF.

Non-embedded standard-14 fonts (and common aliases like Arial/Times New
Roman/Courier New) now use real Adobe Core 14 AFM widths (`_afm.py`,
generated by `scripts/generate_afm_widths.py`) rather than a flat guess;
only genuinely non-standard, non-embedded, `/Widths`-less fonts still fall
back to a guessed width.

### Phase 3 known limitations

- Embedded font outlines: TrueType/OpenType (`glyf` outlines, via
  `/FontFile2` or sfnt-wrapped `/FontFile3`) and Type 1 (`/FontFile`, what
  pdfTeX embeds — `_type1.py`: eexec/charstring decryption, Subrs, Flex,
  seac) and bare CFF (`/FontFile3` Type1C and CIDFontType0C — `_cff.py`:
  INDEX/DICT/charset/encoding/FDSelect, Type 2 charstrings incl. flex,
  hint masks, subr bias, arithmetic ops) are rendered. CFF outlines were
  cross-checked glyph-by-glyph against fontTools on every CFF program in the
  benchmark corpus (1,781/1,781 identical). CFF-flavoured OpenType (an sfnt
  with a `CFF ` table instead of `glyf`) isn't rendered yet — that text
  still measures and positions correctly, it just doesn't paint. Hints are
  ignored (no grid-fitting), which is invisible at typical resolutions.
  Non-embedded fonts use an installed copy of the standard font when the
  system has one (macOS ships Helvetica, Times, Courier, Symbol and
  ZapfDingbats; Linux with Ghostscript has the URW base 35 clones) — read
  from where the OS put them, never bundled. Otherwise they substitute the
  bundled Liberation Sans/Serif/Mono (SIL OFL 1.1) by family-keyword
  matching — real outlines, but not the requested typeface's shapes — and
  Symbol/ZapfDingbats draw nothing. `CLEARLEAF_SYSTEM_FONTS=0` turns the
  system lookup off (reproducible output across machines).
- Line width is scaled by the CTM's approximate uniform scale factor, not
  sheared/scaled per-axis independently.
- Shadings are evaluated through a lookup table (axial/radial, 512
  steps), a sampled 256x256 grid (function-based), or Gouraud triangles
  (meshes; patches are subdivided to ~3 device pixels). Tiling patterns are
  rendered once per use at device resolution and repeated; tiles whose
  content overlaps the neighbouring cell (BBox larger than the step) are
  cut at the cell edge.
- JPXDecode (JPEG 2000) images render as a flat placeholder box — the one
  image codec without a decoder. JBIG2 covers arithmetic and MMR generic
  regions and arithmetic symbol dictionaries/text regions (what jbig2enc
  and most scanners produce); Huffman-coded dictionaries and text regions,
  refinement and halftone regions are skipped. Pure-Python JBIG2 is slow on
  big scans (~10 s for a 36-megapixel symbol-coded page). Both baseline and
  progressive DCTDecode JPEGs are supported; arithmetic-coded, lossless,
  and hierarchical JPEG (rare in PDFs) fall back to the placeholder.
- Chroma upsampling in the JPEG decoder is nearest-neighbor, not
  libjpeg's fancier default (triangle filter) — verified against libjpeg
  to be pixel-exact when chroma subsampling is disabled, and close (a few
  units of average per-channel error) when it's on; see `tests/test_jpeg.py`.
- Transparency: constant alpha, blend modes and soft masks (from
  `ExtGState` and image `/SMask`) are applied, but transparency groups are
  painted straight onto the page — isolated and knockout groups aren't
  composited separately, and the soft mask's backdrop/transfer function
  are the only group parameters honoured.
- Anti-aliasing is supersampled in Y (configurable, default 4x) with
  analytic (exact) coverage in X — correct, but slower than a production
  rasterizer's approach; fine for a rendering path that prioritizes
  correctness, per the project brief.

### Phase 4 known limitations

- `extract_image()` raises `NotImplementedError` for JPXDecode (JPEG
  2000) images, the one codec with no decoder yet (they render as a
  placeholder box); `get_images()` still lists them fine.
- `get_images()` follows PyMuPDF: one entry per image *name* (the same
  image under two names is listed twice), images inside Form XObjects
  included, 9 fields -- `full=True` adds the 10th, the referencer. The
  fields aren't verified one-for-one against PyMuPDF: `colorspace` is
  our own family name (`"DeviceRGB"`, `"Indexed"`, `"Separation"`, ...)
  rather than PyMuPDF's exact string, and `alt_colorspace` is always
  `""` (no separate ICC-alternate name is tracked).
- Non-JPEG images are always re-encoded as 8-bit RGB PNG regardless of
  their original bit depth or color space (so a 1-bit DeviceGray image
  extracts as a 24-bit-per-pixel RGB PNG) — correct output, not a
  byte-for-byte-equivalent re-encode.
- A `ColorSpace` given as a page-resource name (e.g. `/CS0` looked up in
  `Resources/ColorSpace`) can't be resolved by `extract_image(xref)`
  alone, since it only has the image object, not the page that
  referenced it — vanishingly rare in practice (image dicts almost
  always use a standard name or an indirect reference instead).

### Phase 5 known limitations

- `clearleaf.merge()` is page concatenation, not general editing: it copies
  each page's content stream(s) and resources (still-encoded, byte for
  byte, so nothing is re-rendered or re-compressed) into a fresh document.
  Document metadata (`/Info`), outlines/bookmarks, form fields
  (`/AcroForm`), and annotations (`/Annots`) are not carried over.
- Output is always a classic (non-compressed) xref table — simple and
  broadly compatible, but not the more compact cross-reference-stream
  format `clearleaf` itself can read.
- To keep annotations, forms and bookmarks while combining files, use
  `doc.insert_pdf(other)` and `doc.save()` instead.

```python
import clearleaf as pdf

pdf.merge(["jan.pdf", "feb.pdf", "mar.pdf"], "q1_receipts.pdf")
```

or from the command line:

```bash
python scripts/merge_pdfs.py -o q1_receipts.pdf jan.pdf feb.pdf mar.pdf
# or merge every *.pdf in a directory, alphabetically:
python scripts/merge_pdfs.py -o q1_receipts.pdf receipts/
```

### Why no rendering dependencies

Font rasterization, JPEG decoding, and PNG encoding are all commonly
delegated to FreeType, libjpeg, and a system zlib/PNG library respectively
— but per this project's standing preference, functionality gets built
in-repo rather than pulled in as a dependency wherever that's reasonably
achievable. `_raster.py` (scanline polygon fill with analytic-coverage
antialiasing), `_truetype.py` (sfnt table/glyph parsing), `_jpeg.py`
(baseline JPEG: Huffman decode, dequantization, IDCT, YCbCr/YCCK->RGB/CMYK)
and `_png.py` (PNG encoding, using only the standard library's `zlib` for
DEFLATE — the same "standard codec library" allowance the project brief
already makes for `FlateDecode`) are all from-scratch implementations,
verified against libjpeg/PIL and independent PNG decoders in the test
suite rather than trusted blindly. Liberation Sans/Serif/Mono (bundled
under `_fontdata/`, SIL OFL 1.1) is the one exception — bundled *data*
(glyph outlines are inherently a design artifact, not an algorithm to
reimplement), not a software dependency.

## Fidelity vs PyMuPDF

Measured on a 66-file corpus of real-world and edge-case PDFs (py-pdf's
sample files and targeted files from pdf.js's test suite), comparing
extracted text, 72-dpi renders and image counts against PyMuPDF used as a
black-box reference. Every file the reference can read passes all three
checks: **text 100%, rendering 100%, image enumeration 100%** (65/65; one
file with a destroyed page tree has no reference — PyMuPDF finds no pages,
clearleaf recovers 3). For four files PyMuPDF's own text is demonstrably
wrong, so they're scored against expected texts derived without clearleaf
([benchmarks/corpus/EXPECTED.md](benchmarks/corpus/EXPECTED.md)); two
renders depend on the system having the standard fonts installed (see the
notes in [benchmarks/results.md](benchmarks/results.md), which has the
per-file numbers). To reproduce:

That corpus is also what the engine was developed against, so it
overstates what to expect. A **held-out set** of 17 real-world PDFs the
engine had never been run on (arXiv papers, IRS forms and publication 17,
NIST, NASA, the US Constitution, and the Universal Declaration of Human
Rights in Chinese, Japanese, Hindi and Russian) first scored **text
16/17, rendering 13/17, images 17/17**, with no crashes. The four render
misses led to three general fixes: lines thinner than a pixel are drawn
faint rather than black, upright glyphs are placed on whole pixels
vertically (quarter pixels horizontally), and shrunk images are smoothed
with a tent filter. It now scores **text 16/17, rendering 17/17, images
17/17** ([benchmarks/results-heldout.md](benchmarks/results-heldout.md)),
but since those fixes were found by looking at these files, the set is
no longer unseen. The remaining miss is the table-of-contents dot leaders
in IRS publication 17.

```bash
pip install -e ".[bench]"
python benchmarks/fetch_corpus.py   # PDFs aren't committed; see corpus/MANIFEST.csv
python benchmarks/run_fidelity.py
python benchmarks/fetch_corpus.py --corpus heldout && python benchmarks/run_fidelity.py --corpus heldout
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT. See [LICENSE](LICENSE). This project does not depend on or link
against MuPDF/PyMuPDF (AGPL-3.0) or any of its code.
