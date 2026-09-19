"""Python-side checks for the deflate module.

These tests complement the in-language round-trip tests in
``menai_modules/deflate_test.menai`` by validating deflate's output against an
independent DEFLATE decoder (Python's ``zlib``).  This guards against a
symmetric bug in the Menai ``deflate``/``inflate`` pair that a round-trip
through the same implementation would not catch.
"""

import zlib

import pytest

from menai import Menai


def _hex_of(menai: Menai, text: str, mode: str) -> str:
    """Return the hex of deflate(text, mode) for the given mode."""
    expr = (
        '(let ((d (import "deflate")))'
        '  (let ((deflate (d deflate)))'
        f'    (bytes->string-hex (deflate (string->bytes "{text}") "{mode}"))))'
    )
    return menai.evaluate_raw(expr).value


def _bytes_of(menai: Menai, hex_str: str, mode: str) -> str:
    """Return the hex of deflate(list->bytes(0..255), mode)."""
    expr = (
        '(let ((d (import "deflate")))'
        '  (let ((deflate (d deflate)))'
        f'    (bytes->string-hex (deflate (string-hex->bytes "{hex_str}") "{mode}"))))'
    )
    return menai.evaluate_raw(expr).value


MODES = ["stored", "fixed", "dynamic", "auto"]

CASES = [
    "",
    "A",
    "hello",
    "hello hello hello",
    "abcabcabcabcabcabc",
    "the quick brown fox jumps over the lazy dog",
    "a" * 300,
    "ab" * 200,
    "".join(chr(97 + (i % 26)) for i in range(1000)),
    "".join("a" if i % 10 else "b" for i in range(2000)),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", CASES, ids=lambda t: f"len{len(t)}")
def test_zlib_decodes_deflate_output(menai_modules: Menai, mode: str, text: str) -> None:
    """Python's zlib decodes deflate's output back to the original text."""
    hex_str = _hex_of(menai_modules, text, mode)
    raw = bytes.fromhex(hex_str)
    decoded = zlib.decompress(raw, -15).decode("utf-8")
    assert decoded == text


@pytest.mark.parametrize("mode", MODES)
def test_zlib_decodes_all_byte_values(menai_modules: Menai, mode: str) -> None:
    """Python's zlib decodes deflate's output for every byte value."""
    hex_str = "".join(f"{b:02x}" for b in range(256))
    out_hex = _bytes_of(menai_modules, hex_str, mode)
    raw = bytes.fromhex(out_hex)
    decoded = zlib.decompress(raw, -15)
    assert decoded == bytes(range(256))


@pytest.mark.parametrize("mode", MODES)
def test_auto_is_no_larger_than_any_single_mode(menai_modules: Menai, mode: str) -> None:
    """Auto mode never produces more bytes than the given single mode."""
    text = "hello hello hello hello hello"
    auto_len = len(bytes.fromhex(_hex_of(menai_modules, text, "auto")))
    mode_len = len(bytes.fromhex(_hex_of(menai_modules, text, mode)))
    assert auto_len <= mode_len
