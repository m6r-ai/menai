"""Tests for the bytes CRC-32 checksum operation."""

import pytest

from menai import MenaiEvalError


class TestCrc32ResultType:
    """The CRC-32 operation returns an integer."""

    def test_result_is_integer(self, menai):
        """CRC-32 returns an integer value."""
        assert menai.evaluate('(integer? (bytes-crc32 (string->bytes "abc")))') is True

    def test_result_is_in_range(self, menai):
        """CRC-32 returns a value in the unsigned 32-bit range."""
        result = menai.evaluate('(bytes-crc32 (string->bytes "abc"))')
        assert 0 <= result <= 0xFFFFFFFF


class TestCrc32Vectors:
    """CRC-32/ISO-HDLC against the standard check values."""

    @pytest.mark.parametrize("message,expected", [
        ("", 0),
        ("abc", 0x352441C2),
        ("123456789", 0xCBF43926),
        ("hello world", 0x0D4A1185),
    ])
    def test_vectors(self, menai, message, expected):
        """CRC-32 produces the reference checksum for each standard vector."""
        result = menai.evaluate(f'(bytes-crc32 (string->bytes "{message}"))')
        assert result == expected

    def test_multi_block_input(self, menai):
        """CRC-32 of an input longer than the slice-by-8 stride matches the reference."""
        result = menai.evaluate(
            '(bytes-crc32 (list->bytes (map-list (lambda (i) 97) (range 0 1000))))'
        )
        assert result == 2587417091


class TestCrc32Determinism:
    """CRC-32 is a pure function of the input bytes."""

    def test_equal_inputs_equal_checksums(self, menai):
        """Two equal inputs produce equal checksums."""
        expr = '(integer=? (bytes-crc32 (string->bytes "hello")) (bytes-crc32 (string->bytes "hello")))'
        assert menai.evaluate(expr) is True

    def test_different_inputs_differ(self, menai):
        """Different inputs produce different checksums."""
        expr = '(integer=? (bytes-crc32 (string->bytes "hello")) (bytes-crc32 (string->bytes "world")))'
        assert menai.evaluate(expr) is False


class TestCrc32Typing:
    """The CRC-32 operation validates its argument type."""

    def test_non_bytes_argument_raises(self, menai):
        """Passing a non-bytes argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(bytes-crc32 "abc")')
