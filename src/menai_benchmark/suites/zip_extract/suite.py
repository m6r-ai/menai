from __future__ import annotations

import io
import zipfile

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((zip (import "zip-extract"))) ((:: zip extract) (dict-get inputs "input-data")))'

_FIXED_DATE_TIME = (2024, 1, 1, 0, 0, 0)

_DEFLATED = zipfile.ZIP_DEFLATED
_STORED = zipfile.ZIP_STORED

_cache: dict[str, bytes] = {}


def _text_content(size: int) -> bytes:
    """Return a repeated English-like phrase of the given length."""
    phrase = b"the quick brown fox jumps over the lazy dog. "
    return (phrase * (size // len(phrase) + 1))[:size]


def _make_zip(entries: list[tuple[str, bytes, int]]) -> bytes:
    """Build a ZIP archive from (name, content, compression) triples."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content, compression in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = compression
            info.external_attr = 0
            archive.writestr(info, content)

    return buffer.getvalue()


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


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture bytes for a name, generating them on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, entries in _FIXTURES:
        if fixture_name == name:
            data = _make_zip(entries)
            _cache[name] = data
            return data

    raise KeyError(f"unknown ZIP fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai ZIP archive extractor."""

    name = "zip-extract"
    description = "Extract ZIP archives, decompressing stored and deflate entries."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture."""
        return [
            BenchmarkCase(
                name=name,
                input=name,
                iterations=_ITERATIONS,
            )
            for name, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the extract expression, reading the case's fixture bytes."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
