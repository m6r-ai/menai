from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

# Each case is a Menai expression that constructs the value to encode.  The
# cases mirror the json_decode suite's inputs so the two directions can be
# compared, but the value is built directly rather than parsed from JSON, so
# the timing measures encoding alone.

_LONG_STRING = '"' + ("abcdefghij" * 200) + '"'

_FLAT_ARRAY = "(list " + " ".join(str(i) for i in range(100)) + ")"

_FLAT_OBJECT = "(dict " + " ".join(f'"k{i}" {i}' for i in range(50)) + ")"

_MIXED_NESTED_ITEMS = []
for _i in range(20):
    _vals = "(list " + " ".join(str(_i * 5 + _j) for _j in range(5)) + ")"
    _active = "#t" if _i % 2 == 0 else "#f"
    _MIXED_NESTED_ITEMS.append(f'(dict "id" {_i} "vals" {_vals} "active" {_active})')

_MIXED_NESTED = "(list " + " ".join(_MIXED_NESTED_ITEMS) + ")"

_STRING_HEAVY_PAIRS = []
for _i in range(20):
    _STRING_HEAVY_PAIRS.append(f'"field{_i}" "value\\nwith\\ttab\\nand\\nnewlines {_i}"')

_STRING_HEAVY = "(dict " + " ".join(_STRING_HEAVY_PAIRS) + ")"

_NUMBERS_ARRAY = (
    "(list 0 -42 3.14 -3.14 1.0e10 -2.5e-3 100 0.001 999999 -0.5"
    " 42.0 1e5 6.022e23 -1.602e-19 12345.6789)"
)

_UNICODE_STRINGS = (
    '(list "\\u0041\\u0042\\u0043" "\\u00e9\\u00e8\\u00ea"'
    ' "\\u4e2d\\u6587\\u5b57\\u7b26" "\\u03a0\\u03b1\\u03b9")'
)

_CASES: list[tuple[str, str, int]] = [
    ("object", '(dict "name" "Alice" "age" 30 "active" #t "score" 9.5'
               ' "tags" (list "admin" "user")'
               ' "address" (dict "city" "Wonderland" "zip" #none))', 10),
    ("flat_array", _FLAT_ARRAY, 10),
    ("flat_object", _FLAT_OBJECT, 10),
    ("mixed_nested", _MIXED_NESTED, 10),
    ("string_heavy", _STRING_HEAVY, 10),
    ("numbers_array", _NUMBERS_ARRAY, 10),
    ("unicode_strings", _UNICODE_STRINGS, 10),
    ("long_string", _LONG_STRING, 10),
]


def _to_menai_expr(value_expr: str) -> str:
    """Wrap a Menai value expression in an encode call."""
    return f'(let ((json (import "json-encode"))) ((:: json encode) {value_expr}))'


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai JSON encoder."""

    name = "json-encode"
    description = "Encode Menai values of varying structure and size as JSON."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per value to encode."""
        return [
            BenchmarkCase(name=name, input=value_expr, iterations=iters)
            for name, value_expr, iters in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the JSON encode expression, built from the case input value."""
        return MenaiProgram(expression=_to_menai_expr)
