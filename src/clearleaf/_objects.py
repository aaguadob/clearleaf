"""PDF object model (ISO 32000-1:2008 section 7.3).

Mapping from PDF object types to Python representations:
  - Boolean       -> bool
  - Numeric       -> int | float
  - String        -> bytes (raw, undecoded — literal and hex strings both
                     decode to their raw byte content)
  - Name          -> PDFName (a str subclass, so it compares/hashes equal
                     to a plain str and can be used directly as a dict key)
  - Array         -> list
  - Dictionary    -> dict (PDFName keys behave like plain str keys)
  - Stream        -> PDFStream
  - Null          -> None
  - Indirect ref  -> PDFReference
"""

from __future__ import annotations


class PDFName(str):
    """A PDF name object, e.g. ``/Type``. Behaves like ``str`` everywhere."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"/{str.__str__(self)}"


class PDFReference:
    """An indirect reference, e.g. ``12 0 R``."""

    __slots__ = ("num", "gen")

    def __init__(self, num: int, gen: int):
        self.num = num
        self.gen = gen

    def __eq__(self, other):
        return (
            isinstance(other, PDFReference)
            and self.num == other.num
            and self.gen == other.gen
        )

    def __hash__(self):
        return hash((self.num, self.gen))

    def __repr__(self) -> str:
        return f"{self.num} {self.gen} R"


class PDFStream:
    """A stream object: a dictionary plus raw (still-encoded) byte data."""

    __slots__ = ("dict", "raw_data")

    def __init__(self, dict_: dict, raw_data: bytes):
        self.dict = dict_
        self.raw_data = raw_data

    def get_data(self, resolver=None) -> bytes:
        """Return the decoded stream data, running it through every filter
        named in /Filter (in order), with /DecodeParms applied per filter.
        """
        from ._filters import decode_stream

        return decode_stream(self.dict, self.raw_data, resolver=resolver)

    def __repr__(self) -> str:
        return f"PDFStream({self.dict!r}, {len(self.raw_data)} bytes)"


def resolve(obj, resolver):
    """Resolve ``obj`` one level if it's a PDFReference, else return as-is."""
    if isinstance(obj, PDFReference):
        return resolver(obj)
    return obj
