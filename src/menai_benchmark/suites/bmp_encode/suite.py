from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

# Each case encodes a decoded BMP description.  The geometries mirror the
# bmp_decode suite so the two directions can be compared.  The container is
# built as a Menai expression rather than parsed from bytes, so the timing
# measures encoding alone.
#
# Two geometries are deliberate rather than illustrative.  `1024x16` and
# `16x1024` hold the same number of pixels as each other and as `128x128`, so
# they are a canary for an encoder whose cost depends on the shape rather than
# the pixel count.  They differ only in how many rows the pixels are divided
# between, which is what the per-row overhead scales with: `16x1024` is the most
# expensive of the three and `1024x16` the least, and a change that reverses or
# widens that spread is a change to the per-row path.  `256x256` covers the
# direction in which the cost that grows is the image rather than the row.
#
# Four times the pixels must cost about four times the time: compare `256x256`
# with `128x128`, which is what catches work that is quadratic in the image.
# While append-to-output crossed a call boundary they cost 14x, not 4x.

_ITERATIONS = 5

_PIXELS_PER_METRE = 2835

# Fixture geometry: (name, width, height, bits-per-pixel, bottom-up row order).
_FIXTURES: list[tuple[str, int, int, int, bool]] = [
    ("truecolour-64x64", 64, 64, 24, False),
    ("truecolour-128x128", 128, 128, 24, False),
    ("truecolour-1024x16", 1024, 16, 24, False),
    ("truecolour-16x1024", 16, 1024, 24, False),
    ("truecolour-256x256", 256, 256, 24, False),
    ("truecolour-topdown-128x128", 128, 128, 24, True),
    ("truecolour-alpha-128x128", 128, 128, 32, False),
    ("padded-65x64", 65, 64, 24, False),
]


def _pixel_expr(bpp: int, x: int, y: int) -> str:
    """Return a Menai vector expression for one pixel, in RGB/RGBA order."""
    if bpp == 24:
        return f"(vector {(x * 7) % 256} {(y * 11) % 256} {(x + y) * 13 % 256})"

    return f"(vector {(x * 7) % 256} {(y * 11) % 256} {(x + y) * 13 % 256} {(x * 5) % 256})"


def _row_expr(bpp: int, width: int, y: int) -> str:
    """Return a Menai vector expression for one row of pixels."""
    return "(vector " + " ".join(_pixel_expr(bpp, x, y) for x in range(width)) + ")"


def _container_expr(width: int, height: int, bpp: int, bottom_up: bool) -> str:
    """Return a Menai expression constructing the decoded BMP dict."""
    rows = " ".join(_row_expr(bpp, width, y) for y in range(height))
    header = (
        f'(dict "width" {width} "height" {height} "bpp" {bpp} "planes" 1'
        f' "x-ppm" {_PIXELS_PER_METRE} "y-ppm" {_PIXELS_PER_METRE}'
        f' "colours-used" 0 "colours-important" 0)'
    )
    meta = f'(dict "format" "bmp" "bottom-up" {"#t" if bottom_up else "#f"})'
    return f'(dict "header" {header} "pixels" (vector {rows}) "meta" {meta})'


_FIXTURE_CONTAINERS: list[tuple[str, str]] = [
    (name, _container_expr(width, height, bpp, bottom_up))
    for name, width, height, bpp, bottom_up in _FIXTURES
]


def _to_menai_expr(container_expr: str) -> str:
    """Wrap a container expression in an encode call."""
    return f'(let ((bmp (import "bmp-encode"))) ((:: bmp encode) {container_expr}))'


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai BMP encoder."""

    name = "bmp-encode"
    description = "Encode decoded 24-bit and 32-bit BMP descriptions of various orientations."

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
        """Return the BMP encode expression, built from the case's container."""
        return MenaiProgram(expression=_to_menai_expr)
