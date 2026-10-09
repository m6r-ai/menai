from __future__ import annotations

import io
import zipfile

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((docx (import "docx-decode"))) ((:: docx decode) (dict-get inputs "input-data")))'

_FIXED_DATE_TIME = (2024, 1, 1, 0, 0, 0)

_DEFLATED = zipfile.ZIP_DEFLATED

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

_cache: dict[str, bytes] = {}


def _document_xml(paragraphs: int) -> str:
    """Return a word/document.xml with `paragraphs` styled paragraphs."""
    body = "".join(
        "<w:p><w:pPr><w:pStyle w:val=\"Normal\"/></w:pPr>"
        f"<w:r><w:rPr><w:b/></w:rPr>"
        f"<w:t xml:space=\"preserve\">Paragraph {i} of the benchmark document.</w:t></w:r>"
        "</w:p>"
        for i in range(paragraphs)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<w:document xmlns:w=\"{_W}\">"
        f"<w:body>{body}</w:body></w:document>"
    )


def _styles_xml(styles: int) -> str:
    """Return a word/styles.xml declaring `styles` paragraph styles."""
    entries = "".join(
        f"<w:style w:type=\"paragraph\" w:styleId=\"Style{i}\">"
        f"<w:name w:val=\"Style {i}\"/><w:rPr><w:sz w:val=\"22\"/></w:rPr></w:style>"
        for i in range(styles)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<w:styles xmlns:w=\"{_W}\">{entries}</w:styles>"
    )


def _numbering_xml(levels: int) -> str:
    """Return a word/numbering.xml with one abstract definition of `levels` levels."""
    lvls = "".join(
        f"<w:lvl w:ilvl=\"{i}\"><w:start w:val=\"1\"/><w:numFmt w:val=\"bullet\"/>"
        f"<w:lvlText w:val=\"\u2022\"/></w:lvl>"
        for i in range(levels)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<w:numbering xmlns:w=\"{_W}\">"
        f"<w:abstractNum w:abstractNumId=\"0\">{lvls}</w:abstractNum>"
        "<w:num w:numId=\"1\"><w:abstractNumId w:val=\"0\"/></w:num></w:numbering>"
    )


def _document_rels_xml(images: int) -> str:
    """Return a word/_rels/document.xml.rels declaring `images` image relationships."""
    entries = "".join(
        f"<Relationship Id=\"rId{i + 1}\" Type=\"{_IMAGE_REL}\" Target=\"media/image{i}.png\"/>"
        for i in range(images)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<Relationships xmlns=\"{_RELS}\">{entries}</Relationships>"
    )


def _png(size: int) -> bytes:
    """Return `size` bytes standing in for a PNG image."""
    return b"\x89PNG\r\n\x1a\n" + bytes(size)


def _make_docx(
    paragraphs: int,
    styles: int = 0,
    numbering_levels: int = 0,
    images: int = 0,
    image_size: int = 0,
) -> bytes:
    """Build a DOCX package with the given document size and optional parts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        def write(name: str, content: str | bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = _DEFLATED
            archive.writestr(info, content)

        write("[Content_Types].xml", "<Types/>")
        write("_rels/.rels", "<Relationships/>")
        write("word/document.xml", _document_xml(paragraphs))

        if styles:
            write("word/styles.xml", _styles_xml(styles))

        if numbering_levels:
            write("word/numbering.xml", _numbering_xml(numbering_levels))

        if images:
            write("word/_rels/document.xml.rels", _document_rels_xml(images))
            for i in range(images):
                write(f"word/media/image{i}.png", _png(image_size))

    return buffer.getvalue()


_FIXTURES: dict[str, bytes] = {
    "minimal": _make_docx(1),
    "document-100": _make_docx(100),
    "document-500": _make_docx(500),
    "document-2000": _make_docx(2000),
    "styles-100": _make_docx(50, styles=100),
    "numbering-9": _make_docx(50, numbering_levels=9),
    "media-16": _make_docx(50, images=16, image_size=4096),
    "full-500": _make_docx(500, styles=50, numbering_levels=9, images=8, image_size=8192),
}


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture DOCX bytes for a name."""
    return _FIXTURES[name]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai DOCX decoder."""

    name = "docx-decode"
    description = "Decode DOCX packages, decompressing the parts and parsing their XML."

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
