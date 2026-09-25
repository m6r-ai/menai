"""
Generate the BMP fixture files used by the bmp-decode benchmark suite.

The fixtures cover uncompressed 24-bit and 32-bit files, bottom-up and top-down
row order, and a width that requires row padding.  They are committed so every
run reads byte-identical inputs.

Run from the repository root:

    python src/menai_benchmark/suites/bmp-decode/generate_fixtures.py

Output is deterministic: pixel data is derived from a fixed arithmetic pattern
and no timestamps or metadata are written, so re-running the script reproduces
the committed files exactly.
"""

from __future__ import annotations

from pathlib import Path
import struct

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_FILE_HEADER_SIZE = 14
_DIB_HEADER_SIZE = 40
_PIXELS_PER_METRE = 2835


def _pixel_bytes(bpp: int, x: int, y: int) -> bytes:
    """Return the raw channel bytes for one pixel, in BMP channel order (BGR/A)."""
    if bpp == 24:
        return bytes(((x + y) * 13 % 256, (y * 11) % 256, (x * 7) % 256))

    # 32-bit: BGRA
    return bytes(((x + y) * 13 % 256, (y * 11) % 256, (x * 7) % 256, (x * 5) % 256))


def _row_bytes(bpp: int, width: int, y: int) -> bytes:
    """Return one row of pixel data, padded to a 4-byte boundary."""
    row = bytearray()
    for x in range(width):
        row += _pixel_bytes(bpp, x, y)

    padding = (-len(row)) % 4
    row += b"\x00" * padding
    return bytes(row)


def _make_bmp(width: int, height: int, bpp: int, top_down: bool) -> bytes:
    """Build an uncompressed BMP file of the given dimensions and bit depth."""
    row_size = len(_row_bytes(bpp, width, 0))
    image_size = row_size * height
    data_offset = _FILE_HEADER_SIZE + _DIB_HEADER_SIZE
    file_size = data_offset + image_size

    # A negative height marks a top-down file.
    stored_height = -height if top_down else height

    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, data_offset)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        _DIB_HEADER_SIZE,
        width,
        stored_height,
        1,
        bpp,
        0,
        image_size,
        _PIXELS_PER_METRE,
        _PIXELS_PER_METRE,
        0,
        0,
    )

    rows = [_row_bytes(bpp, width, y) for y in range(height)]
    if not top_down:
        rows.reverse()

    return file_header + dib_header + b"".join(rows)


_FIXTURES: list[tuple[str, int, int, int, bool]] = [
    ("truecolour-64x64.bmp", 64, 64, 24, False),
    ("truecolour-128x128.bmp", 128, 128, 24, False),
    ("truecolour-topdown-128x128.bmp", 128, 128, 24, True),
    ("truecolour-alpha-128x128.bmp", 128, 128, 32, False),
    ("padded-65x64.bmp", 65, 64, 24, False),
]


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, width, height, bpp, top_down in _FIXTURES:
        data = _make_bmp(width, height, bpp, top_down)
        (_FIXTURES_DIR / name).write_bytes(data)
        orientation = "top-down" if top_down else "bottom-up"
        print(f"{name}: {width}x{height} {bpp}bpp {orientation} {len(data)} bytes")


if __name__ == "__main__":
    main()
