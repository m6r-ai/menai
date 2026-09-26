from __future__ import annotations

import struct

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((bmp (import "bmp-decode"))) ((:: bmp decode) (dict-get inputs "input-data")))'

# Fixture geometry: (name, width, height, bits-per-pixel, top-down row order).
_FIXTURES: list[tuple[str, int, int, int, bool]] = [
    ("truecolour-64x64.bmp", 64, 64, 24, False),
    ("truecolour-128x128.bmp", 128, 128, 24, False),
    ("truecolour-topdown-128x128.bmp", 128, 128, 24, True),
    ("truecolour-alpha-128x128.bmp", 128, 128, 32, False),
    ("padded-65x64.bmp", 65, 64, 24, False),
]

_FILE_HEADER_SIZE = 14
_DIB_HEADER_SIZE = 40
_PIXELS_PER_METRE = 2835

_cache: dict[str, bytes] = {}


def _pixel_bytes(bpp: int, x: int, y: int) -> bytes:
    """Return the raw channel bytes for one pixel, in BMP channel order (BGR/A)."""
    if bpp == 24:
        return bytes(((x + y) * 13 % 256, (y * 11) % 256, (x * 7) % 256))

    return bytes(((x + y) * 13 % 256, (y * 11) % 256, (x * 7) % 256, (x * 5) % 256))


def _row_bytes(bpp: int, width: int, y: int) -> bytes:
    """Return one row of pixel data, padded to a 4-byte boundary."""
    row = bytearray()
    for x in range(width):
        row += _pixel_bytes(bpp, x, y)

    row += b"\x00" * ((-len(row)) % 4)
    return bytes(row)


def _make_bmp(width: int, height: int, bpp: int, top_down: bool) -> bytes:
    """Build an uncompressed BMP file of the given dimensions and bit depth."""
    row_size = len(_row_bytes(bpp, width, 0))
    image_size = row_size * height
    data_offset = _FILE_HEADER_SIZE + _DIB_HEADER_SIZE
    file_size = data_offset + image_size

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


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture bytes for a name, generating them on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, width, height, bpp, top_down in _FIXTURES:
        if fixture_name == name:
            data = _make_bmp(width, height, bpp, top_down)
            _cache[name] = data
            return data

    raise KeyError(f"unknown BMP fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai BMP decoder."""

    name = "bmp-decode"
    description = "Decode uncompressed 24-bit and 32-bit BMP files of various orientations."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture file."""
        return [
            BenchmarkCase(
                name=name.removesuffix(".bmp"),
                input=name,
                iterations=_ITERATIONS,
            )
            for name, _, _, _, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the BMP decode expression, reading fixture bytes as its input."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
