from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

# Each case encodes a decoded JPEG description.  JPEG is lossy, so unlike the PNG
# and BMP pairs the pixels here are not read back from the decode suite's fixtures:
# the cases use the same geometries as the jpeg_decode suite (the two 128x128 and
# 64x64 sizes, plus a size whose blocks are partial at the right and bottom edges),
# so the two directions are comparable in size and block count.  The container is
# built as a Menai expression rather than parsed from bytes, so the timing measures
# encoding alone.
#
# Sizes stop at 128x128.  The encoder always emits 4:4:4, so its cost grows with
# the pixel count in all three components and a larger image would only lengthen
# the run without adding a code path.

_ITERATIONS = 2

# Fixture geometry: (name, width, height).
_FIXTURES: list[tuple[str, int, int]] = [
    ("colour-64x64", 64, 64),
    ("colour-128x128", 128, 128),
    ("colour-97x61", 97, 61),
]


def _pixel_expr(x: int, y: int) -> str:
    """Return a Menai vector expression for one RGB pixel."""
    return f"(vector {(x * 7) % 256} {(y * 11) % 256} {(x + y) * 13 % 256})"


def _row_expr(width: int, y: int) -> str:
    """Return a Menai vector expression for one row of pixels."""
    return "(vector " + " ".join(_pixel_expr(x, y) for x in range(width)) + ")"


def _container_expr(width: int, height: int) -> str:
    """Return a Menai expression constructing the decoded JPEG dict."""
    rows = " ".join(_row_expr(width, y) for y in range(height))
    header = f'(dict "width" {width} "height" {height} "precision" 8)'
    meta = '(dict "format" "jpeg" "channels" 3)'
    return f'(dict "header" {header} "pixels" (vector {rows}) "meta" {meta})'


_FIXTURE_CONTAINERS: list[tuple[str, str]] = [
    (name, _container_expr(width, height))
    for name, width, height in _FIXTURES
]


def _to_menai_expr(container_expr: str) -> str:
    """Wrap a container expression in an encode call."""
    return f'(let ((jpeg (import "jpeg-encode"))) ((:: jpeg encode) {container_expr}))'


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai JPEG encoder."""

    name = "jpeg-encode"
    description = "Encode decoded JPEG descriptions as baseline sequential JPEG files."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per container to encode."""
        return [
            BenchmarkCase(
                name=name,
                input=container_expr,
                iterations=_ITERATIONS,
            )
            for name, container_expr in _FIXTURE_CONTAINERS
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the JPEG encode expression, built from the case's container."""
        return MenaiProgram(expression=_to_menai_expr)
