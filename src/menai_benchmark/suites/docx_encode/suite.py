from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _element(tag: str, attrs: str, children: str) -> str:
    """Return a Menai expression for an element with the given tag, attrs, and children."""
    return f'(dict "tag" "{tag}" "attrs" {attrs} "children" {children})'


def _text_run(text: str) -> str:
    """Return a Menai expression for a run containing a single text node."""
    text_element = _element("w:t", "(dict)", f'(list "{text}")')
    return _element("w:r", "(dict)", f"(list {text_element})")


def _paragraph(text: str) -> str:
    """Return a Menai expression for a paragraph containing a single run."""
    return _element("w:p", "(dict)", f"(list {_text_run(text)})")


def _document(paragraphs: int) -> str:
    """Return a Menai expression for a word/document.xml element tree."""
    body_children = " ".join(_paragraph(f"Paragraph {i}") for i in range(paragraphs))
    body = _element("w:body", "(dict)", f"(list {body_children})")
    return _element("w:document", f'(dict "xmlns:w" "{_W}")', f"(list {body})")


def _styles(count: int) -> str:
    """Return a Menai expression for a word/styles.xml element tree."""
    parts = []
    for i in range(count):
        name = _element("w:name", f'(dict "w:val" "Style {i}")', "(list)")
        parts.append(_element("w:style", f'(dict "w:type" "paragraph" "w:styleId" "Style{i}")', f"(list {name})"))

    return _element("w:styles", f'(dict "xmlns:w" "{_W}")', f"(list {' '.join(parts)})")


def _numbering(levels: int) -> str:
    """Return a Menai expression for a word/numbering.xml element tree."""
    parts = []
    for i in range(levels):
        fmt = _element("w:numFmt", '(dict "w:val" "bullet")', "(list)")
        parts.append(_element("w:lvl", f'(dict "w:ilvl" "{i}")', f"(list {fmt})"))

    return _element("w:numbering", f'(dict "xmlns:w" "{_W}")', f"(list {' '.join(parts)})")


def _media(images: int, size: int) -> str:
    """Return a Menai expression for a media dict of `images` byte parts."""
    hex_bytes = "89504e470d0a1a0a" + "00" * size
    entries = " ".join(
        f'"word/media/image{i}.png" (string-hex->bytes "{hex_bytes}")'
        for i in range(images)
    )
    return f"(dict {entries})"


def _relationships(images: int) -> str:
    """Return a Menai expression for a relationships dict of `images` image entries."""
    entries = " ".join(
        f'"rId{i + 1}" (dict "type" "{_IMAGE_REL}" "target" "media/image{i}.png" "mode" #none)'
        for i in range(images)
    )
    return f"(dict {entries})"


def _program(paragraphs: int, styles: int = 0, numbering_levels: int = 0, images: int = 0, image_size: int = 0) -> str:
    """Return a Menai expression encoding a DOCX value tree of the given shape."""
    document = _document(paragraphs)
    styles_expr = _styles(styles) if styles else "#none"
    numbering_expr = _numbering(numbering_levels) if numbering_levels else "#none"
    media_expr = _media(images, image_size) if images else "#none"
    relationships = _relationships(images) if images else "(dict)"
    tree = (
        f'(dict "document" {document} "styles" {styles_expr} '
        f'"numbering" {numbering_expr} "relationships" {relationships} "media" {media_expr})'
    )
    return f'(let ((docx (import "docx-encode"))) ((:: docx encode) {tree}))'


_FIXTURES: dict[str, str] = {
    "minimal": _program(1),
    "document-100": _program(100),
    "document-500": _program(500),
    "styles-100": _program(20, styles=100),
    "numbering-9": _program(20, numbering_levels=9),
    "media-16": _program(20, images=16, image_size=2048),
    "full-500": _program(500, styles=50, numbering_levels=9, images=8, image_size=4096),
}


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai DOCX encoder."""

    name = "docx-encode"
    description = "Encode DOCX value trees, serialising the parts and building the package."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the encode expression for the case's value tree."""
        return MenaiProgram(expression=lambda name: _FIXTURES[name])
