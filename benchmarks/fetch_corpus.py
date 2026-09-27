"""Download the benchmark corpus listed in corpus/MANIFEST.csv.

The PDFs themselves are not committed (they come from third-party repos
under their own licences — see the manifest); this fetches them into
corpus/files/, which is git-ignored.

    python benchmarks/fetch_corpus.py
"""

import csv
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
MANIFEST = HERE / "corpus" / "MANIFEST.csv"
FILES = HERE / "corpus" / "files"


def main() -> int:
    FILES.mkdir(parents=True, exist_ok=True)
    failed = []
    with open(MANIFEST, newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        dest = FILES / f"{row['id']}.pdf"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        try:
            with urllib.request.urlopen(row["url"], timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # network errors, 404s
            failed.append((row["id"], exc))
            continue
        if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
            failed.append((row["id"], "not a PDF (link-only file?)"))
            continue
        dest.write_bytes(data)
    print(f"{len(rows) - len(failed)}/{len(rows)} files in {FILES}")
    for name, err in failed:
        print(f"  failed: {name}: {err}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
