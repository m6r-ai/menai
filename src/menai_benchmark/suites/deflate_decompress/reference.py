"""
Python reference implementation of the deflate-decompress Menai module.

This mirrors src/menai/stdlib/deflate-decompress.menai, which is a raw DEFLATE
(RFC 1951) decompressor, so that the two can be timed as the same work. It is
written the way a Python programmer would write it -- a bytearray for output, an
integer bit position rather than a threaded reader value, and dict lookups for
the Huffman tables -- but every algorithmic choice matches the Menai module:

- all three block types, with BTYPE = 11 rejected;
- Huffman decoding by reading one bit at a time and probing a table keyed by
  ``(1 << length) + code``, not by a lookup table indexed on a bit window;
- bit reads gathered from the whole bytes spanning the range rather than one bit
  at a time;
- back-references resolved by taking ``min(distance, length)`` bytes ending at the
  end of the window and repeating that block;
- consecutive literals gathered and appended in one operation per run;
- the same error conditions, with the same messages.

The one structural difference is that the Menai module carries its output as an
append-only value tree with bounded fragment lists, because it has no mutable
state. Here the output is a plain bytearray, and the "window" is simply the tail
of it, which is the whole point of the comparison.
"""

from __future__ import annotations

LENGTH_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258)

LENGTH_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0)

DISTANCE_BASE = (
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513,
    769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
)

DISTANCE_EXTRA = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13)

CODE_LENGTH_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)

FIXED_LIT_LENGTHS = (8,) * 144 + (9,) * 112 + (7,) * 24 + (8,) * 8

FIXED_DIST_LENGTHS = (5,) * 30

WINDOW_CAP = 32768

END_OF_INPUT = "unexpected end of input while reading bits"


class DeflateError(ValueError):
    """Raised for a malformed DEFLATE stream."""


def _fail(message: str) -> None:
    """Raise a DeflateError carrying the module's message prefix."""
    raise DeflateError("deflate-decompress: " + message)


def read_bit(data: bytes, p: int) -> tuple[int, int]:
    """Return (bit at position p, p + 1)."""
    pos = p // 8
    if pos >= len(data):
        _fail(END_OF_INPUT)

    return (data[pos] >> (p % 8)) & 1, p + 1


def read_bits(data: bytes, p: int, n: int) -> tuple[int, int]:
    """Return (n bits LSB-first at position p, p + n), gathered from whole bytes."""
    start_byte = p // 8
    start_bit = p % 8
    nbytes = (start_bit + n + 7) // 8
    if start_byte + nbytes > len(data):
        _fail(END_OF_INPUT)

    chunk = 0
    for k in range(nbytes):
        chunk |= data[start_byte + k] << (8 * k)

    return (chunk >> start_bit) & ((1 << n) - 1), p + n


def align_to_byte(p: int) -> int:
    """Return the position of the next byte boundary at or after p."""
    if p % 8 == 0:
        return p

    return (p // 8 + 1) * 8


def read_raw_bytes(data: bytes, p: int, n: int) -> tuple[bytes, int]:
    """Return (n raw bytes from the byte containing position p, p + 8n)."""
    pos = p // 8
    if pos + n > len(data):
        _fail("unexpected end of input while reading stored block")

    return data[pos:pos + n], p + 8 * n


def build_huffman_table(lengths: tuple[int, ...] | list[int]) -> dict[int, int]:
    """Return the canonical decoding table mapping (1 << length) + code to symbol."""
    maxlen = max(lengths, default=0)

    bl_count: dict[int, int] = {}
    for length in lengths:
        bl_count[length] = bl_count.get(length, 0) + 1

    first_codes: dict[int, int] = {}
    code = 0
    for length in range(1, maxlen + 1):
        first_codes[length] = code
        code = (code + bl_count.get(length, 0)) << 1

    table: dict[int, int] = {}
    for symbol, length in enumerate(lengths):
        if length != 0:
            value = first_codes[length]
            first_codes[length] = value + 1
            table[(1 << length) + value] = symbol

    return table


def decode_symbol(data: bytes, p: int, table: dict[int, int]) -> tuple[int, int]:
    """Decode one symbol, reading bits one at a time until a code matches."""
    length = 0
    code = 0
    while length <= 15:
        byte = p // 8
        if byte >= len(data):
            _fail(END_OF_INPUT)

        bit = (data[byte] >> (p % 8)) & 1
        code = (code << 1) | bit
        length += 1
        p += 1
        symbol = table.get((1 << length) + code)
        if symbol is not None:
            return symbol, p

    _fail("invalid Huffman code (no match within 15 bits)")
    raise AssertionError("unreachable")


def read_extra(data: bytes, p: int, n: int) -> tuple[int, int]:
    """Read n extra bits, returning (value, new position)."""
    if n == 0:
        return 0, p

    return read_bits(data, p, n)


def decode_length(data: bytes, p: int, symbol: int) -> tuple[int, int]:
    """Decode a length symbol (257-285) into a length value."""
    index = symbol - 257
    extra = read_extra(data, p, LENGTH_EXTRA[index])
    return LENGTH_BASE[index] + extra[0], extra[1]


def decode_distance(data: bytes, p: int, symbol: int) -> tuple[int, int]:
    """Decode a distance symbol (0-29) into a distance value."""
    extra = read_extra(data, p, DISTANCE_EXTRA[symbol])
    return DISTANCE_BASE[symbol] + extra[0], extra[1]


def repeat_to_length(block: bytes, block_len: int, length: int) -> bytes:
    """Return exactly length bytes formed by repeating block, then truncating."""
    acc = block[:block_len]
    while len(acc) < length:
        acc += block[:block_len]

    return acc[:length]


class Output:
    """The decompressed output, as a growable byte buffer plus its window tail."""

    __slots__ = ("data",)

    def __init__(self) -> None:
        """Create an empty output."""
        self.data = bytearray()

    def total(self) -> int:
        """Return how many bytes have been produced."""
        return len(self.data)

    def append(self, block: bytes) -> None:
        """Append bytes to the output."""
        self.data += block

    def window(self) -> bytes:
        """Return the most recent bytes a back-reference can reach."""
        if len(self.data) > WINDOW_CAP:
            return bytes(self.data[-WINDOW_CAP:])

        return bytes(self.data)


def copy_back_reference(out: Output, distance: int, length: int) -> None:
    """Copy length bytes from distance bytes back in the output, appending them."""
    if distance > out.total():
        _fail("back-reference distance exceeds output produced so far")

    win = out.window()
    wlen = len(win)
    block_len = min(distance, length)
    block = win[wlen - distance:wlen - distance + block_len]
    out.append(repeat_to_length(block, block_len, length))


def decode_huffman_block(
    data: bytes,
    p: int,
    lit_table: dict[int, int],
    dist_table: dict[int, int],
    out: Output,
) -> tuple[Output, int]:
    """Decode a Huffman-coded block up to its end-of-block symbol."""
    pending: list[int] = []

    while True:
        symbol, p = decode_symbol(data, p, lit_table)
        if symbol == 256:
            if pending:
                out.append(bytes(pending))
                pending.clear()

            return out, p

        if symbol < 256:
            pending.append(symbol)
            continue

        length, p = decode_length(data, p, symbol)
        dist_symbol, p = decode_symbol(data, p, dist_table)
        distance, p = decode_distance(data, p, dist_symbol)
        if pending:
            out.append(bytes(pending))
            pending.clear()

        copy_back_reference(out, distance, length)


def decode_stored_block(data: bytes, p: int) -> tuple[bytes, int]:
    """Decode one stored block (BTYPE = 00)."""
    aligned = align_to_byte(p)
    len_bytes, pos2 = read_raw_bytes(data, aligned, 2)
    nlen_bytes, pos3 = read_raw_bytes(data, pos2, 2)
    length = len_bytes[0] | (len_bytes[1] << 8)
    nlen = nlen_bytes[0] | (nlen_bytes[1] << 8)
    if length != (~nlen & 0xFFFF):
        _fail("stored block LEN does not match NLEN")

    return read_raw_bytes(data, pos3, length)


def read_code_length_table(data: bytes, p: int, hclen: int) -> tuple[dict[int, int], int]:
    """Read the code-length code lengths and build the code-length table."""
    count = hclen + 4
    lengths: dict[int, int] = {}
    for i in range(count):
        value, p = read_bits(data, p, 3)
        lengths[CODE_LENGTH_ORDER[i]] = value

    full = [lengths.get(i, 0) for i in range(19)]
    return build_huffman_table(full), p


def read_code_lengths(data: bytes, p: int, cl_table: dict[int, int], total: int) -> tuple[list[int], int]:
    """Decode the HLIT + HDIST code lengths, expanding repeat codes 16/17/18."""
    lengths: list[int] = []
    while len(lengths) < total:
        symbol, p = decode_symbol(data, p, cl_table)
        if symbol <= 15:
            lengths.append(symbol)

        elif symbol == 16:
            extra, p = read_bits(data, p, 2)
            lengths.extend([lengths[-1]] * (3 + extra))

        elif symbol == 17:
            extra, p = read_bits(data, p, 3)
            lengths.extend([0] * (3 + extra))

        else:
            extra, p = read_bits(data, p, 7)
            lengths.extend([0] * (11 + extra))

    return lengths, p


def decode_dynamic_block(data: bytes, p: int, out: Output) -> tuple[Output, int]:
    """Decode one dynamic Huffman block (BTYPE = 10)."""
    hlit, p = read_bits(data, p, 5)
    hdist, p = read_bits(data, p, 5)
    hclen, p = read_bits(data, p, 4)
    cl_table, p = read_code_length_table(data, p, hclen)
    num_lit = hlit + 257
    num_dist = hdist + 1
    lengths, p = read_code_lengths(data, p, cl_table, num_lit + num_dist)
    lit_lengths = lengths[:num_lit]
    dist_lengths = lengths[num_lit:num_lit + num_dist]
    return decode_huffman_block(data, p, build_huffman_table(lit_lengths), build_huffman_table(dist_lengths), out)


def decode_blocks(data: bytes, p: int, out: Output) -> tuple[Output, int]:
    """Decode blocks until the final block (BFINAL = 1) has been consumed."""
    while True:
        bfinal, p = read_bit(data, p)
        btype, p = read_bits(data, p, 2)

        if btype == 0:
            block, p = decode_stored_block(data, p)
            out.append(block)

        elif btype == 1:
            out, p = decode_huffman_block(data, p, FIXED_LIT_TABLE, FIXED_DIST_TABLE, out)

        elif btype == 2:
            out, p = decode_dynamic_block(data, p, out)

        else:
            _fail("invalid block type (BTYPE = 11)")

        if bfinal == 1:
            return out, p


def decompress(data: bytes) -> bytes:
    """Decompress a raw DEFLATE stream."""
    out, _ = decode_blocks(data, 0, Output())
    return bytes(out.data)


FIXED_LIT_TABLE = build_huffman_table(FIXED_LIT_LENGTHS)

FIXED_DIST_TABLE = build_huffman_table(FIXED_DIST_LENGTHS)
