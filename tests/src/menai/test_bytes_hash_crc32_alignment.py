"""Tests that bytes hashing and CRC-32 are independent of buffer alignment.

`bytes-slice` returns a view whose data pointer is an arbitrary byte offset
into the owning buffer's inline storage.  A sliced value therefore has no
guaranteed alignment, and the native hash and CRC-32 kernels read directly
from that pointer.  These tests embed each standard test vector inside a
larger buffer behind a variable-length prefix and ahead of a variable-length
suffix, then slice out exactly the vector and verify the digest or checksum
against the reference value.  Varying the prefix length shifts the slice's
start offset across every alignment (mod 8 and beyond), which is where an
alignment-dependent load would produce a wrong result.
"""

import pytest


PADDING_HEX = "a5c3e10f7b2d48963f5a0c8e1b7d2496"

SUFFIX_HEX = "deadbeefcafebabe0123456789abcdef"

PREFIX_LENGTHS = list(range(0, 16))


def build_padded_slice_expression(vector_hex: str, prefix_len: int, op: str) -> str:
    """Build a Menai expression that hashes a padded slice of a buffer.

    The buffer is `padding || vector || suffix`, where the padding is exactly
    `prefix_len` bytes, so the vector sits at byte offset `prefix_len`.  The
    expression slices out the vector at [prefix_len, prefix_len + vector_len)
    and applies `op` to it, exercising a slice whose data pointer is offset by
    `prefix_len` bytes from the start of the owning buffer.
    """
    vector_len = len(vector_hex) // 2
    padding_hex = PADDING_HEX[: 2 * prefix_len]
    start = prefix_len
    end = prefix_len + vector_len
    return (
        f'({op} (bytes-slice '
        f'(bytes-concat (string-hex->bytes "{padding_hex}") '
        f'(string-hex->bytes "{vector_hex}") '
        f'(string-hex->bytes "{SUFFIX_HEX}")) '
        f'{start} {end}))'
    )


class TestHashAlignmentIndependence:
    """Hash digests are correct for slices at every buffer offset."""

    @pytest.mark.parametrize("op,vector_hex,expected", [
        (
            "bytes-hash-sha2-256",
            "616263",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        (
            "bytes-hash-sha2-512",
            "616263",
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
            "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        ),
        (
            "bytes-hash-sha2-512-256",
            "616263",
            "53048e2681941ef99b2e29b76b4c7dabe4c2d0c634fc6d46e0e2f13107e7af23",
        ),
        (
            "bytes-hash-sha3-256",
            "616263",
            "3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532",
        ),
    ])
    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_digest_matches_reference_at_every_offset(self, menai, op, vector_hex, expected, prefix_len):
        """The digest of a sliced vector equals the reference digest for that vector."""
        expr = build_padded_slice_expression(vector_hex, prefix_len, op)
        result = menai.evaluate(f'(bytes->string-hex {expr})')
        assert result == expected

    @pytest.mark.parametrize("op,vector_hex,expected", [
        (
            "bytes-hash-sha2-256",
            "6162636462636465636465666465666765666768666768696768696a68696a6b"
            "696a6b6c6a6b6c6d6b6c6d6e6c6d6e6f6d6e6f706e6f7071",
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
        (
            "bytes-hash-sha2-512",
            "61626364656667686263646566676869636465666768696a6465666768696a6b"
            "65666768696a6b6c666768696a6b6c6d6768696a6b6c6d6e68696a6b6c6d6e6f"
            "696a6b6c6d6e6f706a6b6c6d6e6f70716b6c6d6e6f7071726c6d6e6f70717273"
            "6d6e6f70717273746e6f707172737475",
            "8e959b75dae313da8cf4f72814fc143f8f7779c6eb9f7fa17299aeadb6889018"
            "501d289e4900f7e4331b99dec4b5433ac7d329eeb6dd26545e96e55b874be909",
        ),
    ])
    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_multi_block_digest_matches_reference_at_every_offset(
        self, menai, op, vector_hex, expected, prefix_len
    ):
        """A vector longer than one block hashes correctly from every offset."""
        expr = build_padded_slice_expression(vector_hex, prefix_len, op)
        result = menai.evaluate(f'(bytes->string-hex {expr})')
        assert result == expected


class TestCrc32AlignmentIndependence:
    """CRC-32 checksums are correct for slices at every buffer offset."""

    @pytest.mark.parametrize("vector_hex,expected", [
        ("", 0),
        ("616263", 0x352441C2),
        ("313233343536373839", 0xCBF43926),
        ("68656c6c6f20776f726c64", 0x0D4A1185),
    ])
    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_checksum_matches_reference_at_every_offset(self, menai, vector_hex, expected, prefix_len):
        """The checksum of a sliced vector equals the reference checksum for that vector."""
        expr = build_padded_slice_expression(vector_hex, prefix_len, "bytes-crc32")
        result = menai.evaluate(expr)
        assert result == expected

    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_checksum_of_multi_block_vector_matches_reference(self, menai, prefix_len):
        """A vector spanning several slice-by-8 iterations checksums correctly from every offset."""
        vector_hex = "61" * 1000
        expr = build_padded_slice_expression(vector_hex, prefix_len, "bytes-crc32")
        result = menai.evaluate(expr)
        assert result == 2587417091


class TestPaddedSliceEqualsUnpaddedValue:
    """A padded slice hashes identically to the equivalent unpadded value."""

    @pytest.mark.parametrize("op", [
        "bytes-hash-sha2-256",
        "bytes-hash-sha2-512",
        "bytes-hash-sha2-512-256",
        "bytes-hash-sha3-256",
    ])
    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_digest_of_slice_equals_digest_of_plain_bytes(self, menai, op, prefix_len):
        """Hashing a padded slice equals hashing the bare vector bytes."""
        vector_hex = "616263"
        sliced = build_padded_slice_expression(vector_hex, prefix_len, op)
        plain = f'({op} (string-hex->bytes "{vector_hex}"))'
        expr = f'(bytes=? {sliced} {plain})'
        assert menai.evaluate(expr) is True

    @pytest.mark.parametrize("prefix_len", PREFIX_LENGTHS)
    def test_checksum_of_slice_equals_checksum_of_plain_bytes(self, menai, prefix_len):
        """Checksumming a padded slice equals checksumming the bare vector bytes."""
        vector_hex = "313233343536373839"
        sliced = build_padded_slice_expression(vector_hex, prefix_len, "bytes-crc32")
        plain = f'(bytes-crc32 (string-hex->bytes "{vector_hex}"))'
        expr = f'(integer=? {sliced} {plain})'
        assert menai.evaluate(expr) is True
