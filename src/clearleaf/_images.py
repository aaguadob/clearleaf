"""Phase 4: image extraction (`Page.get_images()` / `PDFDocument.extract_image()`).

Mirrors PyMuPDF's split: `get_images()` lists every image XObject visible
from a page's resources (by xref, plus lightweight metadata), and
`extract_image()` does the actual decode/re-encode work for one xref.
JPEG (DCTDecode) images are returned as their original, still-encoded
bytes (no re-encode, no quality loss); everything else is decoded via the
same colorspace machinery `_contentpaint.py` uses for rendering, then
re-encoded to PNG.
"""

from __future__ import annotations

from ._colorspace import (
    DEVICE_CMYK,
    DEVICE_GRAY,
    DEVICE_RGB,
    IndexedColorSpace,
    LabColorSpace,
    PatternColorSpace,
    SeparationColorSpace,
    load_colorspace,
)
from ._objects import PDFReference, PDFStream

_PASSTHROUGH_EXT = {"DCTDecode": "jpg", "DCT": "jpg"}
_UNSUPPORTED_CODECS = {"JPXDecode", "JBIG2Decode"}


def _filter_names(doc, d: dict) -> list:
    filt = doc.resolve(d.get("Filter"))
    if filt is None:
        return []
    return [str(doc.resolve(f)) for f in filt] if isinstance(filt, list) else [str(filt)]


def _mask_xref(doc, d: dict) -> int:
    """The xref of /SMask (soft mask) or a stream-valued /Mask (stencil
    mask) — not a /Mask color-key array, which isn't a separate image."""
    for key in ("SMask", "Mask"):
        ref = d.get(key)
        if isinstance(ref, PDFReference) and isinstance(doc.resolve(ref), PDFStream):
            return ref.num
    return 0


def _colorspace_name(cs) -> str:
    if cs is DEVICE_GRAY:
        return "DeviceGray"
    if cs is DEVICE_RGB:
        return "DeviceRGB"
    if cs is DEVICE_CMYK:
        return "DeviceCMYK"
    if isinstance(cs, IndexedColorSpace):
        return "Indexed"
    if isinstance(cs, LabColorSpace):
        return "Lab"
    if isinstance(cs, SeparationColorSpace):
        return "Separation"
    if isinstance(cs, PatternColorSpace):
        return "Pattern"
    return "DeviceRGB"


def _read_bits(data: bytes, bit_offset: int, nbits: int) -> int:
    val = 0
    byte_idx, bit_in_byte, bits_read = bit_offset // 8, bit_offset % 8, 0
    while bits_read < nbits:
        if byte_idx >= len(data):
            break
        byte = data[byte_idx]
        avail = 8 - bit_in_byte
        take = min(avail, nbits - bits_read)
        chunk = (byte >> (avail - take)) & ((1 << take) - 1)
        val = (val << take) | chunk
        bits_read += take
        bit_in_byte += take
        if bit_in_byte >= 8:
            bit_in_byte, byte_idx = 0, byte_idx + 1
    return val


def _image_mask_to_rgb(doc, data: bytes, width: int, height: int, d: dict) -> bytes:
    decode = doc.resolve(d.get("Decode"))
    invert = isinstance(decode, list) and len(decode) >= 2 and float(doc.resolve(decode[0])) == 1.0
    row_bytes = (width + 7) // 8
    out = bytearray(width * height * 3)
    idx = 0
    for y in range(height):
        row_off = y * row_bytes
        for x in range(width):
            offset = row_off + (x // 8)
            bit = (data[offset] >> (7 - (x % 8))) & 1 if offset < len(data) else 0
            painted = (bit == 0) != invert
            v = 0 if painted else 255
            out[idx], out[idx + 1], out[idx + 2] = v, v, v
            idx += 3
    return bytes(out)


def _sampled_to_rgb(data: bytes, width: int, height: int, cs, bpc: int) -> bytes:
    n = cs.num_components
    row_bytes = (width * n * bpc + 7) // 8
    max_val = float((1 << bpc) - 1)
    is_indexed = isinstance(cs, IndexedColorSpace)
    out = bytearray(width * height * 3)
    idx = 0
    for y in range(height):
        row_bit_base = y * row_bytes * 8
        for x in range(width):
            bit_offset = row_bit_base + x * n * bpc
            comps = []
            for c in range(n):
                v = _read_bits(data, bit_offset + c * bpc, bpc)
                comps.append(float(v) if is_indexed else (v / max_val if max_val > 0 else 0.0))
            r, g, b = cs.to_rgb(comps)
            out[idx], out[idx + 1], out[idx + 2] = r, g, b
            idx += 3
    return bytes(out)


def _walk_resources(doc, resources: dict, full: bool, referencer: int, seen: set, out: list) -> None:
    xobj_dict = doc.resolve(resources.get("XObject")) if isinstance(resources, dict) else None
    if not isinstance(xobj_dict, dict):
        return
    for name, ref in xobj_dict.items():
        xobj = doc.resolve(ref)
        if not isinstance(xobj, PDFStream):
            continue
        subtype = str(doc.resolve(xobj.dict.get("Subtype", "")))
        xref = ref.num if isinstance(ref, PDFReference) else 0
        if subtype == "Image":
            if xref and xref in seen:
                continue
            seen.add(xref)
            d = xobj.dict
            width = int(doc.resolve(d.get("Width", 0)) or 0)
            height = int(doc.resolve(d.get("Height", 0)) or 0)
            bpc = int(doc.resolve(d.get("BitsPerComponent", 8)) or 8)
            is_mask = bool(doc.resolve(d.get("ImageMask", False)))
            filters = _filter_names(doc, d)
            filter_name = filters[-1] if filters else ""
            cs_name = "ImageMask" if is_mask else _colorspace_name(
                load_colorspace(doc, d.get("ColorSpace"), resources)
            )
            out.append((
                xref, _mask_xref(doc, d), width, height, bpc,
                cs_name, "", str(name), filter_name, referencer,
            ))
        elif subtype == "Form" and full:
            form_resources = doc.resolve(xobj.dict.get("Resources"))
            if isinstance(form_resources, dict):
                _walk_resources(doc, form_resources, full, xref, seen, out)


def get_page_images(doc, resources: dict, full: bool) -> list:
    """One tuple per image XObject reachable from `resources`:
    (xref, smask, width, height, bpc, colorspace, alt_colorspace, name,
    filter, referencer) — modeled on PyMuPDF's `Page.get_images()` shape.
    `alt_colorspace` is always `""` (we don't track a separate ICC
    alternate name); `referencer` is the xref of the Form XObject an image
    was found inside, or 0 for images directly in the page's own
    resources. With `full=True`, Form XObjects are also walked recursively
    to find images not directly in the page's own /Resources."""
    out: list = []
    seen: set = set()
    _walk_resources(doc, resources, full, 0, seen, out)
    return out


def extract_image(doc, xref: int) -> dict:
    """Decode image object `xref` to a dict: `{"ext", "width", "height",
    "colorspace", "bpc", "smask", "image"}`. JPEG (DCTDecode) images come
    back as their original encoded bytes (`ext="jpg"`); everything else is
    decoded and re-encoded as PNG (`ext="png"`). Raises `NotImplementedError`
    for codecs with no decoder (JPXDecode/JBIG2Decode)."""
    xobj = doc.get_object(xref)
    if not isinstance(xobj, PDFStream):
        raise ValueError(f"object {xref} is not an image XObject")
    d = xobj.dict
    if str(doc.resolve(d.get("Subtype", ""))) != "Image":
        raise ValueError(f"object {xref} is not an image XObject")

    width = int(doc.resolve(d.get("Width", 0)) or 0)
    height = int(doc.resolve(d.get("Height", 0)) or 0)
    bpc = int(doc.resolve(d.get("BitsPerComponent", 8)) or 8)
    smask_xref = _mask_xref(doc, d)

    filters = _filter_names(doc, d)
    last_filter = filters[-1] if filters else ""

    if last_filter in _PASSTHROUGH_EXT:
        data = xobj.get_data(resolver=doc.resolve)
        cs_obj = d.get("ColorSpace")
        n = load_colorspace(doc, cs_obj, None).num_components if cs_obj is not None else 3
        return {
            "ext": _PASSTHROUGH_EXT[last_filter], "width": width, "height": height,
            "colorspace": n, "bpc": bpc, "smask": smask_xref, "image": data,
        }
    if last_filter in _UNSUPPORTED_CODECS:
        raise NotImplementedError(f"image extraction for {last_filter} is not implemented")

    from ._png import encode_png

    data = xobj.get_data(resolver=doc.resolve)
    is_mask = bool(doc.resolve(d.get("ImageMask", False)))
    if is_mask:
        rgb = _image_mask_to_rgb(doc, data, width, height, d)
        n = 1  # PyMuPDF reports 1 component for stencil masks
    else:
        cs = load_colorspace(doc, d.get("ColorSpace"), None)
        rgb = _sampled_to_rgb(data, width, height, cs, bpc)
        n = cs.num_components
    png = encode_png(rgb, width, height, channels=3)
    return {
        "ext": "png", "width": width, "height": height,
        "colorspace": n, "bpc": bpc, "smask": smask_xref, "image": png,
    }
