from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_IMPORT = '(let ((html (import "html-encode"))) (let ((encode (:: html encode))) '


def _wide(count: int) -> str:
    """Encode one root with `count` sibling element children."""
    return (
        _IMPORT
        + '(encode (list (dict "tag" "ul" "attrs" (dict)'
        + ' "children" (map-list (lambda (i)'
        + ' (dict "tag" "li"'
        + ' "attrs" (dict "class" "item" "data-id" (integer->string i))'
        + ' "children" (list (integer->string i))))'
        + f' (range 0 {count})))))))'
    )


def _deep(depth: int) -> str:
    """Encode a tree nested `depth` levels deep."""
    builder = '(dict "tag" "div" "attrs" (dict) "children" (list "x"))'
    for _ in range(depth):
        builder = f'(dict "tag" "div" "attrs" (dict) "children" (list {builder}))'

    return _IMPORT + f"(encode (list {builder}))))"


def _attributes(count: int) -> str:
    """Encode `count` elements, each carrying four attributes."""
    return (
        _IMPORT
        + '(encode (list (dict "tag" "table" "attrs" (dict)'
        + ' "children" (map-list (lambda (i)'
        + ' (dict "tag" "tr"'
        + ' "attrs" (dict "a" (integer->string i) "b" "x" "c" "y" "d" "z")'
        + ' "children" (list)))'
        + f' (range 0 {count})))))))'
    )


def _mixed(count: int) -> str:
    """Encode `count` paragraphs, each with mixed text and child elements."""
    return (
        _IMPORT
        + '(encode (list (dict "tag" "div" "attrs" (dict)'
        + ' "children" (map-list (lambda (i)'
        + ' (dict "tag" "p" "attrs" (dict)'
        + ' "children" (list "Text " (integer->string i) " with "'
        + ' (dict "tag" "b" "attrs" (dict) "children" (list "bold"))'
        + ' " and "'
        + ' (dict "tag" "i" "attrs" (dict) "children" (list "italic"))'
        + ' " runs.")))'
        + f' (range 0 {count})))))))'
    )


def _void_elements(count: int) -> str:
    """Encode `count` paragraphs, each containing void elements with no children."""
    return (
        _IMPORT
        + '(encode (list (dict "tag" "form" "attrs" (dict)'
        + ' "children" (map-list (lambda (i)'
        + ' (dict "tag" "p" "attrs" (dict)'
        + ' "children" (list "line " (integer->string i)'
        + ' (dict "tag" "br" "attrs" (dict) "children" (list))'
        + ' (dict "tag" "img" "attrs" (dict "src" "a.png" "alt" "x") "children" (list))'
        + ' (dict "tag" "input" "attrs" (dict "type" "text" "value" "v") "children" (list)))))'
        + f' (range 0 {count})))))))'
    )


def _escaping(count: int) -> str:
    """Encode `count` elements whose text and attributes need escaping."""
    return (
        _IMPORT
        + '(encode (list (dict "tag" "div" "attrs" (dict)'
        + ' "children" (map-list (lambda (i)'
        + ' (dict "tag" "p"'
        + ' "attrs" (dict "v" "a&b<c>d\\"e")'
        + ' "children" (list "a & b < c > d")))'
        + f' (range 0 {count})))))))'
    )


def _declarations(count: int) -> str:
    """Encode a document whose top level carries a doctype and many comments."""
    comments = (
        '(map-list (lambda (i)'
        ' (dict "tag" "!--" "attrs" (dict) "children" (list " comment " (integer->string i) " ")))'
        f' (range 0 {count}))'
    )
    return (
        _IMPORT
        + '(encode (list-concat'
        + ' (list (dict "tag" "!doctype" "attrs" (dict) "children" (list "html")))'
        + f" {comments}"
        + ' (list (dict "tag" "html" "attrs" (dict) "children" (list)))))))'
    )


_CASES: list[tuple[str, str]] = [
    ("wide-1000", _wide(1000)),
    ("deep-200", _deep(200)),
    ("attributes-500", _attributes(500)),
    ("mixed-500", _mixed(500)),
    ("void-1000", _void_elements(1000)),
    ("escaping-2000", _escaping(2000)),
    ("declarations-1000", _declarations(1000)),
]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai HTML encoder."""

    name = "html-encode"
    description = "Serialise HTML value trees of varying shape to text."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per tree shape."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name, _ in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the encode expression for each case's tree."""
        expressions = dict(_CASES)

        def expression(case_input: str) -> str:
            return expressions[case_input]

        return MenaiProgram(expression=expression)
