from __future__ import annotations

import struct

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_EXPR = '(let ((jpeg (import "jpeg-decode"))) ((:: jpeg decode) (dict-get inputs "input-data")))'

# The fixtures are baseline sequential files written by the small entropy-coder at
# the end of this module, from the coefficient pattern each case declares.  A block
# is written straight from its coefficients, so the fixtures need no forward
# transform and are a pure function of fixed constants: no binary files are
# committed and every run produces byte-identical input.
#
# The quantisation table is flat (every step is 1), which is what makes a case's
# coefficient pattern legible as decoded sample values.  It does not affect the
# decode cost: the inverse transform reads all 64 coefficients of every block
# whatever they are.

# The natural (row-major) coefficient index of each zigzag position.
_ZIGZAG: list[int] = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

_DC_LUMINANCE_BITS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
_DC_LUMINANCE_VALUES = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_DC_CHROMINANCE_BITS = (0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0)
_DC_CHROMINANCE_VALUES = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)

_AC_LUMINANCE_BITS = (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 125)

_AC_LUMINANCE_VALUES = (
    1, 2, 3, 0, 4, 17, 5, 18, 33, 49, 65, 6, 19, 81, 97, 7, 34, 113, 20, 50, 129, 145, 161,
    8, 35, 66, 177, 193, 21, 82, 209, 240, 36, 51, 98, 114, 130, 9, 10, 22, 23, 24, 25, 26,
    37, 38, 39, 40, 41, 42, 52, 53, 54, 55, 56, 57, 58, 67, 68, 69, 70, 71, 72, 73, 74, 83,
    84, 85, 86, 87, 88, 89, 90, 99, 100, 101, 102, 103, 104, 105, 106, 115, 116, 117, 118,
    119, 120, 121, 122, 131, 132, 133, 134, 135, 136, 137, 138, 146, 147, 148, 149, 150, 151,
    152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169, 170, 178, 179, 180, 181, 182, 183,
    184, 185, 186, 194, 195, 196, 197, 198, 199, 200, 201, 202, 210, 211, 212, 213, 214, 215,
    216, 217, 218, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 241, 242, 243, 244, 245,
    246, 247, 248, 249, 250,
)

_AC_CHROMINANCE_BITS = (0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 119)

_AC_CHROMINANCE_VALUES = (
    0, 1, 2, 3, 17, 4, 5, 33, 49, 6, 18, 65, 81, 7, 97, 113, 19, 34, 50, 129, 8, 20, 66, 145,
    161, 177, 193, 9, 35, 51, 82, 240, 21, 98, 114, 209, 10, 22, 36, 52, 225, 37, 241, 23,
    24, 25, 26, 38, 39, 40, 41, 42, 53, 54, 55, 56, 57, 58, 67, 68, 69, 70, 71, 72, 73, 74,
    83, 84, 85, 86, 87, 88, 89, 90, 99, 100, 101, 102, 103, 104, 105, 106, 115, 116, 117,
    118, 119, 120, 121, 122, 130, 131, 132, 133, 134, 135, 136, 137, 138, 146, 147, 148, 149,
    150, 151, 152, 153, 154, 162, 163, 164, 165, 166, 167, 168, 169, 170, 178, 179, 180, 181,
    182, 183, 184, 185, 186, 194, 195, 196, 197, 198, 199, 200, 201, 202, 210, 211, 212, 213,
    214, 215, 216, 217, 218, 226, 227, 228, 229, 230, 231, 232, 233, 234, 242, 243, 244, 245,
    246, 247, 248, 249, 250,
)

# The DC coefficient of each component's blocks at phase 0, and the AC coefficients
# the sparse pattern places.  A detail block takes an AC coefficient at every
# zigzag position instead, so its entropy-coded scan is much denser than the sparse
# one's.
_DC_BASE = (40, 0, 0)
_SPARSE_AC = ((1, 3), (2, -2), (8, 1))

# Fixture geometry: (name, width, height, components, detail).  A component is
# (id, horizontal sampling, vertical sampling); a 2x2 luminance component with 1x1
# chrominance components is 4:2:0, whose chroma planes are upsampled by sample
# replication when the file is decoded.  4:2:2 and 4:4:0 sample horizontally and
# vertically respectively, which the upsampling reads as separate factors.
_FIXTURES: list[tuple[str, int, int, list[tuple[int, int, int]], bool]] = [
    ("greyscale-64x64", 64, 64, [(1, 1, 1)], False),
    ("greyscale-detail-64x64", 64, 64, [(1, 1, 1)], True),
    ("colour-444-64x64", 64, 64, [(1, 1, 1), (2, 1, 1), (3, 1, 1)], False),
    ("colour-420-64x64", 64, 64, [(1, 2, 2), (2, 1, 1), (3, 1, 1)], False),
    ("colour-422-64x64", 64, 64, [(1, 2, 1), (2, 1, 1), (3, 1, 1)], False),
    ("colour-440-64x64", 64, 64, [(1, 1, 2), (2, 1, 1), (3, 1, 1)], False),
    ("colour-444-128x128", 128, 128, [(1, 1, 1), (2, 1, 1), (3, 1, 1)], False),
    ("colour-420-128x128", 128, 128, [(1, 2, 2), (2, 1, 1), (3, 1, 1)], False),
    ("colour-444-97x61", 97, 61, [(1, 1, 1), (2, 1, 1), (3, 1, 1)], False),
]

_cache: dict[str, bytes] = {}


def _huffman_codes(bits: tuple[int, ...], values: tuple[int, ...]) -> dict[int, tuple[int, int]]:
    """Return the canonical code and length of each symbol in a Huffman table."""
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    index = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            codes[values[index]] = (code, length)
            code += 1
            index += 1

        code <<= 1

    return codes


_DC_LUMINANCE_CODES = _huffman_codes(_DC_LUMINANCE_BITS, _DC_LUMINANCE_VALUES)
_DC_CHROMINANCE_CODES = _huffman_codes(_DC_CHROMINANCE_BITS, _DC_CHROMINANCE_VALUES)
_AC_LUMINANCE_CODES = _huffman_codes(_AC_LUMINANCE_BITS, _AC_LUMINANCE_VALUES)
_AC_CHROMINANCE_CODES = _huffman_codes(_AC_CHROMINANCE_BITS, _AC_CHROMINANCE_VALUES)


class _BitWriter:
    """Writes MSB-first bits to a byte buffer, stuffing a zero after an 0xFF."""

    def __init__(self) -> None:
        """Start an empty buffer with no pending bits."""
        self._out = bytearray()
        self._bits = 0
        self._count = 0

    def write_bits(self, value: int, count: int) -> None:
        """Write the low `count` bits of `value`, most significant first."""
        for shift in range(count - 1, -1, -1):
            self._bits = (self._bits << 1) | ((value >> shift) & 1)
            self._count += 1
            if self._count == 8:
                self._emit(self._bits)
                self._bits = 0
                self._count = 0

    def flush(self) -> bytes:
        """Pad the final byte with one bits and return the written bytes."""
        if self._count:
            padding = 8 - self._count
            self._emit((self._bits << padding) | ((1 << padding) - 1))
            self._bits = 0
            self._count = 0

        return bytes(self._out)

    def _emit(self, byte: int) -> None:
        """Append one byte, stuffing a zero after an 0xFF."""
        self._out.append(byte)
        if byte == 0xFF:
            self._out.append(0x00)


def _category(value: int) -> int:
    """Return the number of bits a coefficient needs (spec F.1.2.1)."""
    return abs(value).bit_length()


def _magnitude(value: int, size: int) -> int:
    """Return the `size`-bit magnitude encoding of a coefficient (spec F.1.2.1)."""
    if value < 0:
        return value + (1 << size) - 1

    return value


def _put_symbol(writer: _BitWriter, codes: dict[int, tuple[int, int]], symbol: int, size: int,
                value: int) -> None:
    """Write one Huffman symbol: its code, then its magnitude bits."""
    code, length = codes[symbol]
    writer.write_bits(code, length)
    writer.write_bits(_magnitude(value, size), size)


def _encode_block(writer: _BitWriter, coefficients: list[int], dc_codes: dict[int, tuple[int, int]],
                  ac_codes: dict[int, tuple[int, int]], predictor: int) -> int:
    """Entropy-code one block of coefficients given in zigzag order, returning its DC value."""
    difference = coefficients[0] - predictor
    _put_symbol(writer, dc_codes, _category(difference), _category(difference), difference)
    run = 0
    for position in range(1, 64):
        value = coefficients[position]
        if value == 0:
            run += 1
            continue

        while run > 15:
            _put_symbol(writer, ac_codes, 0xF0, 0, 0)
            run -= 16

        _put_symbol(writer, ac_codes, (run << 4) | _category(value), _category(value), value)
        run = 0

    if run > 0:
        _put_symbol(writer, ac_codes, 0x00, 0, 0)

    return coefficients[0]


def _segment(marker: int, payload: bytes) -> bytes:
    """Return a marker segment: the marker, its length, and its payload."""
    return struct.pack(">BBH", 0xFF, marker, len(payload) + 2) + payload


def _quant_segment() -> bytes:
    """Return a DQT segment holding one flat quantisation table, table 0."""
    return _segment(0xDB, b"\x00" + bytes(1 for _ in range(64)))


def _frame_segment(width: int, height: int, components: list[tuple[int, int, int]]) -> bytes:
    """Return the SOF0 segment for 8-bit precision and the given components."""
    payload = struct.pack(">BHHB", 8, height, width, len(components))
    for component_id, h, v in components:
        payload += bytes((component_id, (h << 4) | v, 0))

    return _segment(0xC0, payload)


def _huffman_segment() -> bytes:
    """Return a DHT segment holding all four standard tables."""
    tables = [
        (0x00, _DC_LUMINANCE_BITS, _DC_LUMINANCE_VALUES),
        (0x10, _AC_LUMINANCE_BITS, _AC_LUMINANCE_VALUES),
        (0x01, _DC_CHROMINANCE_BITS, _DC_CHROMINANCE_VALUES),
        (0x11, _AC_CHROMINANCE_BITS, _AC_CHROMINANCE_VALUES),
    ]
    payload = bytearray()
    for selector, bits, values in tables:
        payload += bytes((selector,)) + bytes(bits) + bytes(values)

    return _segment(0xC4, bytes(payload))


def _scan_segment(components: list[tuple[int, int, int]]) -> bytes:
    """Return the SOS segment: one sequential scan over every component."""
    payload = bytes((len(components),))
    for index, (component_id, _, _) in enumerate(components):
        payload += bytes((component_id, 0x00 if index == 0 else 0x11))

    return _segment(0xDA, payload + bytes((0, 63, 0)))


def _coefficients(component: int, block_x: int, block_y: int, detail: bool) -> list[int]:
    """Return the 64 coefficients of one block, in zigzag order."""
    # Each block differs from its neighbours so the DC differences are non-zero and
    # the scan is not one repeated code.
    phase = (block_x * 3 + block_y * 5) % 4
    block = [0] * 64
    block[0] = _DC_BASE[component] + 4 * phase
    if not detail:
        for position, weight in _SPARSE_AC:
            block[position] = weight if phase % 2 == 0 else -weight

        return block

    for position in range(1, 64):
        block[position] = 0 if (position + phase) % 3 == 0 else 1 + (position + phase) % 4

    return block


def _build(width: int, height: int, components: list[tuple[int, int, int]], detail: bool) -> bytes:
    """Build a baseline sequential JPEG file of the given size and sampling factors."""
    max_h = max(h for _, h, _ in components)
    max_v = max(v for _, _, v in components)
    mcus_x = -(-width // (8 * max_h))
    mcus_y = -(-height // (8 * max_v))

    dc_codes = [_DC_LUMINANCE_CODES, _DC_CHROMINANCE_CODES, _DC_CHROMINANCE_CODES]
    ac_codes = [_AC_LUMINANCE_CODES, _AC_CHROMINANCE_CODES, _AC_CHROMINANCE_CODES]

    writer = _BitWriter()
    predictors = [0] * len(components)
    for mcu_y in range(mcus_y):
        for mcu_x in range(mcus_x):
            for index, (_, h, v) in enumerate(components):
                for block_y in range(v):
                    for block_x in range(h):
                        coefficients = _coefficients(
                            index,
                            mcu_x * h + block_x,
                            mcu_y * v + block_y,
                            detail,
                        )
                        predictors[index] = _encode_block(
                            writer, coefficients, dc_codes[index], ac_codes[index], predictors[index])

    return b"".join([
        b"\xff\xd8",
        _quant_segment(),
        _frame_segment(width, height, components),
        _huffman_segment(),
        _scan_segment(components),
        writer.flush(),
        b"\xff\xd9",
    ])


def _fixture_bytes(name: str) -> bytes:
    """Return the fixture bytes for a name, generating them on first use."""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    for fixture_name, width, height, components, detail in _FIXTURES:
        if fixture_name == name:
            data = _build(width, height, components, detail)
            _cache[name] = data
            return data

    raise KeyError(f"unknown JPEG fixture: {name}")


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai JPEG decoder."""

    name = "jpeg-decode"
    description = "Decode baseline sequential JPEG files, greyscale and every chroma sampling factor."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per fixture file."""
        return [
            BenchmarkCase(
                name=name,
                input=name,
                iterations=_ITERATIONS,
            )
            for name, _, _, _, _ in _FIXTURES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the JPEG decode expression, reading fixture bytes as its input."""
        return MenaiProgram(
            expression=_EXPR,
            fixture=_fixture_bytes,
        )
