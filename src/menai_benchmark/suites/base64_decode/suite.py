from __future__ import annotations

import base64

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 10

_IMPORT = '(let ((b64 (import "base64-decode"))) (let ((decode (:: b64 decode)) (variant (:: b64 variant))) '

_cache: dict[str, bytes] = {}


def _payload(size: int) -> bytes:
    """Return a deterministic byte payload of the given size."""
    return bytes((i * 37 + 11) % 256 for i in range(size))


def _standard(size: int) -> str:
    """Return standard padded Base64 for a payload of the given size."""
    return base64.b64encode(_payload(size)).decode("ascii")


def _urlsafe(size: int) -> str:
    """Return URL-safe padded Base64 for a payload of the given size."""
    return base64.urlsafe_b64encode(_payload(size)).decode("ascii")


# (case name, encoded text, variant symbol)
_FIXTURES: list[tuple[str, str, str]] = [
    ("standard-1k", _standard(1024), "standard"),
    ("standard-64k", _standard(64 * 1024), "standard"),
    ("url-1k", _urlsafe(1024), "url"),
    ("url-64k", _urlsafe(64 * 1024), "url"),
]


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture Base64 text for a name as UTF-8 bytes, generating it on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, text, _ in _FIXTURES:
        if fixture_name == name:
            data = text.encode("ascii")
            _cache[name] = data
            return data

    raise KeyError(f"unknown base64 fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai Base64 decoder."""

    name = "base64-decode"
    description = "Decode standard and URL-safe Base64 text to bytes."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name, _, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the decode expression, reading the case's fixture bytes."""
        variants = {name: variant for name, _, variant in _FIXTURES}

        def expression(case_input: str) -> str:
            return (
                _IMPORT
                + f'(decode (bytes->string (dict-get inputs "input-data")) (variant \'{variants[case_input]}))))'
            )

        return MenaiProgram(expression=expression, fixture=_fixture_bytes)
