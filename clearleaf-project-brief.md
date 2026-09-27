# Project Brief: Clearleaf — a permissively-licensed PDF engine

## Goal
Build an MIT/Apache-2.0-licensed Python library for reading, rendering, and
extracting content from PDF files, as a drop-in-compatible alternative to
PyMuPDF (which is AGPL-licensed with a paid commercial license from Artifex).

**Non-goal for v1**: writing/editing PDFs. Read-only first.

## Critical legal constraint (read this first)
Do NOT read, copy, transliterate, or closely reference MuPDF's or PyMuPDF's
source code at any point. This must be a clean-room implementation built
strictly from:
- The public PDF specification (ISO 32000-1:2008 / ISO 32000-2:2020)
- Independent, permissively-licensed reference libraries (e.g. pypdf,
  pikepdf, qpdf — check their licenses before consulting or reusing any
  code) for cross-checking behavior, not for copying implementation
- Font/color-space/compression standards (TrueType/OpenType specs, ICC
  color profile spec, standard image codec libraries like zlib/libjpeg)

Architectural similarity to MuPDF is fine (both must parse the same file
format), but do not mirror MuPDF's internal code structure, variable
names, or function decomposition. Match PyMuPDF's *public Python API*
closely (that's the point — migration should be near drop-in) but
implement the internals independently.

## Target public API (mirror PyMuPDF's shape)
```python
import clearleaf as pdf

doc = pdf.open("file.pdf")
page = doc[0]
text = page.get_text()
pix = page.get_pixmap(dpi=150)
pix.save("page1.png")
images = page.get_images()
doc.close()
```

## Scope, in build order

### Phase 1 — Parsing foundation
- PDF file structure parser: header, xref table (both classic and
  cross-reference streams), trailer, object streams
- Object model: dictionaries, arrays, streams, references, indirect objects
- Filter/decoder support: FlateDecode, DCTDecode (JPEG), ASCII85Decode,
  LZWDecode at minimum
- Handle both linearized and non-linearized PDFs
- Basic encryption support (RC4/AES for password-protected read access) —
  can be deferred to Phase 4 if time-constrained

### Phase 2 — Text extraction
- Content stream tokenizer/interpreter (the PDF "mini-language" of
  operators: Tj, TJ, Td, Tf, etc.)
- Font handling: embedded fonts (Type1, TrueType, Type0/CID), font
  encoding tables, glyph-to-Unicode mapping (ToUnicode CMaps)
- `page.get_text()` returning plain text in reading order
- `page.get_text("dict")` or similar returning text with position/bbox
  metadata (this is a commonly used PyMuPDF feature — check their docs
  for the exact structured output shape to match)

### Phase 3 — Rendering (rasterization)
- Full content stream interpreter covering: path construction/painting,
  text rendering, image placement (XObjects), clipping, transformation
  matrices (CTM stack)
- Font glyph rasterization (can initially delegate to an existing
  permissively-licensed font rasterizer like FreeType via bindings,
  rather than writing a rasterizer from scratch — confirm FreeType's
  license, FTL/GPL dual-license, is acceptable for this use case)
- Color space handling: DeviceRGB, DeviceGray, DeviceCMYK, Indexed, ICC
- Output to a raster buffer / PNG via `page.get_pixmap()`
- This is the hardest and highest-value phase — it's what "counts" as
  parity with MuPDF. Prioritize getting this correct over speed
  initially.

### Phase 4 — Image extraction
- `page.get_images()`: enumerate embedded image XObjects
- Extract raw image bytes for JPEG (DCTDecode) and re-encode
  Flate-compressed raster images to PNG

## Validation / benchmarking
- Build a test corpus covering: simple text PDFs, PDFs with embedded
  non-Latin fonts (CJK, Arabic, Cyrillic), rotated pages, PDFs with
  transparency/alpha, PDFs with vector graphics, scanned-image-only PDFs
  (no text layer — extraction should correctly return empty/near-empty
  text, not error)
- For each test file, compare rendered PNG output and extracted text
  against PyMuPDF's output for a similarity/regression check —
  **for comparison/testing purposes only**, never copy PyMuPDF's code
- Track a pass-rate metric per feature category, publish it in the README

## Repo setup
- Python package `clearleaf`, `pip install -e .` for dev
- Use compiled extensions (Rust via PyO3, or C via Cython) for the
  content-stream interpreter and rasterizer once the pure-Python
  prototype works, since PDF parsing/rendering is performance-sensitive
- Standard project layout: `src/clearleaf/`, `tests/`, `benchmarks/`,
  `docs/`
- CI: run the test corpus on every PR, track rendering-fidelity
  regression
- License: MIT (or Apache-2.0 if patent-grant language is wanted)
- README modeled on LibreYOLO's: a 3–4 line usage example up top,
  install instructions, feature/coverage table, license note explaining
  the AGPL alternative angle

## Definition of done for v1
- Phases 1–4 complete for a representative corpus of real-world PDFs
- `pip install clearleaf` works from PyPI
- README with working 3-line example
- Published rendering-fidelity benchmark vs. PyMuPDF on the test corpus
