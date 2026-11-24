"""
Python reference implementation of the deflate-compress Menai module.

This mirrors src/menai/stdlib/deflate-compress.menai, which is a raw DEFLATE
(RFC 1951) compressor, so that the two can be timed against each other as the
same work. It is written the way a Python programmer would write it -- a
bytearray output, mutable lists for the candidate table, exceptions for errors,
and dict lookups for the Huffman tables -- but every algorithmic choice matches
the Menai module, because that is what makes the comparison a language
comparison rather than an algorithm comparison:

- greedy LZ77 with no lazy matching;
- a single dict mapping a three-byte prefix to a most-recent-first list of
  positions, with the same periodic insertion for matches longer than their
  distance;
- the same window size (32768), minimum match (3), maximum match (258),
  chain limit (128) and nice-match cut-off (128);
- the same common-prefix search: one whole-span comparison, then a binary
  search for the divergence point;
- canonical Huffman codes reversed into bit-write order, code lengths built by
  the two-queue method and clamped to 15 bits, and the same run-length encoding
  of code lengths including its chunking choices;
- 'auto' mode encoding all three block types and keeping the shortest, in the
  same order of preference.

`compress` therefore emits byte-identical output to the Menai module, which is
the assertion the deflate suites rely on. Without that assertion a timing
comparison between the two would be meaningless.
"""

from __future__ import annotations

from collections.abc import Callable

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

WINDOW_SIZE = 32768

MIN_MATCH = 3

MAX_MATCH = 258

MAX_CHAIN = 128

NICE_MATCH = 128

MAX_CODE_LENGTH = 15

STORED_BLOCK_MAX = 65535

# A Huffman tree node: a symbol in a leaf, or a (left, right) pair in an internal node.
Payload = int | tuple["Payload", "Payload"]


class Writer:
    """A DEFLATE bit writer: bytes emitted so far, plus a partial byte accumulator."""

    __slots__ = ("data", "bit", "acc")

    def __init__(self) -> None:
        """Create an empty writer."""
        self.data = bytearray()
        self.bit = 0
        self.acc = 0

    def write_bits(self, value: int, count: int) -> None:
        """Append count bits of value, least-significant bit first."""
        if count == 0:
            return

        self.acc |= (value & ((1 << count) - 1)) << self.bit
        total = self.bit + count
        while total >= 8:
            self.data.append(self.acc & 0xFF)
            total -= 8
            self.acc >>= 8

        self.bit = total

    def align(self) -> None:
        """Pad with zero bits to the next byte boundary."""
        if self.bit != 0:
            self.write_bits(0, 8 - self.bit)

    def to_bytes(self) -> bytes:
        """Return the bytes written, padded to a byte boundary."""
        self.align()
        return bytes(self.data)


def reverse_bits(value: int, length: int) -> int:
    """Return the low length bits of value, reversed."""
    result = 0
    for i in range(length):
        result = (result << 1) | ((value >> i) & 1)

    return result


def find_base_index(bases: tuple[int, ...], value: int) -> int:
    """Return the index of the largest base value not exceeding value."""
    lo = 0
    hi = len(bases) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if bases[mid] <= value:
            lo = mid

        else:
            hi = mid - 1

    return lo


def encode_length(length: int) -> tuple[int, int, int]:
    """Return (symbol, extra bits, extra count) for a match length."""
    index = find_base_index(LENGTH_BASE, length)
    return 257 + index, length - LENGTH_BASE[index], LENGTH_EXTRA[index]


def encode_distance(distance: int) -> tuple[int, int, int]:
    """Return (symbol, extra bits, extra count) for a match distance."""
    index = find_base_index(DISTANCE_BASE, distance)
    return index, distance - DISTANCE_BASE[index], DISTANCE_EXTRA[index]


def canonical_codes(lengths: tuple[int, ...] | list[int]) -> list[tuple[int, int]]:
    """Return (reversed code, length) per symbol, in RFC 1951 canonical order."""
    maxlen = max(lengths, default=0)

    bl_count: dict[int, int] = {}
    for length in lengths:
        bl_count[length] = bl_count.get(length, 0) + 1

    nexts: dict[int, int] = {}
    code = 0
    for length in range(1, maxlen + 1):
        nexts[length] = code
        code = (code + bl_count.get(length, 0)) << 1

    codes: list[tuple[int, int]] = []
    for length in lengths:
        if length == 0:
            codes.append((0, 0))

        else:
            value = nexts[length]
            nexts[length] = value + 1
            codes.append((reverse_bits(value, length), length))

    return codes


def write_symbol(writer: Writer, codes: list[tuple[int, int]], symbol: int) -> None:
    """Emit one symbol's Huffman code."""
    code, length = codes[symbol]
    writer.write_bits(code, length)


def prefix_key(data: bytes, i: int) -> int | None:
    """Return the three-byte key at position i, or None if fewer than three bytes remain."""
    if i + 3 > len(data):
        return None

    return (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]


def common_prefix(data: bytes, c: int, i: int, limit: int) -> int:
    """Return how many leading bytes match between the spans at c and i, up to limit."""
    if data[c:c + limit] == data[i:i + limit]:
        return limit

    lo = 0
    hi = limit
    while hi - lo > 1:
        mid = lo + (hi - lo) // 2
        if data[c:c + mid] == data[i:i + mid]:
            lo = mid

        else:
            hi = mid

    return lo


def find_match(data: bytes, i: int, table: dict[int, list[int]]) -> tuple[int, int]:
    """Return (length, distance) of the longest match at i, or (0, 0) if none."""
    key = prefix_key(data, i)
    if key is None:
        return 0, 0

    candidates = table.get(key)
    if not candidates:
        return 0, 0

    limit = min(MAX_MATCH, len(data) - i)
    best_length = 0
    best_distance = 0
    chain = 0

    for c in reversed(candidates):
        if best_length >= limit or best_length >= NICE_MATCH or chain >= MAX_CHAIN:
            break

        if i - c > WINDOW_SIZE:
            break

        chain += 1
        if data[c + best_length] == data[i + best_length]:
            length = common_prefix(data, c, i, limit)
            if length > best_length:
                best_length = length
                best_distance = i - c

    return best_length, best_distance


def add_candidate(data: bytes, i: int, table: dict[int, list[int]]) -> None:
    """Record position i in the candidate table."""
    key = prefix_key(data, i)
    if key is not None:
        table.setdefault(key, []).append(i)


def add_candidates(data: bytes, i: int, length: int, distance: int, table: dict[int, list[int]]) -> None:
    """Record every position covered by a match, one dict update per period."""
    last_full = length - 3
    if distance < length - 2:
        count = min(distance, last_full + 1)
        for p in range(count):
            key = prefix_key(data, i + p)
            if key is None:
                continue

            positions = table.setdefault(key, [])
            for k in range(p, last_full + 1, distance):
                positions.append(i + k)

        start = max(0, length - 2)

    else:
        start = 0

    for k in range(start, length):
        add_candidate(data, i + k, table)


def tokenize(data: bytes) -> list[tuple[int, ...]]:
    """Return the LZ77 token list: (0, byte) for a literal, (1, length, distance) for a match."""
    table: dict[int, list[int]] = {}
    tokens: list[tuple[int, ...]] = []
    i = 0
    size = len(data)

    while i < size:
        length, distance = find_match(data, i, table)
        if length >= MIN_MATCH:
            add_candidates(data, i, length, distance, table)
            tokens.append((1, length, distance))
            i += length

        else:
            add_candidate(data, i, table)
            tokens.append((0, data[i]))
            i += 1

    return tokens


def build_code_lengths(freqs: list[int]) -> list[int]:
    """Return Huffman code lengths for a frequency list, zero for unused symbols."""
    active: list[tuple[Payload, int]] = [(symbol, freq) for symbol, freq in enumerate(freqs) if freq > 0]
    if not active:
        return [0] * len(freqs)

    active.sort(key=lambda node: node[1])
    merged = _merge_all(active)

    depths: dict[int, int] = {}
    _collect_depths(merged[0], 0, depths)
    lengths = [depths.get(symbol, 0) for symbol in range(len(freqs))]

    if max(lengths, default=0) == 0:
        return [1 if freq > 0 else 0 for freq in freqs]

    return lengths


def _merge_all(leaves: list[tuple[Payload, int]]) -> tuple[Payload, int]:
    """Build the Huffman tree, merging the two lightest nodes from two weight-ordered queues."""
    internals: list[tuple[Payload, int]] = []
    while len(leaves) + len(internals) > 1:
        first, leaves, internals = _pop_min(leaves, internals)
        second, leaves, internals = _pop_min(leaves, internals)
        internals.append(((first[0], second[0]), first[1] + second[1]))

    return internals[0] if internals else leaves[0]


def _pop_min(
    leaves: list[tuple[Payload, int]],
    internals: list[tuple[Payload, int]],
) -> tuple[tuple[Payload, int], list[tuple[Payload, int]], list[tuple[Payload, int]]]:
    """Return the lightest node and the two queues with it removed."""
    if not leaves:
        return internals[0], leaves, internals[1:]

    if not internals or leaves[0][1] <= internals[0][1]:
        return leaves[0], leaves[1:], internals

    return internals[0], leaves, internals[1:]


def _collect_depths(payload: Payload, depth: int, depths: dict[int, int]) -> None:
    """Record the depth of every symbol in a Huffman tree payload."""
    if isinstance(payload, tuple):
        _collect_depths(payload[0], depth + 1, depths)
        _collect_depths(payload[1], depth + 1, depths)

    else:
        depths[payload] = depth


def clamp_lengths(lengths: list[int]) -> list[int]:
    """Clamp code lengths to the DEFLATE maximum of 15 bits."""
    return [min(length, MAX_CODE_LENGTH) for length in lengths]


def write_block_header(writer: Writer, final: bool, btype: int) -> None:
    """Write the three-bit block header (BFINAL, BTYPE)."""
    writer.write_bits(1 if final else 0, 1)
    writer.write_bits(btype, 2)


def write_stored_block(writer: Writer, data: bytes, start: int, end: int, final: bool) -> Writer:
    """Encode a stored block; returns a fresh writer holding the appended raw bytes."""
    write_block_header(writer, final, 0)
    writer.align()
    length = end - start
    writer.write_bits(length, 16)
    writer.write_bits(~length & 0xFFFF, 16)
    writer.align()
    result = Writer()
    result.data = writer.data
    result.data.extend(data[start:end])
    return result


def write_tokens(
    writer: Writer,
    tokens: list[tuple[int, ...]],
    lit_codes: list[tuple[int, int]],
    dist_codes: list[tuple[int, int]],
) -> None:
    """Emit the token stream using the given code lists, ending with symbol 256."""
    for token in tokens:
        if token[0] == 0:
            write_symbol(writer, lit_codes, token[1])

        else:
            length, distance = token[1], token[2]
            symbol, extra, count = encode_length(length)
            write_symbol(writer, lit_codes, symbol)
            writer.write_bits(extra, count)
            symbol, extra, count = encode_distance(distance)
            write_symbol(writer, dist_codes, symbol)
            writer.write_bits(extra, count)

    write_symbol(writer, lit_codes, 256)


def write_fixed_block(writer: Writer, tokens: list[tuple[int, ...]], final: bool) -> None:
    """Encode a fixed Huffman block (BTYPE = 01)."""
    write_block_header(writer, final, 1)
    write_tokens(
        writer,
        tokens,
        canonical_codes(FIXED_LIT_LENGTHS),
        canonical_codes(FIXED_DIST_LENGTHS),
    )


def count_frequencies(tokens: list[tuple[int, ...]]) -> tuple[list[int], list[int]]:
    """Return the literal/length and distance symbol frequencies of a token list."""
    lit_freqs = [0] * 286
    dist_freqs = [0] * 30
    lit_freqs[256] = 1

    for token in tokens:
        if token[0] == 0:
            lit_freqs[token[1]] += 1

        else:
            lit_freqs[encode_length(token[1])[0]] += 1
            dist_freqs[encode_distance(token[2])[0]] += 1

    return lit_freqs, dist_freqs


def trimmed_count(lengths: list[int], minimum: int) -> int:
    """Return how many entries to keep, dropping trailing zero lengths to a minimum."""
    n = len(lengths)
    while n > minimum and lengths[n - 1] == 0:
        n -= 1

    return n


def rle_code_lengths(lengths: list[int]) -> list[tuple[int, int, int]]:
    """Run-length encode code lengths into (symbol, extra bits, extra count) items."""
    items: list[tuple[int, int, int]] = []
    i = 0
    size = len(lengths)

    while i < size:
        value = lengths[i]
        n = 1
        while i + n < size and lengths[i + n] == value:
            n += 1

        if value == 0:
            _emit_zeros(items, n)

        else:
            _emit_nonzero(items, value, n)

        i += n

    return items


def _emit_zeros(items: list[tuple[int, int, int]], n: int) -> None:
    """Emit a run of n zeros using codes 17 (3-10) and 18 (11-138)."""
    while n > 0:
        if n <= 2:
            for _ in range(n):
                items.append((0, 0, 0))

            return

        if n <= 10:
            chunk = min(10, n)
            items.append((17, chunk - 3, 3))

        else:
            chunk = min(138, n)
            items.append((18, chunk - 11, 7))

        n -= chunk


def _emit_nonzero(items: list[tuple[int, int, int]], value: int, n: int) -> None:
    """Emit a run of n copies of a non-zero value."""
    items.append((value, 0, 0))
    n -= 1
    while n > 0:
        if n <= 2:
            for _ in range(n):
                items.append((value, 0, 0))

            return

        chunk = min(6, n)
        items.append((16, chunk - 3, 2))
        n -= chunk


def build_code_length_code(
    items: list[tuple[int, int, int]],
) -> tuple[list[int], list[tuple[int, int]], list[tuple[int, int, int]]]:
    """Return (code-length lengths, code-length codes, items)."""
    freq_map: dict[int, int] = {}
    for item in items:
        freq_map[item[0]] = freq_map.get(item[0], 0) + 1

    cl_freqs = [freq_map.get(i, 0) for i in range(19)]
    cl_lengths = clamp_lengths(build_code_lengths(cl_freqs))
    return cl_lengths, canonical_codes(cl_lengths), items


def code_length_count(cl_lengths: list[int]) -> int:
    """Return the number of code-length code lengths to transmit (HCLEN + 4)."""
    n = 19
    while n > 4 and cl_lengths[CODE_LENGTH_ORDER[n - 1]] == 0:
        n -= 1

    return n


def write_code_length_lengths(writer: Writer, cl_lengths: list[int], count: int) -> None:
    """Write the code-length code lengths in the special order."""
    for i in range(count):
        writer.write_bits(cl_lengths[CODE_LENGTH_ORDER[i]], 3)


def write_code_lengths(writer: Writer, items: list[tuple[int, int, int]], cl_codes: list[tuple[int, int]]) -> None:
    """Write the run-length encoded code lengths using the code-length code."""
    for item in items:
        write_symbol(writer, cl_codes, item[0])
        writer.write_bits(item[1], item[2])


def write_dynamic_block(writer: Writer, tokens: list[tuple[int, ...]], final: bool) -> None:
    """Encode a dynamic Huffman block (BTYPE = 10)."""
    lit_freqs, dist_freqs = count_frequencies(tokens)
    lit_lengths = clamp_lengths(build_code_lengths(lit_freqs))
    dist_lengths = clamp_lengths(build_code_lengths(dist_freqs))
    num_lit = trimmed_count(lit_lengths, 257)
    num_dist = trimmed_count(dist_lengths, 1)

    items = rle_code_lengths(lit_lengths[:num_lit] + dist_lengths[:num_dist])
    cl_lengths, cl_codes, cl_items = build_code_length_code(items)
    hclen_count = code_length_count(cl_lengths)

    write_block_header(writer, final, 2)
    writer.write_bits(num_lit - 257, 5)
    writer.write_bits(num_dist - 1, 5)
    writer.write_bits(hclen_count - 4, 4)
    write_code_length_lengths(writer, cl_lengths, hclen_count)
    write_code_lengths(writer, cl_items, cl_codes)
    write_tokens(writer, tokens, canonical_codes(lit_lengths), canonical_codes(dist_lengths))


def encode_stored(data: bytes) -> bytes:
    """Encode the whole input as stored (BTYPE = 00) blocks."""
    size = len(data)
    writer = Writer()
    if size == 0:
        return write_stored_block(writer, data, 0, 0, True).to_bytes()

    start = 0
    while start < size:
        end = min(size, start + STORED_BLOCK_MAX)
        writer = write_stored_block(writer, data, start, end, end == size)
        start = end

    return writer.to_bytes()


def encode_fixed(tokens: list[tuple[int, ...]]) -> bytes:
    """Encode a token stream as a single fixed Huffman block."""
    writer = Writer()
    write_fixed_block(writer, tokens, True)
    return writer.to_bytes()


def encode_dynamic(tokens: list[tuple[int, ...]]) -> bytes:
    """Encode a token stream as a single dynamic Huffman block."""
    writer = Writer()
    write_dynamic_block(writer, tokens, True)
    return writer.to_bytes()


def encode_auto(data: bytes) -> bytes:
    """Encode with each block type and return the shortest result."""
    tokens = tokenize(data)
    stored = encode_stored(data)
    fixed = encode_fixed(tokens)
    dynamic = encode_dynamic(tokens)
    shortest = stored if len(stored) < len(fixed) else fixed
    return shortest if len(shortest) < len(dynamic) else dynamic


def compress(data: bytes, mode: str = "auto") -> bytes:
    """Compress data into a raw DEFLATE stream."""
    if mode == "stored":
        return encode_stored(data)

    if mode == "fixed":
        return encode_fixed(tokenize(data))

    if mode == "dynamic":
        return encode_dynamic(tokenize(data))

    return encode_auto(data)


def mode_table() -> dict[str, Callable[..., bytes]]:
    """Return the mode names mapped to their encoders, mirroring the module's mode enum."""
    return {
        "auto": lambda data: compress(data, "auto"),
        "stored": lambda data: compress(data, "stored"),
        "fixed": lambda data: compress(data, "fixed"),
        "dynamic": lambda data: compress(data, "dynamic"),
    }
