# Contributing

Bug reports with the PDF that shows the problem are the most useful thing
you can send. If the file is private, say which program made it.

## The clean-room rule

clearleaf exists to be an MIT-licensed alternative to PyMuPDF, which is
AGPL. To keep it that way, **never read, copy or translate MuPDF's or
PyMuPDF's source code** while working on clearleaf, and don't submit code
derived from it. Work from the PDF specification (ISO 32000), font and image
format specifications, and permissively licensed projects (check their
licence first). Running PyMuPDF as a program to compare output, as the
benchmarks do, is fine. [clearleaf-project-brief.md](clearleaf-project-brief.md)
has the details.

## Setting up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The library itself has no dependencies; keep it that way. Optional pieces
have extras: `[bench]` (PyMuPDF and numpy, for the benchmark) and `[app]`
(pywebview, for the desktop app).

The editor's browser test needs Playwright:

```bash
pip install playwright && playwright install chromium
pytest tests/test_editor_browser.py
```

## Benchmarks

Changes to text extraction or rendering should keep the fidelity
benchmark where it is:

```bash
pip install -e ".[bench]"
python benchmarks/fetch_corpus.py && python benchmarks/run_fidelity.py
python benchmarks/fetch_corpus.py --corpus heldout && python benchmarks/run_fidelity.py --corpus heldout
```

Mention in your pull request if any file scores lower than before.

## Building the desktop app

```bash
pip install -e ".[app]" pyinstaller
pyinstaller packaging/clearleaf.spec
```

## Releasing

1. Set the version in `pyproject.toml` and `src/clearleaf/__init__.py`, and
   add a section to [CHANGELOG.md](CHANGELOG.md).
2. Publish a GitHub release tagged `v<version>`. The workflows upload the
   library to PyPI and attach the Mac and Windows apps to the release.
