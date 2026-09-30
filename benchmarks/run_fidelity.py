"""Fidelity benchmark: clearleaf vs PyMuPDF on the corpus in corpus/MANIFEST.csv.

PyMuPDF is used strictly as a black-box reference (its output only — never
its source), per the clean-room policy in clearleaf-project-brief.md. It is
a benchmark-only dependency: pip install -e ".[bench]".

    python benchmarks/fetch_corpus.py
    python benchmarks/run_fidelity.py            # writes benchmarks/results.md
    python benchmarks/run_fidelity.py --only pdfjs-rotation
    python benchmarks/run_fidelity.py --corpus heldout   # writes benchmarks/results-heldout.md

Per file, three checks:
  text    word-level similarity of get_text() over the first TEXT_PAGES pages
          (character-level for mostly-CJK text, which has no word spaces)
  render  share of pixels within PIXEL_TOL (max channel diff) at 72 dpi over
          the first RENDER_PAGES pages, plus PSNR; a size mismatch scores 0
  images  get_images() count over the first TEXT_PAGES pages matches
Each file runs in its own subprocess with a timeout, so a hang or crash in
one file is recorded as a result instead of stopping the run.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent
MANIFEST = HERE / "corpus" / "MANIFEST.csv"
FILES = HERE / "corpus" / "files"
CORPUS = "corpus"
OUT = HERE / "out"
EXPECTED = HERE / "corpus" / "expected"  # verified texts where PyMuPDF is wrong (see corpus/EXPECTED.md)

TEXT_PAGES = 10
RENDER_PAGES = 2
MAX_WORDS = 5000
PIXEL_TOL = 40
SIZE_SLACK = 2
TEXT_PASS = 0.95
RENDER_PASS = 0.95
TIMEOUT_S = 300


# -- worker (runs in a subprocess, one file) --------------------------------


def _words(text: str) -> list[str]:
    return text.split()[:MAX_WORDS]


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (0x3040 <= cp <= 0x30FF or 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF
            or 0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF or 0xFF00 <= cp <= 0xFFEF)


def _text_score(ours: str, ref: str) -> float:
    """Word-level similarity -- which is what catches merged words -- except
    for text that's mostly CJK, where words aren't space-separated and
    characters are compared instead (whitespace ignored)."""
    ref_chars = [c for c in ref if not c.isspace()]
    if ref_chars and sum(map(_is_cjk, ref_chars)) / len(ref_chars) > 0.3:
        ours_chars = [c for c in ours if not c.isspace()][: MAX_WORDS * 4]
        return SequenceMatcher(None, ours_chars, ref_chars[: MAX_WORDS * 4], autojunk=False).ratio()
    a, b = _words(ours), _words(ref)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _render_score(ours, ref) -> dict:
    import numpy as np

    # Page-size rounding differs by up to a pixel between renderers (e.g.
    # 595.28pt -> 595 vs 596 px); compare the shared area then. A bigger
    # mismatch means a different page box or rotation, which scores 0.
    if abs(ours.width - ref.width) > SIZE_SLACK or abs(ours.height - ref.height) > SIZE_SLACK:
        return {
            "score": 0.0,
            "psnr": None,
            "note": f"size {ours.width}x{ours.height} vs {ref.width}x{ref.height}",
        }
    h, w = min(ours.height, ref.height), min(ours.width, ref.width)
    a = np.frombuffer(ours.samples, dtype=np.uint8).reshape(ours.height, ours.width, 3)[:h, :w].astype(np.int16)
    b = np.frombuffer(ref.samples, dtype=np.uint8).reshape(ref.height, ref.width, ref.n)[:h, :w, :3].astype(np.int16)
    diff = np.abs(a - b)
    score = float((diff.max(axis=2) <= PIXEL_TOL).mean())
    mse = float((diff.astype(np.float64) ** 2).mean())
    psnr = None if mse == 0 else float(10 * np.log10(255.0**2 / mse))
    return {"score": score, "psnr": psnr, "note": ""}


def _check(result: dict, key: str, fn) -> None:
    start = time.perf_counter()
    try:
        result[key] = fn()
    except Exception as exc:
        result[key] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=3)}
    result[key]["seconds"] = round(time.perf_counter() - start, 3)


def run_one(path: Path, password: str = "") -> dict:
    import pymupdf

    import clearleaf

    result: dict = {}
    ref_doc = pymupdf.open(path)
    if ref_doc.needs_pass:
        ref_doc.authenticate(password)
    result["pages"] = len(ref_doc)

    holder = {}

    def open_ours():
        doc = clearleaf.open(path)
        if doc.needs_pass and not doc.authenticate(password):
            raise ValueError("password rejected")
        holder["doc"] = doc
        return {"pages": len(doc)}

    _check(result, "open", open_ours)
    ours = holder.get("doc")

    def text():
        n = min(TEXT_PAGES, len(ref_doc))
        a = "\n".join(ours[i].get_text() for i in range(n))
        b = "\n".join(ref_doc[i].get_text() for i in range(n))
        out = {"score": _text_score(a, b), "ours_words": len(a.split()), "ref_words": len(b.split())}
        expected = EXPECTED / f"{path.stem}.txt"
        if expected.exists():  # PyMuPDF is wrong here: score against the verified text
            out["reference_score"] = out["score"]
            out["score"] = _text_score(a, expected.read_text(encoding="utf-8"))
            out["against"] = "expected"
        return out

    def render():
        pages = []
        for i in range(min(RENDER_PAGES, len(ref_doc))):
            pages.append(_render_score(ours[i].get_pixmap(dpi=72), ref_doc[i].get_pixmap(dpi=72, alpha=False)))
        return {
            "score": min(p["score"] for p in pages),
            "psnr": min((p["psnr"] for p in pages if p["psnr"] is not None), default=None),
            "note": "; ".join(p["note"] for p in pages if p["note"]),
        }

    def images():
        n = min(TEXT_PAGES, len(ref_doc))
        a = sum(len(ours[i].get_images()) for i in range(n))
        b = sum(len(ref_doc[i].get_images()) for i in range(n))
        return {"match": a == b, "ours": a, "ref": b}

    if len(ref_doc) == 0:
        # The reference itself finds no pages (e.g. a destroyed page tree):
        # nothing to compare against.
        result["no_reference"] = f"reference has no pages; clearleaf found {len(ours) if ours else 0}"
    elif ours is not None:
        _check(result, "text", text)
        _check(result, "render", render)
        _check(result, "images", images)
    return result


# -- driver ------------------------------------------------------------------


def _run_in_subprocess(row: dict) -> dict:
    path = FILES / f"{row['id']}.pdf"
    base = {"id": row["id"], "category": row["category"]}
    if not path.exists():
        return {**base, "fatal": "missing — run fetch_corpus.py"}
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, __file__, "--worker", str(path), "--password", row.get("password", "")],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {**base, "fatal": f"timeout after {TIMEOUT_S}s"}
    elapsed = round(time.perf_counter() - start, 1)
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
        return {**base, "fatal": f"worker crashed: {tail}", "seconds": elapsed}
    return {**base, **json.loads(proc.stdout.strip().splitlines()[-1]), "seconds": elapsed}


def _status(r: dict, key: str) -> str:
    if "fatal" in r:
        return "fatal"
    if "error" in r.get("open", {}):
        return "open-error"
    if "no_reference" in r:
        return "n/a"
    c = r.get(key, {})
    if "error" in c:
        return "error"
    if key == "text":
        return "pass" if c["score"] >= TEXT_PASS else "fail"
    if key == "render":
        return "pass" if c["score"] >= RENDER_PASS else "fail"
    return "pass" if c["match"] else "fail"


def _first_error(r: dict) -> str:
    if "fatal" in r:
        return r["fatal"]
    if "no_reference" in r:
        return r["no_reference"]
    for key in ("open", "text", "render", "images"):
        err = r.get(key, {}).get("error")
        if err:
            return f"{key}: {err}"
    return r.get("render", {}).get("note", "")


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "—"


def _environment_note() -> str:
    import platform

    from clearleaf._stdfonts import system_font

    installed = system_font("Helvetica", False, False) is not None and system_font("ZapfDingbats", False, False) is not None
    fonts = ("the standard 14 fonts installed on this machine" if installed
             else "the bundled Liberation substitutes (no installed standard fonts)")
    return (f"Run on {platform.system()} {platform.machine()}, Python {platform.python_version()}. "
            f"Non-embedded standard fonts were drawn with {fonts}; without installed Helvetica/Times/"
            "Courier/Symbol/ZapfDingbats (CLEARLEAF_SYSTEM_FONTS=0) the renders of pdfjs-standard_fonts "
            "and pdfjs-ZapfDingbats score ~0.94 and ~0.93, just under the bar.")


def write_markdown(results: list[dict], path: Path) -> None:
    checks = ("text", "render", "images")
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    lines = [
        "# Fidelity benchmark: clearleaf vs PyMuPDF",
        "",
        f"Corpus: {len(results)} files ([MANIFEST]({CORPUS}/MANIFEST.csv)). "
        f"Pass: text similarity ≥ {TEXT_PASS}, render ≥ {RENDER_PASS:.0%} of pixels within "
        f"±{PIXEL_TOL}/255 at 72 dpi (first {RENDER_PAGES} pages), image count equal "
        f"(first {TEXT_PAGES} pages). Generated by `benchmarks/run_fidelity.py`.",
        "",
        _environment_note(),
        "",
        "Text marked † is scored against a verified expected text instead of PyMuPDF's, "
        "because PyMuPDF's output for that file is demonstrably wrong; the PyMuPDF score "
        "is shown too. How each expectation was derived, without clearleaf: "
        "[EXPECTED.md](corpus/EXPECTED.md). Files PyMuPDF can't read at all (\"no reference\") "
        "can't be compared and are left out of the percentages.",
        "",
        "## By category",
        "",
        "| category | files | text pass | render pass | images pass | crashed / unopenable |",
        "|---|---|---|---|---|---|",
    ]
    rows = sorted(by_cat.items()) + [("**all**", results)]
    for cat, rs in rows:
        comparable = [r for r in rs if _status(r, "text") != "n/a"]
        cells = [cat, str(len(rs)) + (f" ({len(rs) - len(comparable)} no reference)" if len(comparable) < len(rs) else "")]
        for key in checks:
            cells.append(_pct(sum(_status(r, key) == "pass" for r in comparable), len(comparable)))
        broken = sum(_status(r, "text") in ("fatal", "open-error") for r in rs)
        cells.append(str(broken))
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## Per file", "", "| file | category | text | render (PSNR) | images | time (s) | first problem |", "|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r["category"], r["id"])):
        t, rd, im = r.get("text", {}), r.get("render", {}), r.get("images", {})
        text_cell = f"{t['score']:.3f}" if "score" in t else _status(r, "text")
        if t.get("against") == "expected":
            text_cell += f" † (PyMuPDF {t['reference_score']:.3f})"
        if "score" in rd:
            psnr = f" ({rd['psnr']:.1f} dB)" if rd.get("psnr") else ""
            render_cell = f"{rd['score']:.3f}{psnr}"
        else:
            render_cell = _status(r, "render")
        img_cell = f"{im['ours']}/{im['ref']}" if "ours" in im else _status(r, "images")
        problem = _first_error(r).replace("|", "\\|")[:120]
        lines.append(f"| {r['id']} | {r['category']} | {text_cell} | {render_cell} | {img_cell} | {r.get('seconds', '')} | {problem} |")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--only", help="run only manifest ids containing this substring")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--password", default="", help=argparse.SUPPRESS)
    parser.add_argument("--corpus", default="corpus", help="corpus directory under benchmarks/")
    args = parser.parse_args()
    global MANIFEST, FILES, CORPUS
    CORPUS = args.corpus
    MANIFEST = HERE / args.corpus / "MANIFEST.csv"
    FILES = HERE / args.corpus / "files"

    if args.worker:
        print(json.dumps(run_one(args.worker, args.password)))
        return 0

    with open(MANIFEST, newline="") as f:
        rows = [r for r in csv.DictReader(f) if not args.only or args.only in r["id"]]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = []
        for r in pool.map(_run_in_subprocess, rows):
            results.append(r)
            print(f"{r['id']:<55} {_status(r, 'text'):<10} {_status(r, 'render'):<10} {_status(r, 'images')}", flush=True)

    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    if not args.only:
        name = "results.md" if CORPUS == "corpus" else f"results-{CORPUS}.md"
        write_markdown(results, HERE / name)
        print(f"wrote {HERE / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
