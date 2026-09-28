"""Build the bundled glyph/character data in src/clearleaf/_data/ from
Adobe's published sources (both BSD-3-Clause, see _data/ADOBE-LICENSE.txt):

- glyphlist.txt.gz: the Adobe Glyph List (adobe-type-tools/agl-aglfn),
  glyph name -> Unicode, used for /Differences names.
- <Registry>-<Ordering>.cid2uni.gz: CID -> Unicode for Adobe's public
  character collections (adobe-type-tools/cmap-resources), inverted from
  the Uni*-UTF32-H CMaps (plus the -V CMaps for vertical-only CIDs). Stored
  as big-endian uint32 per CID (0 = no mapping), then zlib-compressed.

    python scripts/build_glyph_data.py
"""

import gzip
import re
import struct
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "clearleaf" / "_data"
AGL = "https://raw.githubusercontent.com/adobe-type-tools/agl-aglfn/master/glyphlist.txt"
AGL_LICENSE = "https://raw.githubusercontent.com/adobe-type-tools/agl-aglfn/master/LICENSE.md"
CMAPS = "https://raw.githubusercontent.com/adobe-type-tools/cmap-resources/master/"
COLLECTIONS = {  # output name -> (directory, horizontal UTF-32 CMap, vertical CMap or None)
    "Adobe-Japan1": ("Adobe-Japan1-7", "UniJIS-UTF32-H", "UniJIS-UCS2-V"),
    "Adobe-GB1": ("Adobe-GB1-6", "UniGB-UTF32-H", "UniGB-UCS2-V"),
    "Adobe-CNS1": ("Adobe-CNS1-7", "UniCNS-UTF32-H", "UniCNS-UCS2-V"),
    "Adobe-Korea1": ("Adobe-Korea1-2", "UniKS-UTF32-H", "UniKS-UCS2-V"),
    "Adobe-KR": ("Adobe-KR-9", "UniAKR-UTF32-H", None),
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def unicode_to_cid(cmap_text: str) -> list:
    """(unicode, cid) pairs from a Uni* CMap's cidchar/cidrange sections."""
    pairs = []
    for block in re.findall(r"begincidchar(.*?)endcidchar", cmap_text, re.S):
        for src, cid in re.findall(r"<([0-9a-fA-F]+)>\s+(\d+)", block):
            pairs.append((int(src, 16), int(cid)))
    for block in re.findall(r"begincidrange(.*?)endcidrange", cmap_text, re.S):
        for lo, hi, cid in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s+(\d+)", block):
            lo, hi, cid = int(lo, 16), int(hi, 16), int(cid)
            pairs += [(u, cid + (u - lo)) for u in range(lo, hi + 1)]
    return pairs


def _preference(u: int) -> tuple:
    """Lower sorts first: ordinary code points before compatibility forms."""
    compat = (
        0xF900 <= u <= 0xFAFF or 0x2F800 <= u <= 0x2FA1F  # CJK compatibility ideographs
        or 0x2E80 <= u <= 0x2FDF  # CJK radicals supplement / Kangxi radicals (e.g. U+2F47 vs U+65E5)
        or 0xFE30 <= u <= 0xFE4F  # CJK compatibility forms
        or 0xE000 <= u <= 0xF8FF  # private use
    )
    return (compat, u)


def build_collection(directory: str, horizontal: str, vertical) -> bytes:
    best: dict = {}
    for name in [horizontal] + ([vertical] if vertical else []):
        text = fetch(f"{CMAPS}{directory}/CMap/{name}").decode("latin-1")
        for u, cid in unicode_to_cid(text):
            if name == vertical and cid in best:
                continue  # vertical CMaps only fill CIDs the horizontal one lacks
            if cid not in best or _preference(u) < _preference(best[cid]):
                best[cid] = u
    table = [0] * (max(best) + 1)
    for cid, u in best.items():
        table[cid] = u
    return gzip.compress(struct.pack(f">{len(table)}I", *table), mtime=0)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "glyphlist.txt.gz").write_bytes(gzip.compress(fetch(AGL), mtime=0))
    (OUT / "ADOBE-LICENSE.txt").write_bytes(
        b"Adobe Glyph List (adobe-type-tools/agl-aglfn) and CMap resources\n"
        b"(adobe-type-tools/cmap-resources) are both published under this licence:\n\n" + fetch(AGL_LICENSE)
    )
    for out_name, (directory, horizontal, vertical) in COLLECTIONS.items():
        data = build_collection(directory, horizontal, vertical)
        (OUT / f"{out_name}.cid2uni.gz").write_bytes(data)
        print(f"{out_name}: {len(data):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
