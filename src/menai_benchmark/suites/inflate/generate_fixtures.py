"""
Generate the raw DEFLATE fixture files used by the inflate benchmark suite.

Each fixture is a raw DEFLATE stream (RFC 1951, no zlib wrapper) whose
decompressed size and block encoding exercise the inflate paths.  The fixtures
are committed so every run reads byte-identical inputs.

Run from the repository root:

    python src/menai_benchmark/suites/inflate/generate_fixtures.py

Output is deterministic.  The compression level is fixed per fixture and the
content is derived from fixed arithmetic patterns, so re-running the script
reproduces the committed files exactly.
"""

from __future__ import annotations

from pathlib import Path
import zlib

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_RAW_DEFLATE_WBITS = -15


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


def _compress(raw: bytes, level: int) -> bytes:
    """Compress *raw* to a raw DEFLATE stream at the given level."""
    compressor = zlib.compressobj(level=level, wbits=_RAW_DEFLATE_WBITS)
    return compressor.compress(raw) + compressor.flush()


def _compress_stored(raw: bytes) -> bytes:
    """Compress *raw* to a raw DEFLATE stream using stored blocks only."""
    return _compress(raw, 0)


_FIXTURES: list[tuple[str, bytes]] = [
    ("text-4k.deflate", _repeating_text(4096)),
    ("text-32k.deflate", _repeating_text(32768)),
    ("incremental-16k.deflate", _incremental_bytes(16384)),
    ("stored-8k.deflate", _repeating_text(8192)),
    ("runs-32k.deflate", _runs(32768)),
]

_STORED_FIXTURES = {"stored-8k.deflate"}


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, raw in _FIXTURES:
        if name in _STORED_FIXTURES:
            data = _compress_stored(raw)

        else:
            data = _compress(raw, 9)

        (_FIXTURES_DIR / name).write_bytes(data)
        print(f"{name}: {len(raw)} raw -> {len(data)} compressed bytes")


if __name__ == "__main__":
    main()
