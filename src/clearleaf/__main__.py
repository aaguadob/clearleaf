"""``clearleaf`` command line.

    clearleaf edit file.pdf          # open the editor in your browser
    clearleaf text file.pdf          # print the text
    clearleaf merge out.pdf a.pdf b.pdf
"""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="clearleaf", description="Read and edit PDF files.")
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("edit", help="open a PDF in the editor (runs locally, in your browser)")
    e.add_argument("file")
    e.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    e.add_argument("--no-browser", action="store_true", help="print the address instead of opening a browser")
    t = sub.add_parser("text", help="print a PDF's text")
    t.add_argument("file")
    m = sub.add_parser("merge", help="concatenate PDFs")
    m.add_argument("output")
    m.add_argument("inputs", nargs="+")
    args = parser.parse_args(argv)

    from .errors import PDFError

    try:
        if args.command == "edit":
            from .editor import serve

            serve(args.file, open_browser=not args.no_browser, port=args.port)
        elif args.command == "text":
            from . import open as open_pdf

            doc = open_pdf(args.file)
            for page in doc:
                sys.stdout.write(page.get_text())
                sys.stdout.write("\f")
        elif args.command == "merge":
            from . import merge

            merge(args.inputs, args.output)
    except (PDFError, OSError) as exc:
        print(f"clearleaf: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
