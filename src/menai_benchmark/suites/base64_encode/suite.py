from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 10

_IMPORT = '(let ((b64 (import "base64-encode"))) (let ((encode (:: b64 encode)) (variant (:: b64 variant))) '

_cache: dict[str, bytes] = {}


def _payload(size: int) -> bytes:
    """Return a deterministic byte payload of the given size."""
    return bytes((i * 37 + 11) % 256 for i in range(size))


# (case name, payload size, variant symbol)
_CASES: list[tuple[str, int, str]] = [
    ("standard-1k", 1024, "standard"),
    ("standard-64k", 64 * 1024, "standard"),
    ("unpadded-1k", 1024, "standard-unpadded"),
    ("url-64k", 64 * 1024, "url"),
]


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture payload for a name, generating it on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, size, _ in _CASES:
        if fixture_name == name:
            data = _payload(size)
            _cache[name] = data
            return data

    raise KeyError(f"unknown base64 fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai Base64 encoder."""

    name = "base64-encode"
    description = "Encode bytes as standard and URL-safe Base64 text."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per payload and variant."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name, _, _ in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the encode expression, reading the case's fixture bytes."""
        variants = {name: variant for name, _, variant in _CASES}

        def expression(case_input: str) -> str:
            return (
                _IMPORT
                + f'(encode (dict-get inputs "input-data") (variant \'{variants[case_input]}))))'
            )

        return MenaiProgram(expression=expression, fixture=_fixture_bytes)
