from __future__ import annotations

import struct
import zlib

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((png (import "png-decode"))) ((:: png decode) (dict-get inputs "input-data")))'

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_COLOUR_TYPE_GREYSCALE = 0
_COLOUR_TYPE_TRUECOLOUR = 2
_COLOUR_TYPE_PALETTE = 3
_COLOUR_TYPE_GREYSCALE_ALPHA = 4
_COLOUR_TYPE_TRUECOLOUR_ALPHA = 6

# Fixture geometry: (name, width, height, colour type).
_FIXTURES: list[tuple[str, int, int, int]] = [
    ("greyscale-64x64.png", 64, 64, _COLOUR_TYPE_GREYSCALE),
    ("greyscale-alpha-64x64.png", 64, 64, _COLOUR_TYPE_GREYSCALE_ALPHA),
    ("palette-128x128.png", 128, 128, _COLOUR_TYPE_PALETTE),
    ("truecolour-128x128.png", 128, 128, _COLOUR_TYPE_TRUECOLOUR),
    ("truecolour-alpha-128x128.png", 128, 128, _COLOUR_TYPE_TRUECOLOUR_ALPHA),
    ("truecolour-192x192.png", 192, 192, _COLOUR_TYPE_TRUECOLOUR),
]

_cache: dict[str, bytes] = {}


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


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture bytes for a name, generating them on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, width, height, colour_type in _FIXTURES:
        if fixture_name == name:
            data = _make_png(width, height, colour_type)
            _cache[name] = data
            return data

    raise KeyError(f"unknown PNG fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai PNG decoder."""

    name = "png-decode"
    description = "Decode non-interlaced 8-bit PNG files of various colour types and sizes."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture file."""
        return [
            BenchmarkCase(
                name=name.removesuffix(".png"),
                input=name,
                iterations=_ITERATIONS,
            )
            for name, _, _, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the PNG decode expression, reading fixture bytes as its input."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
