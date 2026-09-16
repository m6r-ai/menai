"""
Generate the raw input fixture files used by the deflate benchmark suite.

Unlike the other binary-format suites, deflate *produces* a compressed stream,
so its fixtures are the uncompressed inputs to be compressed.  The fixtures are
committed so every run reads byte-identical inputs.

Run from the repository root:

    python src/menai_benchmark/suites/deflate/generate_fixtures.py

Output is deterministic: content is derived from fixed arithmetic patterns, so
re-running the script reproduces the committed files exactly.
"""

from __future__ import annotations

from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _repeating_text(size: int) -> bytes:
    """Return a repeated English-like phrase of the given length."""
    phrase = b"the quick brown fox jumps over the lazy dog. "
    return (phrase * (size // len(phrase) + 1))[:size]


def _incremental_bytes(size: int) -> bytes:
    """Return bytes that cycle through all 256 values, which compress poorly."""
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
    ("text-4k.bin", _repeating_text(4096)),
    ("text-32k.bin", _repeating_text(32768)),
    ("incremental-16k.bin", _incremental_bytes(16384)),
    ("runs-32k.bin", _runs(32768)),
]


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, raw in _FIXTURES:
        (_FIXTURES_DIR / name).write_bytes(raw)
        print(f"{name}: {len(raw)} bytes")


if __name__ == "__main__":
    main()
