from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((html (import "html-decode"))) ((:: html decode) (bytes->string (dict-get inputs "input-data"))))'

_cache: dict[str, bytes] = {}


def _wide(count: int) -> str:
    """Return a document with one root and `count` sibling elements."""
    items = "".join(f'<li class="item" data-id="{i}">{i}</li>' for i in range(count))
    return f"<ul>{items}</ul>"


def _deep(depth: int) -> str:
    """Return a document nested `depth` levels deep."""
    return "<div>" * depth + "x" + "</div>" * depth


def _attributes(count: int) -> str:
    """Return a document with `count` elements, each carrying four attributes."""
    items = "".join(
        f'<tr a="{i}" b="x{i}" c="y{i}" d="z{i}"><td>v</td></tr>'
        for i in range(count)
    )
    return f"<table>{items}</table>"


def _mixed(count: int) -> str:
    """Return a document with mixed text and child elements in each paragraph."""
    paras = "".join(
        f"<p>Text {i} with <b>bold</b> and <i>italic</i> runs.</p>"
        for i in range(count)
    )
    return f"<div>{paras}</div>"


def _entities(count: int) -> str:
    """Return a document whose text carries named, decimal, and hex references."""
    items = "".join(
        "<p>a &amp; b &lt; c &nbsp; d &#38; e &#x26; f &copy; g</p>"
        for _ in range(count)
    )
    return f"<div>{items}</div>"


def _void_elements(count: int) -> str:
    """Return a document dense with void elements that carry no end tag."""
    items = "".join(
        f'<p>line {i}<br><img src="a.png" alt="x"><input type="text" value="v"></p>'
        for i in range(count)
    )
    return f"<form>{items}</form>"


def _omitted_end_tags(count: int) -> str:
    """Return a document relying on omitted end tags, which the decoder recovers."""
    items = "".join(f"<li>item {i}" for i in range(count))
    return f"<ul>{items}</ul>"


def _raw_text(count: int) -> str:
    """Return a document whose script and style content is raw text."""
    scripts = "".join(
        f"<script>if (a < b && c > d) {{ f({i}); }}</script>"
        "<style>a > b { color: red; }</style>"
        for i in range(count)
    )
    return f"<head>{scripts}</head>"


def _realistic(count: int) -> str:
    """Return a page shaped like a real one: doctype, head, comments, and a body."""
    rows = "".join(
        f'<tr><td>{i}</td><td><a href="/item/{i}">Item {i}</a></td>'
        f'<td><span class="tag">t{i}</span></td></tr>'
        for i in range(count)
    )
    return (
        '<!DOCTYPE html>'
        '<html lang="en">'
        '<head><meta charset="utf-8"><title>Report</title>'
        '<style>td { padding: 2px; }</style></head>'
        '<body>'
        '<!-- generated report -->'
        '<h1>Report &amp; summary</h1>'
        '<p>Generated <b>automatically</b>; see <a href="/help">help</a>.</p>'
        f'<table>{rows}</table>'
        "<script>window.addEventListener('load', function () { init(); });</script>"
        '</body></html>'
    )


_FIXTURES: dict[str, str] = {
    "wide-1000": _wide(1000),
    "deep-200": _deep(200),
    "attributes-500": _attributes(500),
    "mixed-500": _mixed(500),
    "entities-2000": _entities(2000),
    "void-1000": _void_elements(1000),
    "omitted-end-tags-1000": _omitted_end_tags(1000),
    "raw-text-500": _raw_text(500),
    "realistic-500": _realistic(500),
}


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture HTML for a name as UTF-8 bytes, generating it on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    data = _FIXTURES[name].encode("utf-8")
    _cache[name] = data
    return data


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai HTML decoder."""

    name = "html-decode"
    description = "Parse HTML documents of varying shape into a value tree."

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
