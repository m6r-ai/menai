from __future__ import annotations

import io
import zipfile

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((pptx (import "pptx-decode"))) ((:: pptx decode) (dict-get inputs "input-data")))'

_FIXED_DATE_TIME = (2024, 1, 1, 0, 0, 0)

_DEFLATED = zipfile.ZIP_DEFLATED

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_REL = f"{_R}/slide"
_IMAGE_REL = f"{_R}/image"


def _slide_xml(shapes: int) -> str:
    """Return a ppt/slides/slideN.xml with `shapes` text shapes."""
    body = "".join(
        "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Shape\"/></p:nvSpPr>"
        "<p:txBody><a:bodyPr/><a:p><a:r>"
        f"<a:t>Shape {i} of the benchmark slide.</a:t>"
        "</a:r></a:p></p:txBody></p:sp>"
        for i in range(shapes)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<p:sld xmlns:p=\"{_P}\" xmlns:a=\"{_P}\">"
        f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
    )


def _slide_rels_xml(images: int) -> str:
    """Return a ppt/slides/_rels/slideN.xml.rels declaring `images` image relationships."""
    entries = "".join(
        f"<Relationship Id=\"rId{i + 1}\" Type=\"{_IMAGE_REL}\" Target=\"../media/image{i}.png\"/>"
        for i in range(images)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<Relationships xmlns=\"{_RELS}\">{entries}</Relationships>"
    )


def _presentation_xml(slides: int) -> str:
    """Return a ppt/presentation.xml whose slide list references `slides` slides."""
    sld_ids = "".join(
        f"<p:sldId id=\"{256 + i}\" r:id=\"rId{i + 1}\"/>"
        for i in range(slides)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<p:presentation xmlns:p=\"{_P}\" xmlns:r=\"{_R}\">"
        f"<p:sldIdLst>{sld_ids}</p:sldIdLst></p:presentation>"
    )


def _presentation_rels_xml(slides: int) -> str:
    """Return a ppt/_rels/presentation.xml.rels resolving the slide list."""
    entries = "".join(
        f"<Relationship Id=\"rId{i + 1}\" Type=\"{_SLIDE_REL}\" Target=\"slides/slide{i + 1}.xml\"/>"
        for i in range(slides)
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<Relationships xmlns=\"{_RELS}\">{entries}</Relationships>"
    )


def _slide_master_xml() -> str:
    """Return a ppt/slideMasters/slideMaster1.xml."""
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<p:sldMaster xmlns:p=\"{_P}\"><p:cSld><p:spTree/></p:cSld></p:sldMaster>"
    )


def _slide_layout_xml() -> str:
    """Return a ppt/slideLayouts/slideLayout1.xml."""
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<p:sldLayout xmlns:p=\"{_P}\"><p:cSld><p:spTree/></p:cSld></p:sldLayout>"
    )


def _theme_xml() -> str:
    """Return a ppt/theme/theme1.xml."""
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        f"<a:theme xmlns:a=\"{_P}\" name=\"Benchmark\"><a:themeElements/></a:theme>"
    )


def _png(size: int) -> bytes:
    """Return `size` bytes standing in for a PNG image."""
    return b"\x89PNG\r\n\x1a\n" + bytes(size)


def _make_pptx(
    slides: int,
    shapes: int = 1,
    slide_images: int = 0,
    image_size: int = 0,
    masters: int = 0,
    layouts: int = 0,
    theme: bool = False,
) -> bytes:
    """Build a PPTX package with the given slide count and optional parts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        def write(name: str, content: str | bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = _DEFLATED
            archive.writestr(info, content)

        write("[Content_Types].xml", "<Types/>")
        write("_rels/.rels", "<Relationships/>")
        write("ppt/presentation.xml", _presentation_xml(slides))
        write("ppt/_rels/presentation.xml.rels", _presentation_rels_xml(slides))

        for i in range(slides):
            write(f"ppt/slides/slide{i + 1}.xml", _slide_xml(shapes))
            if slide_images:
                write(f"ppt/slides/_rels/slide{i + 1}.xml.rels", _slide_rels_xml(slide_images))

        for i in range(masters):
            write(f"ppt/slideMasters/slideMaster{i + 1}.xml", _slide_master_xml())

        for i in range(layouts):
            write(f"ppt/slideLayouts/slideLayout{i + 1}.xml", _slide_layout_xml())

        if theme:
            write("ppt/theme/theme1.xml", _theme_xml())

        for i in range(slides * slide_images):
            write(f"ppt/media/image{i}.png", _png(image_size))

    return buffer.getvalue()


_FIXTURES: dict[str, bytes] = {
    "minimal": _make_pptx(1),
    "slides-10": _make_pptx(10),
    "slides-50": _make_pptx(50),
    "slides-200": _make_pptx(200),
    "shapes-50": _make_pptx(10, shapes=50),
    "masters-8": _make_pptx(10, masters=8, layouts=16),
    "media-16": _make_pptx(8, slide_images=2, image_size=4096),
    "theme-and-masters": _make_pptx(10, masters=4, layouts=8, theme=True),
    "full-50": _make_pptx(50, shapes=20, slide_images=2, image_size=8192, masters=4, layouts=8, theme=True),
}


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture PPTX bytes for a name."""
    return _FIXTURES[name]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai PPTX decoder."""

    name = "pptx-decode"
    description = "Decode PPTX packages, resolving the slide list and parsing the parts' XML."

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
