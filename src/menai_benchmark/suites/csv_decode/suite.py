from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((csv (import "csv-decode"))) ((:: csv decode) (bytes->string (dict-get inputs "input-data"))))'

_cache: dict[str, bytes] = {}


def _plain(rows: int, cols: int) -> str:
    """Return unquoted CSV with `rows` rows of `cols` fields."""
    return "\r\n".join(
        ",".join(f"f{r}-{c}" for c in range(cols))
        for r in range(rows)
    )


def _quoted(rows: int, cols: int) -> str:
    """Return CSV whose fields are quoted and contain embedded delimiters."""
    return "\r\n".join(
        ",".join(f'"field {r}, {c}"' for c in range(cols))
        for r in range(rows)
    )


def _escaped(rows: int, cols: int) -> str:
    """Return CSV whose quoted fields contain doubled quotes."""
    return "\r\n".join(
        ",".join(f'"say ""hi"" {r}-{c}"' for c in range(cols))
        for r in range(rows)
    )


def _multiline(rows: int) -> str:
    """Return CSV whose quoted fields contain embedded line terminators."""
    return "\r\n".join(
        f'"line one\nline two {r}",{r}' for r in range(rows)
    )


def _wide(rows: int, cols: int) -> str:
    """Return CSV with many fields per row."""
    return "\r\n".join(
        ",".join(str(c) for c in range(cols))
        for _ in range(rows)
    )


_FIXTURES: dict[str, str] = {
    "plain-1000x10": _plain(1000, 10),
    "quoted-1000x10": _quoted(1000, 10),
    "escaped-1000x10": _escaped(1000, 10),
    "multiline-1000": _multiline(1000),
    "wide-100x100": _wide(100, 100),
}


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture CSV for a name as UTF-8 bytes, generating it on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    data = _FIXTURES[name].encode("utf-8")
    _cache[name] = data
    return data


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai CSV decoder."""

    name = "csv-decode"
    description = "Decode RFC 4180 CSV text of varying shape into a table."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the decode expression, reading the case's fixture bytes."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
