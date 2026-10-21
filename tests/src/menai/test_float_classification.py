"""Tests for float classification and decomposition builtins.

Covers float-nan?, float-inf?, float-finite?, float-modf, float-frexp, and
float-ldexp.  NaN and infinity are produced by float-log of zero, which returns
-inf, and by subtracting two infinities, which returns NaN; Menai has no literal
syntax for either value.
"""

import math
import pytest

from menai import MenaiEvalError
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction

_NEG_INF = "(float-log 0.0)"
_POS_INF = "(float-neg (float-log 0.0))"
_NAN = f"(float- {_POS_INF} {_POS_INF})"


class TestFloatNan:
    """float-nan? reports whether its argument is NaN."""

    def test_nan_is_nan(self, menai):
        """A NaN value is reported as NaN."""
        assert menai.evaluate(f"(float-nan? {_NAN})") is True

    def test_ordinary_float_is_not_nan(self, menai):
        """An ordinary float is not NaN."""
        assert menai.evaluate("(float-nan? 1.0)") is False
        assert menai.evaluate("(float-nan? 0.0)") is False
        assert menai.evaluate("(float-nan? -1.5)") is False

    def test_infinity_is_not_nan(self, menai):
        """An infinity is not NaN."""
        assert menai.evaluate(f"(float-nan? {_POS_INF})") is False
        assert menai.evaluate(f"(float-nan? {_NEG_INF})") is False

    def test_nan_is_not_equal_to_itself(self, menai):
        """NaN compares unequal to itself, which is why float-nan? is needed."""
        assert menai.evaluate(f"(float=? {_NAN} {_NAN})") is False

    def test_non_float_is_a_type_error(self, menai):
        """A non-float argument raises a type error."""
        for expr in ("(float-nan? 1)", '(float-nan? "x")', "(float-nan? #t)"):
            with pytest.raises(MenaiEvalError):
                menai.evaluate(expr)


class TestFloatInf:
    """float-inf? reports whether its argument is an infinity of either sign."""

    def test_positive_infinity(self, menai):
        """A positive infinity is reported as infinite."""
        assert menai.evaluate(f"(float-inf? {_POS_INF})") is True

    def test_negative_infinity(self, menai):
        """A negative infinity is reported as infinite."""
        assert menai.evaluate(f"(float-inf? {_NEG_INF})") is True

    def test_ordinary_float_is_not_infinite(self, menai):
        """An ordinary float is not infinite."""
        assert menai.evaluate("(float-inf? 1.0)") is False
        assert menai.evaluate("(float-inf? 0.0)") is False

    def test_nan_is_not_infinite(self, menai):
        """NaN is not an infinity."""
        assert menai.evaluate(f"(float-inf? {_NAN})") is False

    def test_non_float_is_a_type_error(self, menai):
        """A non-float argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(float-inf? 1)")


class TestFloatFinite:
    """float-finite? reports whether its argument is neither NaN nor infinite."""

    def test_ordinary_float_is_finite(self, menai):
        """An ordinary float is finite."""
        assert menai.evaluate("(float-finite? 1.0)") is True
        assert menai.evaluate("(float-finite? 0.0)") is True
        assert menai.evaluate("(float-finite? -1e308)") is True

    def test_infinity_is_not_finite(self, menai):
        """An infinity is not finite."""
        assert menai.evaluate(f"(float-finite? {_POS_INF})") is False
        assert menai.evaluate(f"(float-finite? {_NEG_INF})") is False

    def test_nan_is_not_finite(self, menai):
        """NaN is not finite."""
        assert menai.evaluate(f"(float-finite? {_NAN})") is False

    def test_classifications_are_exhaustive(self, menai):
        """Exactly one of nan?, inf?, and finite? holds for any float."""
        for value in ("0.0", "1.5", "-1e300", _POS_INF, _NEG_INF, _NAN):
            flags = [
                menai.evaluate(f"(float-nan? {value})"),
                menai.evaluate(f"(float-inf? {value})"),
                menai.evaluate(f"(float-finite? {value})"),
            ]
            assert flags.count(True) == 1, f"{value} classified as {flags}"

    def test_non_float_is_a_type_error(self, menai):
        """A non-float argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(float-finite? 1)")


class TestFloatModf:
    """float-modf splits a float into its fractional and integral parts."""

    def test_positive_value(self, menai):
        """A positive value splits into matching fractional and integral parts."""
        assert menai.evaluate("(float-modf 3.75)") == [0.75, 3.0]

    def test_negative_value(self, menai):
        """A negative value keeps the sign on both parts."""
        assert menai.evaluate("(float-modf -3.75)") == [-0.75, -3.0]

    def test_integral_value_has_zero_fraction(self, menai):
        """An integral value has a zero fractional part."""
        assert menai.evaluate("(float-modf 5.0)") == [0.0, 5.0]

    def test_parts_sum_to_the_original(self, menai):
        """The two parts sum back to the original value."""
        result = menai.evaluate("(float-modf 12.375)")
        assert abs(result[0] + result[1] - 12.375) < 1e-12

    def test_matches_python_modf(self, menai):
        """The result matches Python's math.modf."""
        for value in (0.5, -0.5, 1.0, -1.0, 2.25, -2.25, 1e10 + 0.5):
            result = menai.evaluate(f"(float-modf {value!r})")
            frac, whole = math.modf(value)
            assert result == [frac, whole], f"float-modf {value}"

    def test_infinity_splits_into_infinite_integral_part(self, menai):
        """An infinity has an infinite integral part and a zero fractional part."""
        result = menai.evaluate(f"(float-modf {_NEG_INF})")
        assert result[0] == 0.0
        assert result[1] == float("-inf")

    def test_nan_propagates_to_both_parts(self, menai):
        """NaN produces NaN in both parts."""
        result = menai.evaluate(f"(float-modf {_NAN})")
        assert math.isnan(result[0])
        assert math.isnan(result[1])

    def test_non_float_is_a_type_error(self, menai):
        """A non-float argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(float-modf 1)")


class TestFloatFrexp:
    """float-frexp decomposes a float into a mantissa and a binary exponent."""

    def test_power_of_two(self, menai):
        """A power of two has a mantissa of one half."""
        assert menai.evaluate("(float-frexp 8.0)") == [0.5, 4]

    def test_negative_value(self, menai):
        """A negative value has a negative mantissa."""
        assert menai.evaluate("(float-frexp -8.0)") == [-0.5, 4]

    def test_zero(self, menai):
        """Zero decomposes to a zero mantissa and a zero exponent."""
        assert menai.evaluate("(float-frexp 0.0)") == [0.0, 0]

    def test_matches_python_frexp(self, menai):
        """The result matches Python's math.frexp."""
        for value in (1.0, 2.0, 3.0, 0.5, -0.25, 1024.0, 1e-10):
            result = menai.evaluate(f"(float-frexp {value!r})")
            mantissa, exponent = math.frexp(value)
            assert result == [mantissa, exponent], f"float-frexp {value}"

    def test_mantissa_is_in_the_documented_range(self, menai):
        """A finite non-zero mantissa lies in [0.5, 1) in magnitude."""
        for value in (1.0, 3.0, 7.5, -9.25, 1e-5):
            mantissa = menai.evaluate(f"(float-frexp {value!r})")[0]
            assert 0.5 <= abs(mantissa) < 1.0, f"float-frexp {value}"

    def test_exponent_is_an_integer(self, menai):
        """The exponent is an integer, not a float."""
        assert menai.evaluate("(integer? (list-nth (float-frexp 8.0) 1))") is True

    def test_infinity(self, menai):
        """An infinity has an infinite mantissa and a zero exponent."""
        result = menai.evaluate(f"(float-frexp {_NEG_INF})")
        assert result[0] == float("-inf")
        assert result[1] == 0

    def test_non_float_is_a_type_error(self, menai):
        """A non-float argument raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(float-frexp "a")')


class TestFloatLdexp:
    """float-ldexp scales a mantissa by a power of two."""

    def test_positive_exponent(self, menai):
        """A positive exponent multiplies by a power of two."""
        assert menai.evaluate("(float-ldexp 0.5 4)") == 8.0
        assert menai.evaluate("(float-ldexp 1.0 10)") == 1024.0

    def test_negative_exponent(self, menai):
        """A negative exponent divides by a power of two."""
        assert menai.evaluate("(float-ldexp 1.0 -1)") == 0.5
        assert menai.evaluate("(float-ldexp 8.0 -3)") == 1.0

    def test_zero_exponent_is_identity(self, menai):
        """A zero exponent leaves the mantissa unchanged."""
        assert menai.evaluate("(float-ldexp 3.75 0)") == 3.75

    def test_inverse_of_frexp(self, menai):
        """ldexp of the frexp parts reproduces the original value exactly."""
        for value in (1.0, 3.0, 7.5, -9.25, 1024.0, 1e-10, 1e300):
            result = menai.evaluate(
                f"(let ((parts (float-frexp {value!r})))"
                f"  (float-ldexp (list-nth parts 0) (list-nth parts 1)))"
            )
            assert result == value, f"round trip for {value}"

    def test_scaling_is_exact_for_powers_of_two(self, menai):
        """Scaling by a power of two introduces no rounding error."""
        assert menai.evaluate("(float-ldexp 0.1 3)") == 0.1 * 8

    def test_overflow_to_infinity(self, menai):
        """An exponent beyond the representable range overflows to infinity."""
        assert menai.evaluate("(float-ldexp 1.0 1024)") == float("inf")

    def test_largest_finite_exponent(self, menai):
        """The largest finite exponent produces a finite value."""
        assert math.isfinite(menai.evaluate("(float-ldexp 1.0 1023)"))

    def test_underflow_to_zero(self, menai):
        """A sufficiently negative exponent underflows to zero."""
        assert menai.evaluate("(float-ldexp 1.0 -1075)") == 0.0

    def test_non_float_mantissa_is_a_type_error(self, menai):
        """A non-float mantissa raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(float-ldexp 1 1)")

    def test_non_integer_exponent_is_a_type_error(self, menai):
        """A non-integer exponent raises a type error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(float-ldexp 1.0 1.5)")


class TestConstantFolding:
    """A call whose arguments are all literals is folded at compile time."""

    @pytest.mark.parametrize("expression", [
        "(float-nan? 1.0)",
        "(float-inf? 1.0)",
        "(float-finite? 1.0)",
        "(float-modf 3.75)",
        "(float-frexp 8.0)",
        "(float-ldexp 0.5 4)",
    ])
    def test_literal_call_folds_to_a_constant(self, menai, expression):
        """The compiled program returns a constant, with no opcode executed."""
        code = menai.compile(expression)
        opcodes = [unpack_instruction(word).opcode for word in code.instructions]
        assert Opcode.RETURN in opcodes
        assert not any(
            opcode in opcodes for opcode in (
                Opcode.FLOAT_NAN_P, Opcode.FLOAT_INF_P, Opcode.FLOAT_FINITE_P,
                Opcode.FLOAT_MODF, Opcode.FLOAT_FREXP, Opcode.FLOAT_LDEXP,
            )
        ), f"{expression} was not folded"

    @pytest.mark.parametrize("folded,runtime", [
        ("(float-nan? 1.0)", "(let ((f (lambda (x) x))) (float-nan? (f 1.0)))"),
        ("(float-inf? 1.0)", "(let ((f (lambda (x) x))) (float-inf? (f 1.0)))"),
        ("(float-finite? 1.0)", "(let ((f (lambda (x) x))) (float-finite? (f 1.0)))"),
        ("(float-modf 3.75)", "(let ((f (lambda (x) x))) (float-modf (f 3.75)))"),
        ("(float-frexp 8.0)", "(let ((f (lambda (x) x))) (float-frexp (f 8.0)))"),
        ("(float-ldexp 0.5 4)", "(let ((f (lambda (x) x))) (float-ldexp (f 0.5) 4))"),
    ])
    def test_folded_value_matches_runtime_value(self, menai, folded, runtime):
        """The folded value equals the value computed at runtime."""
        assert menai.evaluate(folded) == menai.evaluate(runtime)
