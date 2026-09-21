"""Tests for the bytes cryptographic hashing operations."""

import pytest

from menai import MenaiEvalError


class TestHashResultType:
    """Hash operations return bytes of the correct size."""

    @pytest.mark.parametrize("op,expected_len", [
        ("bytes-hash-sha2-256", 32),
        ("bytes-hash-sha2-512", 64),
        ("bytes-hash-sha2-512-256", 32),
        ("bytes-hash-sha3-256", 32),
    ])
    def test_result_is_bytes(self, menai, op, expected_len):
        """Each hash operation returns a bytes value of the documented size."""
        assert menai.evaluate(f'(bytes? ({op} (string->bytes "abc")))') is True
        assert menai.evaluate(f'(bytes-length ({op} (string->bytes "abc")))') == expected_len


class TestSha2_256:
    """SHA2-256 against the standard FIPS 180-4 test vectors."""

    @pytest.mark.parametrize("message,expected", [
        (
            "abc",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        (
            "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
        (
            "",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
    ])
    def test_vectors(self, menai, message, expected):
        """SHA2-256 produces the reference digest for each standard vector."""
        result = menai.evaluate(f'(bytes->string-hex (bytes-hash-sha2-256 (string->bytes "{message}")))')
        assert result == expected

    def test_million_a(self, menai):
        """SHA2-256 of one million 'a' bytes matches the reference digest."""
        result = menai.evaluate(
            '(bytes->string-hex (bytes-hash-sha2-256 (list->bytes (map-list (lambda (i) 97) (range 0 1000000)))))'
        )
        assert result == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"


class TestSha2_512:
    """SHA2-512 against the standard FIPS 180-4 test vectors."""

    @pytest.mark.parametrize("message,expected", [
        (
            "abc",
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
            "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        ),
        (
            "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
            "ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
            "8e959b75dae313da8cf4f72814fc143f8f7779c6eb9f7fa17299aeadb6889018"
            "501d289e4900f7e4331b99dec4b5433ac7d329eeb6dd26545e96e55b874be909",
        ),
        (
            "",
            "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
            "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e",
        ),
    ])
    def test_vectors(self, menai, message, expected):
        """SHA2-512 produces the reference digest for each standard vector."""
        result = menai.evaluate(f'(bytes->string-hex (bytes-hash-sha2-512 (string->bytes "{message}")))')
        assert result == expected

    def test_million_a(self, menai):
        """SHA2-512 of one million 'a' bytes matches the reference digest."""
        result = menai.evaluate(
            '(bytes->string-hex (bytes-hash-sha2-512 (list->bytes (map-list (lambda (i) 97) (range 0 1000000)))))'
        )
        assert result == (
            "e718483d0ce769644e2e42c7bc15b4638e1f98b13b2044285632a803afa973eb"
            "de0ff244877ea60a4cb0432ce577c31beb009c5c2c49aa2e4eadb217ad8cc09b"
        )


class TestSha2_512_256:
    """SHA2-512/256 against the standard FIPS 180-4 test vectors."""

    @pytest.mark.parametrize("message,expected", [
        (
            "abc",
            "53048e2681941ef99b2e29b76b4c7dabe4c2d0c634fc6d46e0e2f13107e7af23",
        ),
        (
            "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
            "ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
            "3928e184fb8690f840da3988121d31be65cb9d3ef83ee6146feac861e19b563a",
        ),
        (
            "",
            "c672b8d1ef56ed28ab87c3622c5114069bdd3ad7b8f9737498d0c01ecef0967a",
        ),
    ])
    def test_vectors(self, menai, message, expected):
        """SHA2-512/256 produces the reference digest for each standard vector."""
        result = menai.evaluate(f'(bytes->string-hex (bytes-hash-sha2-512-256 (string->bytes "{message}")))')
        assert result == expected


class TestSha3_256:
    """SHA3-256 against the standard FIPS 202 test vectors."""

    def test_empty(self, menai):
        """SHA3-256 of the empty message matches the reference digest."""
        result = menai.evaluate('(bytes->string-hex (bytes-hash-sha3-256 (string->bytes "")))')
        assert result == "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a"

    def test_200_bytes_of_a3(self, menai):
        """SHA3-256 of 200 bytes of 0xa3 matches the reference digest."""
        result = menai.evaluate(
            '(bytes->string-hex (bytes-hash-sha3-256 (list->bytes (map-list (lambda (i) 163) (range 0 200)))))'
        )
        assert result == "79f38adec5c20307a98ef76e8324afbfd46cfd81b22e3973c65fa1bd9de31787"


class TestHashDeterminism:
    """Hashing is a pure function of the input bytes."""

    @pytest.mark.parametrize("op", [
        "bytes-hash-sha2-256",
        "bytes-hash-sha2-512",
        "bytes-hash-sha2-512-256",
        "bytes-hash-sha3-256",
    ])
    def test_equal_inputs_equal_digests(self, menai, op):
        """Two equal inputs produce equal digests."""
        expr = f'(bytes=? ({op} (string->bytes "hello")) ({op} (string->bytes "hello")))'
        assert menai.evaluate(expr) is True

    @pytest.mark.parametrize("op", [
        "bytes-hash-sha2-256",
        "bytes-hash-sha2-512",
        "bytes-hash-sha2-512-256",
        "bytes-hash-sha3-256",
    ])
    def test_different_inputs_differ(self, menai, op):
        """Different inputs produce different digests."""
        expr = f'(bytes=? ({op} (string->bytes "hello")) ({op} (string->bytes "world")))'
        assert menai.evaluate(expr) is False


class TestHashTyping:
    """Hash operations validate their argument type."""

    @pytest.mark.parametrize("op", [
        "bytes-hash-sha2-256",
        "bytes-hash-sha2-512",
        "bytes-hash-sha2-512-256",
        "bytes-hash-sha3-256",
    ])
    def test_non_bytes_argument_raises(self, menai, op):
        """Passing a non-bytes argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(f'({op} "abc")')
