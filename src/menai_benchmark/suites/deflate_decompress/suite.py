from __future__ import annotations

import zlib
from collections.abc import Callable

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((deflate (import "deflate-decompress"))) ((:: deflate decompress) (dict-get inputs "input-data")))'

_RAW_DEFLATE_WBITS = -15

_cache: dict[str, bytes] = {}


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


def _compress(raw: bytes, level: int) -> bytes:
    """Compress *raw* to a raw DEFLATE stream at the given level."""
    compressor = zlib.compressobj(level=level, wbits=_RAW_DEFLATE_WBITS)
    return compressor.compress(raw) + compressor.flush()


_FIXTURES: list[tuple[str, Callable[[], bytes]]] = [
    ("text-4k", lambda: _compress(_repeating_text(4096), 9)),
    ("text-32k", lambda: _compress(_repeating_text(32768), 9)),
    ("incremental-16k", lambda: _compress(_incremental_bytes(16384), 9)),
    ("stored-8k", lambda: _compress(_repeating_text(8192), 0)),
    ("runs-32k", lambda: _compress(_runs(32768), 9)),
]


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture bytes for a name, generating them on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, generate in _FIXTURES:
        if fixture_name == name:
            data = generate()
            _cache[name] = data
            return data

    raise KeyError(f"unknown deflate-decompress fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai raw DEFLATE decompressor."""

    name = "deflate-decompress"
    description = "Decompress raw DEFLATE streams of varying content and block encoding."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture file."""
        return [
            BenchmarkCase(
                name=name,
                input=name,
                iterations=_ITERATIONS,
            )
            for name, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the decompress expression, reading fixture bytes as its input."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
