"""Exceptions raised by clearleaf.

Everything clearleaf raises for a bad or hostile file is a ``PDFError``
(a ``ValueError``), so callers processing untrusted PDFs need one
``except`` clause. Internal errors met while reading a malformed file
(a ``RecursionError`` or ``IndexError`` deep in the parser) are re-raised
as ``PDFError`` at the public API, with the original as ``__cause__``.
"""

from __future__ import annotations

import functools
import os
import struct
import zlib


class PDFError(ValueError):
    """The file is not a PDF clearleaf can read, or is malformed."""


class LimitExceeded(PDFError):
    """The file exceeds a safety limit (see ``clearleaf.LIMITS``)."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


#: Safety limits for untrusted input. Change them in place, e.g.
#: ``clearleaf.LIMITS["max_stream_bytes"] = 1 << 30``.
LIMITS = {
    # Decoded size of any one stream: stops decompression bombs (a few MB
    # of Flate data that inflates to many GB).
    "max_stream_bytes": _env_int("CLEARLEAF_MAX_STREAM_BYTES", 256 << 20),
    # Nesting depth of arrays/dictionaries in objects and content streams.
    "max_nesting": 256,
}

_INTERNAL = (RecursionError, IndexError, KeyError, TypeError, AttributeError, struct.error, zlib.error,
             OverflowError, ZeroDivisionError, UnicodeError, ValueError)


def public(fn):
    """Re-raise internal errors from reading a malformed file as PDFError."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PDFError:
            raise
        except _INTERNAL as exc:
            raise PDFError(f"malformed PDF ({type(exc).__name__}: {exc})") from exc

    return wrapper
