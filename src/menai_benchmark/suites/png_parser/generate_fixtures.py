"""
Generate the PNG fixture files used by the png_parser benchmark suite.

The fixtures are committed to the repository so the benchmark reads
byte-identical inputs on every machine and every run.  This script exists to
document how those files were produced and to allow them to be regenerated.

Run from the repository root:

    python src/menai_benchmark/suites/png_parser/generate_fixtures.py

Output is deterministic: no timestamps, no ancillary chunks, a fixed zlib
compression level, and pixel data derived from a fixed arithmetic pattern.
Re-running the script reproduces the committed files exactly.
"""

from __future__ import annotations

from pathlib import Path
import struct
import zlib

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_COLOUR_TYPE_GREYSCALE = 0
_COLOUR_TYPE_TRUECOLOUR = 2
_COLOUR_TYPE_PALETTE = 3
_COLOUR_TYPE_GREYSCALE_ALPHA = 4
_COLOUR_TYPE_TRUECOLOUR_ALPHA = 6

_CHANNELS: dict[int, int] = {
    _COLOUR_TYPE_GREYSCALE: 1,
    _COLOUR_TYPE_TRUECOLOUR: 3,
    _COLOUR_TYPE_PALETTE: 1,
    _COLOUR_TYPE_GREYSCALE_ALPHA: 2,
    _COLOUR_TYPE_TRUECOLOUR_ALPHA: 4,
}


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Return a PNG chunk: length, type, data, CRC."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _pixel_bytes(colour_type: int, x: int, y: int) -> bytes:
    """Return the raw channel bytes for one pixel, from a fixed arithmetic pattern."""
    if colour_type == _COLOUR_TYPE_GREYSCALE:
        return bytes(((x * 7 + y * 11) % 256,))

    if colour_type == _COLOUR_TYPE_TRUECOLOUR:
        return bytes(((x * 7) % 256, (y * 11) % 256, ((x + y) * 13) % 256))

    if colour_type == _COLOUR_TYPE_PALETTE:
        return bytes(((x + y) % 16,))

    if colour_type == _COLOUR_TYPE_GREYSCALE_ALPHA:
        return bytes(((x * 7 + y * 11) % 256, (x * 3) % 256))

    # _COLOUR_TYPE_TRUECOLOUR_ALPHA
    return bytes(((x * 7) % 256, (y * 11) % 256, ((x + y) * 13) % 256, (x * 5) % 256))


def _palette() -> bytes:
    """Return a 16-entry RGB palette."""
    entries = bytearray()
    for i in range(16):
        entries += bytes(((i * 16) % 256, (255 - i * 16) % 256, (i * 8) % 256))

    return bytes(entries)


def _make_png(width: int, height: int, colour_type: int) -> bytes:
    """Build a non-interlaced 8-bit PNG of the given size and colour type."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)

    raw = bytearray()
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            raw += _pixel_bytes(colour_type, x, y)

    idat = zlib.compress(bytes(raw), 9)

    parts = [_SIGNATURE, _chunk(b"IHDR", ihdr)]
    if colour_type == _COLOUR_TYPE_PALETTE:
        parts.append(_chunk(b"PLTE", _palette()))

    parts.append(_chunk(b"IDAT", idat))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


_FIXTURES: list[tuple[str, int, int, int]] = [
    ("greyscale-64x64.png", 64, 64, _COLOUR_TYPE_GREYSCALE),
    ("greyscale-alpha-64x64.png", 64, 64, _COLOUR_TYPE_GREYSCALE_ALPHA),
    ("palette-128x128.png", 128, 128, _COLOUR_TYPE_PALETTE),
    ("truecolour-128x128.png", 128, 128, _COLOUR_TYPE_TRUECOLOUR),
    ("truecolour-alpha-128x128.png", 128, 128, _COLOUR_TYPE_TRUECOLOUR_ALPHA),
    ("truecolour-192x192.png", 192, 192, _COLOUR_TYPE_TRUECOLOUR),
]


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, width, height, colour_type in _FIXTURES:
        data = _make_png(width, height, colour_type)
        (_FIXTURES_DIR / name).write_bytes(data)
        print(f"{name}: {width}x{height} colour-type={colour_type} {len(data)} bytes")


if __name__ == "__main__":
    main()
