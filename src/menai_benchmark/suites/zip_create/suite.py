from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

# Each case builds a ZIP archive from a generated container.  The entry sets
# mirror the zip-entries and zip-extract suites' fixtures so the two directions
# can be compared.

_DEFLATED = 8
_STORED = 0

_ITERATIONS = 5


def _text_content(size: int) -> bytes:
    """Return a repeated English-like phrase of the given length."""
    phrase = b"the quick brown fox jumps over the lazy dog. "
    return (phrase * (size // len(phrase) + 1))[:size]


def _bytes_literal(data: bytes) -> str:
    """Return a Menai expression constructing a bytes value."""
    return f'(string-hex->bytes "{data.hex()}")'


def _entry_expr(name: str, content: bytes, compression: int) -> str:
    """Return a Menai expression constructing one entry dict."""
    return (
        f'(dict "name" "{name}"'
        f' "compression" {compression}'
        f' "content" {_bytes_literal(content)})'
    )


def _container_expr(entries: list[tuple[str, bytes, int]]) -> str:
    """Return a Menai expression constructing the container dict."""
    entry_exprs = " ".join(_entry_expr(name, content, compression)
                           for name, content, compression in entries)
    return f'(dict "entries" (list {entry_exprs}) "meta" (dict "comment" #none))'


def _many_entries(count: int) -> list[tuple[str, bytes, int]]:
    """Return a list of small deflate entries."""
    return [
        (f"file{i:03d}.txt", _text_content(200 + i), _DEFLATED)
        for i in range(count)
    ]


_FIXTURES: list[tuple[str, list[tuple[str, bytes, int]]]] = [
    ("single-deflate", [
        ("hello.txt", _text_content(1024), _DEFLATED),
    ]),
    ("single-stored", [
        ("hello.txt", _text_content(1024), _STORED),
    ]),
    ("mixed-16", [
        (f"file{i:02d}.txt", _text_content(512 + i * 32),
         _DEFLATED if i % 2 == 0 else _STORED)
        for i in range(16)
    ]),
    ("many-128", _many_entries(128)),
    ("large-256k", [
        ("large.bin", _text_content(256 * 1024), _DEFLATED),
    ]),
]


def _to_menai_expr(container_expr: str) -> str:
    """Wrap a container expression in a create call."""
    return f'(let ((zip (import "zip-create"))) ((:: zip create) {container_expr}))'


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai ZIP archive writer."""

    name = "zip-create"
    description = "Build ZIP archives with stored and deflate entries."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per container to build."""
        return [
            BenchmarkCase(
                name=name,
                input=_container_expr(entries),
                iterations=_ITERATIONS,
            )
            for name, entries in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the create expression, built from the case's container."""
        return MenaiProgram(expression=_to_menai_expr)
