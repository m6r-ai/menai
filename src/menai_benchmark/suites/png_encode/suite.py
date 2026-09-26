from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

# Each case encodes a decoded PNG description.  The encoder emits truecolour
# (type 2) or truecolour with alpha (type 6), so the cases use the RGB and RGBA
# geometries from the png_decode suite, where the two directions are comparable.
# The container is built as a Menai expression rather than parsed from bytes, so
# the timing measures encoding alone.
#
# The largest case is 96x96.  Encoding dominates the cost only at small sizes;
# beyond this the DEFLATE compressor (which writes one bit per call) accounts
# for almost all of the time, so larger images would mostly measure compression.

_ITERATIONS = 2

# Fixture geometry: (name, width, height, channels).
_FIXTURES: list[tuple[str, int, int, int]] = [
    ("truecolour-48x48", 48, 48, 3),
    ("truecolour-96x96", 96, 96, 3),
    ("truecolour-alpha-96x96", 96, 96, 4),
]


def _pixel_expr(channels: int, x: int, y: int) -> str:
    """Return a Menai vector expression for one pixel, in RGB or RGBA order."""
    if channels == 3:
        return f"(vector {(x * 7) % 256} {(y * 11) % 256} {(x + y) * 13 % 256})"

    return f"(vector {(x * 7) % 256} {(y * 11) % 256} {(x + y) * 13 % 256} {(x * 5) % 256})"


def _row_expr(channels: int, width: int, y: int) -> str:
    """Return a Menai vector expression for one row of pixels."""
    return "(vector " + " ".join(_pixel_expr(channels, x, y) for x in range(width)) + ")"


def _container_expr(width: int, height: int, channels: int) -> str:
    """Return a Menai expression constructing the decoded PNG dict."""
    rows = " ".join(_row_expr(channels, width, y) for y in range(height))
    header = f'(dict "width" {width} "height" {height} "bit-depth" 8)'
    meta = f'(dict "format" "png" "channels" {channels})'
    return f'(dict "header" {header} "pixels" (vector {rows}) "meta" {meta})'


_FIXTURE_CONTAINERS: list[tuple[str, str]] = [
    (name, _container_expr(width, height, channels))
    for name, width, height, channels in _FIXTURES
]


def _to_menai_expr(container_expr: str) -> str:
    """Wrap a container expression in an encode call."""
    return f'(let ((png (import "png-encode"))) ((:: png encode) {container_expr}))'


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai PNG encoder."""

    name = "png-encode"
    description = "Encode decoded truecolour and truecolour-with-alpha PNG descriptions."

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
        """Return the PNG encode expression, built from the case's container."""
        return MenaiProgram(expression=_to_menai_expr)
