from __future__ import annotations

import json
from typing import Any

from menai import Menai
from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai_benchmark.suites.json_parser.json_parser import parse as _parse_functional

_LONG_STRING = '"' + ("abcdefghij" * 200) + '"'
_DEEP_ARRAY = ("[" * 500) + "0" + ("]" * 500)

# Large flat array: 100 integers — stresses the array resume loop and list building.
_FLAT_ARRAY = "[" + ",".join(str(i) for i in range(100)) + "]"

# Large flat object: 50 key-value pairs — stresses the object key/value loop and dict ops.
_FLAT_OBJECT = "{" + ",".join(f'"k{i}": {i}' for i in range(50)) + "}"

# Mixed nested structure: array of 20 objects, each with a nested array of 5 integers
# and a boolean — exercises the stack machine across multiple container types.
_MIXED_NESTED_ITEMS = []
for _i in range(20):
    _vals = ",".join(str(_i * 5 + _j) for _j in range(5))
    _active = "true" if _i % 2 == 0 else "false"
    _MIXED_NESTED_ITEMS.append(f'{{"id": {_i}, "vals": [{_vals}], "active": {_active}}}')

_MIXED_NESTED = "[" + ",".join(_MIXED_NESTED_ITEMS) + "]"

# String-heavy object: 20 string values with various escapes — stresses the string parser.
_STRING_HEAVY_PAIRS = []
for _i in range(20):
    _STRING_HEAVY_PAIRS.append(f'"field{_i}": "value\\nwith\\ttab\\nand\\nnewlines {_i}"')

_STRING_HEAVY = "{" + ",".join(_STRING_HEAVY_PAIRS) + "}"

# Number variety: integers, negative floats, scientific notation — stresses the number scanner.
_NUMBERS_ARRAY = (
    "[0, -42, 3.14, -3.14, 1.0e10, -2.5e-3, 100, 0.001, 999999, -0.5,"
    " 42.0, 1e5, 6.022e23, -1.602e-19, 12345.6789]"
)

# Unicode escapes: strings with multiple \uXXXX sequences — tests the hex parsing path.
_UNICODE_STRINGS = (
    '["\\u0041\\u0042\\u0043", "\\u00e9\\u00e8\\u00ea",'
    ' "\\u4e2d\\u6587\\u5b57\\u7b26", "\\u03a0\\u03b1\\u03b9"]'
)

_CASES: list[tuple[str, str, int]] = [
    ("object",         '{"name": "Alice", "age": 30, "active": true, "score": 9.5, "tags": ["admin", "user"], '
                     '"address": {"city": "Wonderland", "zip": null}}', 10),
    ("flat_array",     _FLAT_ARRAY,             10),
    ("flat_object",    _FLAT_OBJECT,            10),
    ("mixed_nested",   _MIXED_NESTED,           10),
    ("string_heavy",   _STRING_HEAVY,           10),
    ("numbers_array",  _NUMBERS_ARRAY,          10),
    ("unicode_strings", _UNICODE_STRINGS,        10),
    ("long_string",    _LONG_STRING,            10),
    ("deep_array",     _DEEP_ARRAY,             10),
]


def _to_menai_expr(json_str: str) -> str:
    """Wrap a JSON string in a Menai parse call, escaping for Menai string syntax."""
    escaped = json_str.replace("\\", "\\\\").replace('"', '\\"')
    return f'(let ((json (import "json_parser"))) ((dict-get json "parse") "{escaped}"))'


class Suite(BenchmarkSuite):
    """Benchmark suite comparing Menai, idiomatic Python, and functional Python JSON parsers."""

    name = "json_parser"
    description = "Parse JSON strings of varying structure and size."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per JSON input."""
        return [
            BenchmarkCase(name=name, input=json_str, iterations=iters)
            for name, json_str, iters in _CASES
        ]

    def implementations(self, menai: Menai) -> list[Implementation]:
        """Return Menai, idiomatic Python, and functional Python parser implementations."""
        def prepare_menai(json_str: str) -> Any:
            """Build expression string and compile to bytecode (untimed)."""
            return menai.compile(_to_menai_expr(json_str))

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        def run_python_idiomatic(json_str: str) -> Any:
            """Parse using Python's stdlib json.loads()."""
            return json.loads(json_str)

        def run_python_functional(json_str: str) -> Any:
            """Parse using the pure-functional explicit-stack Python parser."""
            return _parse_functional(json_str)

        return [
            Implementation(name="Menai",               run=run_menai, prepare=prepare_menai),
            Implementation(name="Python (idiomatic)",  run=run_python_idiomatic),
            Implementation(name="Python (functional)", run=run_python_functional),
        ]

    def results_equal(self, a: Any, b: Any) -> bool:
        """Return True if both results are equal parsed values."""
        return a == b
