# clearleaf

A permissively-licensed (MIT), clean-room PDF engine for Python — a
drop-in-compatible alternative to [PyMuPDF](https://pymupdf.readthedocs.io/),
which is AGPL-licensed with a paid commercial license from Artifex.

```python
import clearleaf as pdf

doc = pdf.open("file.pdf")
page = doc[0]
print(page.get_text())
page.get_pixmap(dpi=150).save("page1.png")
for xref, *_ in page.get_images():
    img = doc.extract_image(xref)
    open(f"image{xref}.{img['ext']}", "wb").write(img["image"])
doc.close()
```

> **Status: pre-alpha, Phases 1-4.** File structure, object model, text
> extraction, rendering (`get_pixmap()`), and image extraction
> (`get_images()`/`extract_image()`) are all implemented — including a
> from-scratch rasterizer, TrueType glyph rendering, and a baseline +
> progressive JPEG decoder (no FreeType/libjpeg/Pillow at runtime; see "Why
> no rendering dependencies" below). See the coverage table below for
> per-feature fidelity notes and known gaps.

## Install

```bash
pip install -e ".[dev]"
```

Requires Python 3.9+. No runtime dependencies — the whole engine is pure
standard library (`zlib` for Flate/PNG compression, nothing else).

## Why this exists

PyMuPDF wraps MuPDF, which is AGPL-3.0-licensed (commercial licenses
available from Artifex for closed-source use). `clearleaf` targets the same
public Python API shape so migration is close to a drop-in replacement, but
is implemented independently: strictly from the public PDF specification
(ISO 32000-1/2) and permissively-licensed reference material, with no code
read from or copied out of MuPDF/PyMuPDF. See
[clearleaf-project-brief.md](clearleaf-project-brief.md) for the full
clean-room policy and roadmap.

## Feature coverage

| Phase | Feature | Status |
|---|---|---|
| 1 | Header / xref table (classic) parsing | Done |
| 1 | Cross-reference streams (PDF 1.5+) | Done |
| 1 | Object streams (compressed objects) | Done |
| 1 | Object model (dict/array/stream/name/ref/string/number) | Done |
| 1 | Filters: FlateDecode (+ PNG/TIFF predictors), ASCII85Decode, LZWDecode | Done |
| 1 | DCTDecode (JPEG passthrough) | Done |
| 1 | Encryption (RC4/AES password-protected read) | Not started |
| 2 | Content stream tokenizer/interpreter | Done |
| 2 | Font handling: simple fonts (Type1/TrueType), Type0/CID + Identity-H | Done |
| 2 | Encoding: WinAnsi/MacRoman/Standard + /Differences, ToUnicode CMaps | Done |
| 2 | `page.get_text()` — plain text, reading order | Done |
| 2 | `page.get_text("dict")` — blocks/lines/spans with bbox metadata | Done (approximate — see below) |
| 3 | Path construction/painting (fill, stroke, clip), CTM stack | Done |
| 3 | Color spaces: Gray/RGB/CMYK, Indexed, ICCBased, Lab, Separation/DeviceN | Done |
| 3 | Text rendering via real glyph outlines (embedded TrueType + Liberation substitutes for non-embedded standard-14/system fonts) | Done |
| 3 | Image XObjects: raw/Flate samples, ImageMask, Form XObjects | Done |
| 3 | DCTDecode (JPEG) rendering — from-scratch baseline + progressive decoder | Done |
| 3 | `page.get_pixmap()` / PNG output (from-scratch encoder) | Done |
| 4 | `page.get_images()` — enumerate image XObjects (`full=True` also recurses into Form XObjects) | Done |
| 4 | `doc.extract_image(xref)` — JPEG passthrough, everything else re-encoded to PNG | Done (approximate — see below) |
| 5 | `clearleaf.merge(paths, output)` — concatenate pages from multiple PDFs into one | Done (page concatenation only — see below) |

### Phase 2 known limitations

- Type0/CID fonts without `/ToUnicode` and without Identity encoding (e.g.
  genuine Adobe-Japan1/GB1/... character collections) decode to empty text
  — those CID-to-Unicode mapping tables aren't shipped (they're large,
  official Adobe resource files; low value here since virtually all
  real-world CID-font PDFs embed a `/ToUnicode` CMap regardless).
- Symbolic simple fonts without a `/ToUnicode` CMap resolve via the
  embedded font program's own `cmap` table when there is one (crossing its
  (3,0)/(1,0) code->glyph subtable against its (3,1) Unicode subtable —
  see `_truetype.py`); only fonts with *no* embedded TrueType/OpenType
  program (or a bare Type1/CFF program, not yet parsed) fall back to a
  Latin-1-ish guess.
- Symbol/ZapfDingbats' own built-in (non-Latin) encodings aren't modeled —
  their AFM widths are available but code-to-glyph resolution for them
  isn't, so decoded text for those two fonts specifically is unreliable.
- `get_text("dict")`'s block/line grouping is a y-position/paragraph-gap
  heuristic, not full layout analysis (no column detection, reading-order
  inference, etc.); span `flags` (bold/italic/serif/monospaced) are a
  best-effort read of `/FontDescriptor`, not verified bit-for-bit against
  PyMuPDF's own (unpublished) convention.
- Word breaks that producers express only through positioning (pdfTeX's
  `TJ` offsets, words placed separately with `Td`) are recovered by a gap
  heuristic: a forward gap of more than 0.15 em between two runs on the
  same line becomes a space. Right-to-left scripts come out in visual
  (glyph) order, not logical order — no bidi reordering yet.

Non-embedded standard-14 fonts (and common aliases like Arial/Times New
Roman/Courier New) now use real Adobe Core 14 AFM widths (`_afm.py`,
generated by `scripts/generate_afm_widths.py`) rather than a flat guess;
only genuinely non-standard, non-embedded, `/Widths`-less fonts still fall
back to a guessed width.

### Phase 3 known limitations

- Embedded font outlines: only TrueType/OpenType (`glyf` outlines, via
  `/FontFile2` or sfnt-wrapped `/FontFile3`) are rendered. Bare Type1/CFF
  embedded programs have no outline parser yet — that text still measures
  and positions correctly (widths come from `/Widths`/AFM as in Phase 2),
  it just doesn't paint glyph shapes for those specific fonts.
  Non-embedded fonts (including the standard-14) substitute Liberation
  Sans/Serif/Mono (SIL OFL 1.1) by family-keyword matching on the font
  name — real outlines, but not the requested typeface's actual shapes.
- Strokes always render with round joins and round caps — `/LineJoin` and
  `/LineCap` are accepted operands but not honored precisely.
- Line width is scaled by the CTM's approximate uniform scale factor, not
  sheared/scaled per-axis independently.
- Pattern color spaces (tiling and shading patterns) render as a flat
  placeholder gray — implementing them means either re-executing a tiling
  pattern's content stream per tile or evaluating a shading function per
  pixel, both substantial features on their own.
- JPXDecode (JPEG 2000), CCITTFaxDecode (fax/G3/G4), and JBIG2Decode
  images render as a flat placeholder box — only DCTDecode (regular JPEG)
  has a decoder. Both baseline and progressive DCTDecode JPEGs are
  supported; arithmetic-coded, lossless, and hierarchical JPEG (rare in
  PDFs) raise internally and fall back to the placeholder.
- Chroma upsampling in the JPEG decoder is nearest-neighbor, not
  libjpeg's fancier default (triangle filter) — verified against libjpeg
  to be pixel-exact when chroma subsampling is disabled, and close (a few
  units of average per-channel error) when it's on; see `tests/test_jpeg.py`.
- Soft masks, blend modes, and shadings aren't implemented; `ExtGState` is
  only consulted for constant alpha (`/ca`, `/CA`).
- Anti-aliasing is supersampled in Y (configurable, default 4x) with
  analytic (exact) coverage in X — correct, but slower than a production
  rasterizer's approach; fine for a rendering path that prioritizes
  correctness, per the project brief.

### Phase 4 known limitations

- `extract_image()` raises `NotImplementedError` for JPXDecode/
  CCITTFaxDecode/JBIG2Decode images (same codecs that render as a
  placeholder box in Phase 3 — no decoder exists yet); `get_images()`
  still lists them fine, only the extraction step fails.
- The `get_images()` tuple is modeled on PyMuPDF's `(xref, smask, width,
  height, bpc, colorspace, alt_colorspace, name, filter, referencer)`
  shape but isn't verified field-for-field against it: `colorspace` is
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
- This is intentionally narrow scope beyond the project's original v1
  non-goal of "no writing/editing" (see `clearleaf-project-brief.md`) —
  it exists to support merging same-shaped documents (e.g. scanned
  receipts) into one file, not to edit existing PDFs in place.

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
black-box reference. Current pass rates: **text 67%, rendering 61%, image
enumeration 92%** — full per-category and per-file results in
[benchmarks/results.md](benchmarks/results.md). To reproduce:

```bash
pip install -e ".[bench]"
python benchmarks/fetch_corpus.py   # PDFs aren't committed; see corpus/MANIFEST.csv
python benchmarks/run_fidelity.py
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT. See [LICENSE](LICENSE). This project does not depend on or link
against MuPDF/PyMuPDF (AGPL-3.0) or any of its code.
