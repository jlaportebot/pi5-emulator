"""PPM to PNG conversion for QEMU builds that lack libpng support in ``screendump``."""
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PPM_MAGIC = b"P6"
PPM_MAX_VALUE = 255
RGB_COLOR_TYPE = 2
BITS_PER_CHANNEL = 8
CHANNELS = 3
FILTER_NONE = b"\x00"


def parse_ppm(data):
    """Return ``(width, height, rgb_bytes)`` from a binary PPM (QEMU writes ``P6``)."""
    fields, position = [], 0
    while len(fields) < 4:
        while position < len(data) and data[position:position + 1].isspace():
            position += 1
        start = position
        while position < len(data) and not data[position:position + 1].isspace():
            position += 1
        if start == position:
            raise ValueError("truncated PPM header")
        fields.append(data[start:position])
    position += 1  # the single whitespace byte after maxval
    magic, width, height, maxval = fields
    if magic != PPM_MAGIC or int(maxval) != PPM_MAX_VALUE:
        raise ValueError("not an 8-bit binary PPM")
    width, height = int(width), int(height)
    pixels = data[position:position + width * height * CHANNELS]
    if len(pixels) != width * height * CHANNELS:
        raise ValueError("PPM pixel data is truncated")
    return width, height, pixels


def _chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def ppm_to_png(data):
    """Encode a binary PPM as an 8-bit RGB PNG (no external libraries)."""
    width, height, pixels = parse_ppm(data)
    stride = width * CHANNELS
    rows = b"".join(FILTER_NONE + pixels[row * stride:(row + 1) * stride]
                    for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, BITS_PER_CHANNEL, RGB_COLOR_TYPE, 0, 0, 0)
    return (PNG_SIGNATURE + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))
