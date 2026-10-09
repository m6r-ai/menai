from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((xml (import "xml-decode"))) ((:: xml decode) (bytes->string (dict-get inputs "input-data"))))'

_cache: dict[str, bytes] = {}


def _wide(count: int) -> str:
    """Return a document with one root and `count` sibling elements."""
    items = "".join(f"<item id=\"{i}\">{i}</item>" for i in range(count))
    return f"<root>{items}</root>"


def _deep(depth: int) -> str:
    """Return a document nested `depth` levels deep."""
    return "<a>" * depth + "x" + "</a>" * depth


def _attributes(count: int) -> str:
    """Return a document with `count` elements, each carrying four attributes."""
    items = "".join(
        f"<row a=\"{i}\" b=\"x{i}\" c=\"y{i}\" d=\"z{i}\"/>"
        for i in range(count)
    )
    return f"<table>{items}</table>"


def _mixed(count: int) -> str:
    """Return a document with mixed text and child elements in each paragraph."""
    paras = "".join(
        f"<p>Text {i} with <b>bold</b> and <i>italic</i> runs.</p>"
        for i in range(count)
    )
    return f"<doc>{paras}</doc>"


def _entities(count: int) -> str:
    """Return a document whose text carries the predefined entities."""
    items = "".join(
        "<t>a &amp; b &lt; c &gt; d &#38; e</t>"
        for _ in range(count)
    )
    return f"<doc>{items}</doc>"


def _ooxml_like(paragraphs: int) -> str:
    """Return a WordprocessingML-like part, as an Office document would contain."""
    body = "".join(
        "<w:p><w:pPr><w:pStyle w:val=\"Normal\"/></w:pPr>"
        f"<w:r><w:rPr><w:b/></w:rPr><w:t xml:space=\"preserve\">Paragraph {i}</w:t></w:r>"
        "</w:p>"
        for i in range(paragraphs)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{body}</w:body></w:document>"
    )


_FIXTURES: dict[str, str] = {
    "wide-1000": _wide(1000),
    "deep-200": _deep(200),
    "attributes-500": _attributes(500),
    "mixed-500": _mixed(500),
    "entities-2000": _entities(2000),
    "ooxml-500": _ooxml_like(500),
}


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture XML for a name as UTF-8 bytes, generating it on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    data = _FIXTURES[name].encode("utf-8")
    _cache[name] = data
    return data


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai XML decoder."""

    name = "xml-decode"
    description = "Parse XML documents of varying shape into a value tree."

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
