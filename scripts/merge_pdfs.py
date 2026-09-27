"""CLI for clearleaf.merge: concatenate PDFs into one file.

    python scripts/merge_pdfs.py -o combined.pdf a.pdf b.pdf c.pdf

With a directory instead of a file list, merges every *.pdf in it in
alphabetical order:

    python scripts/merge_pdfs.py -o combined.pdf expenses/
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import clearleaf as pdf


def _resolve_inputs(args):
    if len(args) == 1 and os.path.isdir(args[0]):
        paths = sorted(glob.glob(os.path.join(args[0], "*.pdf")))
        if not paths:
            raise SystemExit(f"no *.pdf files found in {args[0]!r}")
        return paths
    return args


def main():
    parser = argparse.ArgumentParser(description="Merge PDFs into one file.")
    parser.add_argument("inputs", nargs="+", help="PDF files, or a single directory of PDFs")
    parser.add_argument("-o", "--output", required=True, help="output PDF path")
    args = parser.parse_args()

    inputs = _resolve_inputs(args.inputs)
    pdf.merge(inputs, args.output)
    print(f"wrote {args.output} ({len(inputs)} input file(s))")
    for p in inputs:
        print(f"  + {p}")


if __name__ == "__main__":
    main()
