import io
import os
import struct
import zlib

import pytest

import clearleaf as pdf

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def path(name):
    return os.path.join(FIXTURES, name)


def _parse_png(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks = {}
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        chunks[tag] = payload
        pos += 12 + length
    width, height, bit_depth, color_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = width * channels
    pixels = bytearray()
    row_pos = 0
    for _ in range(height):
        assert raw[row_pos] == 0  # encoder always emits filter type "None"
        row_pos += 1
        pixels += raw[row_pos : row_pos + stride]
        row_pos += stride
    return bytes(pixels), width, height, channels


def test_get_images_lists_flate_rgb_image():
    doc = pdf.open(path("image_flate_rgb.pdf"))
    page = doc[0]
    images = page.get_images()
    assert len(images) == 1
    xref, smask, width, height, bpc, cs, alt_cs, name, filt, referencer = images[0]
    assert (width, height, bpc) == (2, 2, 8)
    assert cs == "DeviceRGB"
    assert filt == "FlateDecode"
    assert name == "Im1"
    assert smask == 0
    assert referencer == 0
    assert xref == 5
    doc.close()


def test_extract_image_flate_rgb_roundtrips_pixel_exact():
    doc = pdf.open(path("image_flate_rgb.pdf"))
    page = doc[0]
    xref = page.get_images()[0][0]
    info = doc.extract_image(xref)
    assert info["ext"] == "png"
    assert (info["width"], info["height"]) == (2, 2)
    assert info["colorspace"] == 3
    pixels, w, h, channels = _parse_png(info["image"])
    assert (w, h, channels) == (2, 2, 3)
    assert pixels == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    doc.close()


def test_extract_image_stencil_mask():
    doc = pdf.open(path("image_mask.pdf"))
    page = doc[0]
    images = page.get_images()
    assert images[0][5] == "ImageMask"
    xref = images[0][0]
    info = doc.extract_image(xref)
    assert info["colorspace"] == 1
    pixels, w, h, channels = _parse_png(info["image"])
    assert (w, h, channels) == (8, 1, 3)
    # 0b10101010: bit=1 -> unpainted (white), bit=0 -> painted (black).
    expected_bit = [1, 0, 1, 0, 1, 0, 1, 0]
    for i, bit in enumerate(expected_bit):
        v = 255 if bit else 0
        assert tuple(pixels[i * 3 : i * 3 + 3]) == (v, v, v)
    doc.close()


def test_extract_image_unsupported_codec_raises():
    doc = pdf.open(path("image_unsupported_codec.pdf"))
    page = doc[0]
    images = page.get_images()
    assert images[0][8] == "CCITTFaxDecode"  # listing itself must not fail
    xref = images[0][0]
    with pytest.raises(NotImplementedError):
        doc.extract_image(xref)
    doc.close()


def test_get_images_full_false_skips_nested_form_images():
    doc = pdf.open(path("image_in_form.pdf"))
    page = doc[0]
    assert page.get_images(full=False) == []
    doc.close()


def test_get_images_full_true_finds_nested_form_images():
    doc = pdf.open(path("image_in_form.pdf"))
    page = doc[0]
    images = page.get_images(full=True)
    assert len(images) == 1
    xref, smask, width, height, bpc, cs, alt_cs, name, filt, referencer = images[0]
    assert (width, height) == (1, 1)
    assert referencer == 5  # xref of the Form XObject that contains it
    doc.close()


def test_extract_image_jpeg_passthrough_matches_original_bytes():
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (4, 4))
    img.putdata([(x * 60, y * 60, 0) for y in range(4) for x in range(4)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    from tests.generate_fixtures import PdfBuilder

    b = PdfBuilder()
    page_content = zlib.compress(b"q 100 0 0 100 0 0 cm /Im1 Do Q")
    b.add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    b.add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    b.add_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
    )
    b.add_stream_object(4, {"Filter": "/FlateDecode"}, page_content)
    b.add_stream_object(
        5,
        {
            "Type": "/XObject", "Subtype": "/Image",
            "Width": 4, "Height": 4, "ColorSpace": "/DeviceRGB",
            "BitsPerComponent": 8, "Filter": "/DCTDecode",
        },
        jpeg_bytes,
    )
    b.write_classic_xref_and_trailer(size=6, root_num=1)
    data = bytes(b.buf)

    doc = pdf.open(data)
    page = doc[0]
    images = page.get_images()
    assert images[0][8] == "DCTDecode"
    info = doc.extract_image(images[0][0])
    assert info["ext"] == "jpg"
    assert info["colorspace"] == 3
    assert info["image"] == jpeg_bytes
    doc.close()
