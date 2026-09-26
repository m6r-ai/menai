from __future__ import annotations

import zlib
from collections.abc import Callable

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((zlib (import "zlib-decompress"))) ((:: zlib decompress) (dict-get inputs "input-data")))'

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


_FIXTURES: list[tuple[str, Callable[[], bytes]]] = [
    ("text-4k.zlib", lambda: zlib.compress(_repeating_text(4096), 9)),
    ("text-32k.zlib", lambda: zlib.compress(_repeating_text(32768), 9)),
    ("incremental-16k.zlib", lambda: zlib.compress(_incremental_bytes(16384), 9)),
    ("runs-32k.zlib", lambda: zlib.compress(_runs(32768), 9)),
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

    raise KeyError(f"unknown zlib-decompress fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai zlib stream decompressor."""

    name = "zlib-decompress"
    description = "Decompress zlib streams whose contents compress to varying degrees."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture file."""
        return [
            BenchmarkCase(
                name=name.removesuffix(".zlib"),
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
