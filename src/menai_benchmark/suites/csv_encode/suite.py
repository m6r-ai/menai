from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_IMPORT = '(let ((csv (import "csv-encode"))) (let ((encode (:: csv encode))) '


def _table(rows: int, cols: int, field: str) -> str:
    """Return an expression that builds a rows x cols table and encodes it."""
    row_expr = (
        "(lambda (r) (list->vector "
        f"(map-list (lambda (c) {field}) (range 0 {cols}))))"
    )
    return (
        _IMPORT
        + f"(encode (map-vector {row_expr} (list->vector (range 0 {rows}))))))"
    )


_PLAIN_FIELD = '(string-concat "f" (integer->string r) "-" (integer->string c))'
_QUOTED_FIELD = '(string-concat "field " (integer->string r) ", " (integer->string c))'
_ESCAPED_FIELD = '(string-concat "say ""hi"" " (integer->string r) "-" (integer->string c))'
_WIDE_FIELD = "(integer->string c)"


_CASES: list[tuple[str, str]] = [
    ("plain-1000x10", _table(1000, 10, _PLAIN_FIELD)),
    ("quoted-1000x10", _table(1000, 10, _QUOTED_FIELD)),
    ("escaped-1000x10", _table(1000, 10, _ESCAPED_FIELD)),
    ("wide-100x100", _table(100, 100, _WIDE_FIELD)),
]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai CSV encoder."""

    name = "csv-encode"
    description = "Encode tables of field strings as RFC 4180 CSV text."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per table shape."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name, _ in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the encode expression for each case's table."""
        expressions = dict(_CASES)

        def expression(case_input: str) -> str:
            return expressions[case_input]

        return MenaiProgram(expression=expression)
