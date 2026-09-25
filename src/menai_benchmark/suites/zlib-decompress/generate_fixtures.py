"""
Generate the zlib fixture files used by the zlib-decompress benchmark suite.

Each fixture is a complete zlib stream (RFC 1950) whose decompressed size and
content variety exercise the DEFLATE decompression paths inside zlib-decompress.  The fixtures
are committed so every run reads byte-identical inputs.

Run from the repository root:

    python src/menai_benchmark/suites/zlib-decompress/generate_fixtures.py

Output is deterministic: a fixed zlib compression level and content derived
from fixed arithmetic patterns.  Re-running the script reproduces the
committed files exactly.
"""

from __future__ import annotations

from pathlib import Path
import zlib

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _repeating_text(size: int) -> bytes:
    """Return a repeated English-like phrase of the given length."""
    phrase = b"the quick brown fox jumps over the lazy dog. "
    return (phrase * (size // len(phrase) + 1))[:size]


def _incremental_bytes(size: int) -> bytes:
    """Return bytes that cycle through all 256 values, which compresses poorly."""
    return bytes(i % 256 for i in range(size))


def _runs(size: int) -> bytes:
    """Return bytes made of long runs of a single value, which compress very well."""
    out = bytearray()
    value = 0
    while len(out) < size:
        out += bytes([value]) * 64
        value = (value + 1) % 256

    return bytes(out[:size])


_FIXTURES: list[tuple[str, bytes]] = [
    ("text-4k.zlib", _repeating_text(4096)),
    ("text-32k.zlib", _repeating_text(32768)),
    ("incremental-16k.zlib", _incremental_bytes(16384)),
    ("runs-32k.zlib", _runs(32768)),
]


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, raw in _FIXTURES:
        data = zlib.compress(raw, 9)
        (_FIXTURES_DIR / name).write_bytes(data)
        print(f"{name}: {len(raw)} raw -> {len(data)} compressed bytes")


if __name__ == "__main__":
    main()
