from __future__ import annotations

from collections.abc import Callable

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram, ReferenceImplementation
from menai_benchmark.suites.deflate_compress import reference

_ITERATIONS = 5

_EXPR = '(let ((deflate (import "deflate-compress"))) ((:: deflate compress) (dict-get inputs "input-data")))'

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
    ("text-4k", lambda: _repeating_text(4096)),
    ("text-32k", lambda: _repeating_text(32768)),
    ("incremental-16k", lambda: _incremental_bytes(16384)),
    ("runs-32k", lambda: _runs(32768)),
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

    raise KeyError(f"unknown deflate-compress fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai raw DEFLATE compressor."""

    name = "deflate-compress"
    description = "Compress raw byte inputs of varying content using the default block encoding."

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
        """Return the compress expression, reading fixture bytes as its input."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )

    def reference_implementations(self) -> list[ReferenceImplementation]:
        """
        Return a pure-Python reimplementation of the same compressor.

        It mirrors deflate-compress.menai step for step -- same greedy LZ77 search,
        same chain and nice-match bounds, same Huffman construction and code-length
        chunking -- and emits byte-identical output for every fixture and block
        mode, so the ratio is the language's cost for identical work.  No compiled
        library is involved: the comparison is Python against Menai, not C against
        Menai.
        """
        return [
            ReferenceImplementation(
                name="python",
                prepare=_fixture_bytes,
                run=reference.compress,
            ),
        ]
