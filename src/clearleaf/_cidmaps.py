"""CID -> Unicode for Adobe's public character collections (Japan1, GB1,
CNS1, Korea1, KR), for CID-keyed fonts that come without a /ToUnicode
CMap. Data: `_data/<Registry>-<Ordering>.cid2uni.gz`, built from Adobe's
cmap-resources by `scripts/build_glyph_data.py` (BSD-3, see
`_data/ADOBE-LICENSE.txt`)."""

from __future__ import annotations

import gzip
import os
import struct
from typing import Optional

_DATA = os.path.join(os.path.dirname(__file__), "_data")
_cache: dict[str, Optional[tuple]] = {}


def _as_text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("latin-1")
    return str(value) if value is not None else ""


def collection_table(registry, ordering) -> Optional[tuple]:
    """CID-indexed tuple of code points (0 = unmapped), or None if the
    collection isn't one of the bundled Adobe collections."""
    name = f"{_as_text(registry)}-{_as_text(ordering)}"
    if name not in _cache:
        path = os.path.join(_DATA, f"{name}.cid2uni.gz")
        try:
            raw = gzip.decompress(open(path, "rb").read())
            _cache[name] = struct.unpack(f">{len(raw) // 4}I", raw)
        except OSError:
            _cache[name] = None
    return _cache[name]
