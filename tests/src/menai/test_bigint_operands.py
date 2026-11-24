"""Tests for bigint operands at the fixnum boundary.

An integer is a fixnum immediate while its value fits the payload of the tagged
word, and a boxed bignum outside it.  Opcodes that take an integer split into two
classes by what the operand means.

An operand that carries a *value* must accept either representation: ``match``
arms, ``range`` bounds, a ``float-ldexp`` exponent and a ``function-accepts?``
arity are all compared or counted, and a bigint is a legitimate argument to each.

An operand that carries a *size* can never be satisfied by a bigint, because it
exceeds every length the machine can hold: an index, an offset, a slice bound, a
radix, a codepoint or a shift count.  Those opcodes report the parameter's own
error, and the diagnosis does not depend on which representation the value
arrived in.
"""

import pytest

from menai import MenaiEvalError

FIXNUM_MAX = 1152921504606846975  # 2^60 - 1, the largest fixnum immediate
FIXNUM_MIN = -1152921504606846976  # -2^60, the smallest fixnum immediate
BIG = 1152921504606846976  # 2^60, the smallest positive bignum
BIG_NEG = -1152921504606846977  # the smallest bignum below FIXNUM_MIN


def runtime_bignum(value: int) -> str:
    """Build an expression yielding a bigint computed at run time.

    A literal operand can be folded at compile time, which would exercise the
    constant folder rather than the opcode under test.  Reading the value back
    out of a dict keeps the operand opaque to the folder.
    """
    return f'(dict-get (dict "n" {value}) "n")'


class TestValueOperandsAcceptBigints:
    """Opcodes that take an integer value accept a bigint operand."""

    @pytest.mark.parametrize("scrutinee", [BIG, BIG + 1])
    def test_match_selects_the_arm_for_a_bigint_scrutinee(self, menai, scrutinee):
        """A dense bigint arm set matches a bigint scrutinee on its own arm."""
        expression = (
            f"(match {runtime_bignum(scrutinee)}"
            f" ({BIG} 'first)"
            f" ({BIG + 1} 'second)"
            " (_ 'none))"
        )

        expected = "first" if scrutinee == BIG else "second"
        assert menai.evaluate_and_format(expression) == expected

    def test_match_falls_through_for_a_bigint_outside_the_arm_set(self, menai):
        """A bigint scrutinee outside every arm takes the default arm."""
        expression = (
            f"(match {runtime_bignum(BIG + 5)}"
            f" ({BIG} 'first)"
            f" ({BIG + 1} 'second)"
            " (_ 'none))"
        )

        assert menai.evaluate_and_format(expression) == "none"

    def test_range_ascends_between_bigint_bounds(self, menai):
        """Range counts from a bigint start to a bigint end."""
        expression = f"(range {runtime_bignum(BIG)} {BIG + 2})"

        assert menai.evaluate_and_format(expression) == f"({BIG} {BIG + 1})"

    def test_range_descends_between_bigint_bounds(self, menai):
        """Range honours a negative step between bigint bounds."""
        expression = f"(range {runtime_bignum(BIG + 3)} {BIG} -1)"

        assert menai.evaluate_and_format(expression) == f"({BIG + 3} {BIG + 2} {BIG + 1})"

    def test_ldexp_with_a_bigint_exponent_overflows_to_infinity(self, menai):
        """A huge positive exponent scales the mantissa up to infinity."""
        expression = f"(float-ldexp 0.5 {runtime_bignum(BIG)})"

        assert menai.evaluate(expression) == float("inf")

    def test_ldexp_with_a_negative_bigint_exponent_underflows_to_zero(self, menai):
        """A huge negative exponent scales the mantissa down to zero."""
        expression = f"(float-ldexp 0.5 {runtime_bignum(BIG_NEG)})"

        assert menai.evaluate(expression) == 0.0

    def test_variadic_function_accepts_a_bigint_arity(self, menai):
        """A variadic function accepts any arity, however large."""
        expression = f"(function-accepts? integer+ {runtime_bignum(BIG)})"

        assert menai.evaluate(expression) is True

    def test_fixed_arity_function_rejects_a_bigint_arity(self, menai):
        """A fixed-arity function accepts no arity other than its own."""
        expression = f"(function-accepts? integer-abs {runtime_bignum(BIG)})"

        assert menai.evaluate(expression) is False


class TestSizeOperandsRejectBigints:
    """Opcodes that take a size reject a bigint with their own error."""

    @pytest.mark.parametrize("expression,message", [
        (f"(vector-nth (vector 1 2 3) {runtime_bignum(BIG)})", "index out of range"),
        (f"(list-nth (list 1 2 3) {runtime_bignum(BIG)})", "index out of range"),
        ('(string-nth "abc" ' + runtime_bignum(BIG) + ")", "index out of range"),
        ("(bytes-nth (string->bytes \"abc\") " + runtime_bignum(BIG) + ")", "offset out of bounds"),
        ('(bytes-read-u64-be (string->bytes "abcdefgh") ' + runtime_bignum(BIG) + ")", "offset out of bounds"),
        ('(string-slice "abcdef" ' + runtime_bignum(BIG) + " 2)", "slice start index out of range"),
        ('(string-slice "abcdef" 1 ' + runtime_bignum(BIG) + ")", "slice end index out of range"),
        ("(vector-slice (vector 1 2 3) " + runtime_bignum(BIG) + ")", "offset out of bounds"),
        ("(list-slice (list 1 2 3) " + runtime_bignum(BIG) + ")", "slice start index out of range"),
        (f"(integer->string 255 {runtime_bignum(BIG)})", "radix must be 2, 8, 10, or 16"),
        (f'(string->integer "ff" {runtime_bignum(BIG)})', "radix must be 2, 8, 10, or 16"),
        (f"(integer-codepoint->string {runtime_bignum(BIG)})", "invalid Unicode scalar value"),
        (f"(integer-bit-shift-left 1 {runtime_bignum(BIG)})", "shift amount too large"),
        (f"(integer-bit-shift-right 1 {runtime_bignum(BIG)})", "shift amount too large"),
    ])
    def test_size_operand_reports_its_own_error(self, menai, expression, message):
        """A bigint size operand is refused by the parameter's own diagnostic."""
        with pytest.raises(MenaiEvalError, match=message):
            menai.evaluate(expression)

    @pytest.mark.parametrize("size", [FIXNUM_MAX, FIXNUM_MIN, BIG, BIG_NEG])
    def test_radix_diagnosis_ignores_the_representation(self, menai, size):
        """An out-of-range radix is reported the same way in either representation."""
        expression = f"(integer->string 255 {runtime_bignum(size)})"

        with pytest.raises(MenaiEvalError, match="radix must be 2, 8, 10, or 16"):
            menai.evaluate(expression)

    @pytest.mark.parametrize("size", [FIXNUM_MAX, BIG, BIG_NEG])
    def test_codepoint_diagnosis_ignores_the_representation(self, menai, size):
        """An out-of-range codepoint is reported the same way in either representation."""
        expression = f"(integer-codepoint->string {runtime_bignum(size)})"

        with pytest.raises(MenaiEvalError, match="invalid Unicode scalar value"):
            menai.evaluate(expression)

    @pytest.mark.parametrize("size", [BIG, BIG_NEG])
    def test_bigint_shift_count_is_refused_before_allocating(self, menai, size):
        """A shift count too large for the payload is refused as a shift error.

        A shift count has no fixed domain: a large fixnum count is a real request
        for a proportionally large result, and the allocator reports it if the
        result cannot be built.  A count that is not even a fixnum can never be
        satisfied, so it is refused before any allocation is attempted.
        """
        expression = f"(integer-bit-shift-left 1 {runtime_bignum(size)})"

        with pytest.raises(MenaiEvalError, match="shift amount too large"):
            menai.evaluate(expression)


class TestFixnumSizedOperandsStillWork:
    """A size operand inside the fixnum range keeps working."""

    def test_index_and_slice_with_fixnum_sizes(self, menai):
        """Representation alone never changes whether a valid size is accepted."""
        assert menai.evaluate("(vector-nth (vector 10 20 30) 1)") == 20
        assert menai.evaluate_and_format('(string-slice "abcdef" 1 3)') == '"bc"'
        assert menai.evaluate_and_format(f"(integer->string 255 {16})") == '"ff"'
        assert menai.evaluate("(integer-bit-shift-left 1 3)") == 8
