from __future__ import annotations

from collections.abc import Callable

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SLIDE_REL = f"{_R}/slide"
_IMAGE_REL = f"{_R}/image"


def _element(tag: str, attrs: str, children: str) -> str:
    """Return a Menai expression for an element with the given tag, attrs, and children."""
    return f'(dict "tag" "{tag}" "attrs" {attrs} "children" {children})'


def _shape(text: str) -> str:
    """Return a Menai expression for a shape containing a single text run."""
    text_element = _element("a:t", "(dict)", f'(list "{text}")')
    run = _element("a:r", "(dict)", f"(list {text_element})")
    paragraph = _element("a:p", "(dict)", f"(list {run})")
    return _element("p:sp", "(dict)", f"(list {paragraph})")


def _slide(shapes: int) -> str:
    """Return a Menai expression for a ppt/slides/slideN.xml element tree."""
    body = " ".join(_shape(f"Shape {i}") for i in range(shapes))
    sp_tree = _element("p:spTree", "(dict)", f"(list {body})")
    c_sld = _element("p:cSld", "(dict)", f"(list {sp_tree})")
    return _element("p:sld", f'(dict "xmlns:p" "{_P}")', f"(list {c_sld})")


def _presentation(slides: int) -> str:
    """Return a Menai expression for a ppt/presentation.xml element tree."""
    sld_ids = " ".join(
        _element("p:sldId", f'(dict "id" "{256 + i}" "r:id" "rId{100 + i}")', "(list)")
        for i in range(slides)
    )
    sld_id_lst = _element("p:sldIdLst", "(dict)", f"(list {sld_ids})")
    return _element("p:presentation", f'(dict "xmlns:p" "{_P}" "xmlns:r" "{_R}")', f"(list {sld_id_lst})")


def _slide_master() -> str:
    """Return a Menai expression for a ppt/slideMasters/slideMaster1.xml element tree."""
    return _element("p:sldMaster", f'(dict "xmlns:p" "{_P}")', "(list)")


def _slide_layout() -> str:
    """Return a Menai expression for a ppt/slideLayouts/slideLayout1.xml element tree."""
    return _element("p:sldLayout", f'(dict "xmlns:p" "{_P}")', "(list)")


def _theme() -> str:
    """Return a Menai expression for a ppt/theme/theme1.xml element tree."""
    return _element("a:theme", f'(dict "xmlns:a" "{_P}")', "(list)")


def _media(slides: int, images_per_slide: int, size: int) -> str:
    """Return a Menai expression for a media dict of byte parts."""
    hex_bytes = "89504e470d0a1a0a" + "00" * size
    entries = " ".join(
        f'"ppt/media/image{i}.png" (string->bytes "{hex_bytes}")'
        for i in range(slides * images_per_slide)
    )
    return f"(dict {entries})"


def _slide_relationships(images: int) -> str:
    """Return a Menai expression for a slide's relationships dict."""
    entries = " ".join(
        f'"rId{i + 1}" (dict "type" "{_IMAGE_REL}" "target" "../media/image{i}.png" "mode" #none)'
        for i in range(images)
    )
    return f"(dict {entries})"


def _slide_entries(slides: int, shapes: int, images_per_slide: int) -> str:
    """Return a Menai expression for the "slides" list."""
    entries = " ".join(
        f'(dict "part" "ppt/slides/slide{i + 1}.xml" '
        f'"element" {_slide(shapes)} '
        f'"relationships" {_slide_relationships(images_per_slide) if images_per_slide else "(dict)"})'
        for i in range(slides)
    )
    return f"(list {entries})"


def _presentation_relationships(slides: int) -> str:
    """Return a Menai expression for the presentation's relationships dict."""
    entries = " ".join(
        f'"rId{100 + i}" (dict "type" "{_SLIDE_REL}" "target" "slides/slide{i + 1}.xml" "mode" #none)'
        for i in range(slides)
    )
    return f"(dict {entries})"


def _element_list(count: int, factory: Callable[[], str]) -> str:
    """Return a Menai expression for a list of `count` elements from `factory`."""
    return f"(list {' '.join(factory() for _ in range(count))})"


def _program(
    slides: int,
    shapes: int = 1,
    images_per_slide: int = 0,
    image_size: int = 0,
    masters: int = 0,
    layouts: int = 0,
    theme: bool = False,
) -> str:
    """Return a Menai expression encoding a PPTX value tree of the given shape."""
    theme_expr = _theme() if theme else "#none"
    media_expr = _media(slides, images_per_slide, image_size) if images_per_slide else "#none"
    tree = (
        f'(dict "presentation" {_presentation(slides)} '
        f'"slides" {_slide_entries(slides, shapes, images_per_slide)} '
        f'"slide-masters" {_element_list(masters, _slide_master)} '
        f'"slide-layouts" {_element_list(layouts, _slide_layout)} '
        f'"theme" {theme_expr} '
        f'"relationships" {_presentation_relationships(slides)} '
        f'"media" {media_expr})'
    )
    return f'(let ((pptx (import "pptx-encode"))) ((:: pptx encode) {tree}))'


_FIXTURES: dict[str, str] = {
    "minimal": _program(1),
    "slides-10": _program(10),
    "slides-50": _program(50),
    "shapes-50": _program(10, shapes=50),
    "masters-8": _program(10, masters=8, layouts=16),
    "media-16": _program(8, images_per_slide=2, image_size=2048),
    "theme-and-masters": _program(10, masters=4, layouts=8, theme=True),
    "full-50": _program(50, shapes=20, images_per_slide=2, image_size=4096, masters=4, layouts=8, theme=True),
}


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai PPTX encoder."""

    name = "pptx-encode"
    description = "Encode PPTX value trees, serialising the parts and building the package."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture."""
        return [
            BenchmarkCase(name=name, input=name, iterations=_ITERATIONS)
            for name in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the encode expression for the case's value tree."""
        return MenaiProgram(expression=lambda name: _FIXTURES[name])
