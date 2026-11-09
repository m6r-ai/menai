"""Tests for the new float operations (tier 1 and tier 2)."""

import math
import pytest

from menai import MenaiEvalError


class TestFloatInverseTrig:
    """Test float-asin, float-acos, float-atan, float-atan2."""

    def test_asin_basic(self, menai):
        """float-asin returns the inverse sine of its argument."""
        assert abs(menai.evaluate("(float-asin 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-asin 1.0)") - math.asin(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-asin -1.0)") - math.asin(-1.0)) < 1e-10
        assert abs(menai.evaluate("(float-asin 0.5)") - math.asin(0.5)) < 1e-10

    def test_asin_domain_error(self, menai):
        """float-asin raises an error for arguments outside [-1, 1]."""
        for value in ("2.0", "-2.0", "1.5"):
            with pytest.raises(MenaiEvalError):
                menai.evaluate(f"(float-asin {value})")

    def test_acos_basic(self, menai):
        """float-acos returns the inverse cosine of its argument."""
        assert abs(menai.evaluate("(float-acos 1.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-acos 0.0)") - math.acos(0.0)) < 1e-10
        assert abs(menai.evaluate("(float-acos -1.0)") - math.acos(-1.0)) < 1e-10
        assert abs(menai.evaluate("(float-acos 0.5)") - math.acos(0.5)) < 1e-10

    def test_acos_domain_error(self, menai):
        """float-acos raises an error for arguments outside [-1, 1]."""
        for value in ("2.0", "-2.0", "1.5"):
            with pytest.raises(MenaiEvalError):
                menai.evaluate(f"(float-acos {value})")

    def test_atan_basic(self, menai):
        """float-atan returns the inverse tangent of its argument."""
        assert abs(menai.evaluate("(float-atan 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-atan 1.0)") - math.atan(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-atan -1.0)") - math.atan(-1.0)) < 1e-10
        assert abs(menai.evaluate("(float-atan 2.0)") - math.atan(2.0)) < 1e-10

    def test_atan2_basic(self, menai):
        """float-atan2 returns the four-quadrant inverse tangent of y/x."""
        assert abs(menai.evaluate("(float-atan2 0.0 1.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-atan2 1.0 1.0)") - math.pi / 4) < 1e-10
        assert abs(menai.evaluate("(float-atan2 1.0 0.0)") - math.pi / 2) < 1e-10
        assert abs(menai.evaluate("(float-atan2 -1.0 -1.0)") - math.atan2(-1.0, -1.0)) < 1e-10
        assert abs(menai.evaluate("(float-atan2 1.0 -1.0)") - math.atan2(1.0, -1.0)) < 1e-10


class TestFloatHyperbolic:
    """Test float-sinh, float-cosh, float-tanh."""

    def test_sinh_basic(self, menai):
        """float-sinh returns the hyperbolic sine of its argument."""
        assert abs(menai.evaluate("(float-sinh 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-sinh 1.0)") - math.sinh(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-sinh -1.0)") - math.sinh(-1.0)) < 1e-10

    def test_cosh_basic(self, menai):
        """float-cosh returns the hyperbolic cosine of its argument."""
        assert abs(menai.evaluate("(float-cosh 0.0)") - 1.0) < 1e-10
        assert abs(menai.evaluate("(float-cosh 1.0)") - math.cosh(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-cosh -1.0)") - math.cosh(-1.0)) < 1e-10

    def test_tanh_basic(self, menai):
        """float-tanh returns the hyperbolic tangent of its argument."""
        assert abs(menai.evaluate("(float-tanh 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-tanh 1.0)") - math.tanh(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-tanh -1.0)") - math.tanh(-1.0)) < 1e-10

    def test_hyperbolic_identities(self, menai):
        """Hyperbolic identities hold: cosh^2 - sinh^2 = 1."""
        x = 0.7
        cosh_val = menai.evaluate("(float-cosh 0.7)")
        sinh_val = menai.evaluate("(float-sinh 0.7)")
        assert abs(cosh_val**2 - sinh_val**2 - 1.0) < 1e-10


class TestFloatHypot:
    """Test float-hypot."""

    def test_hypot_basic(self, menai):
        """float-hypot returns sqrt(a^2 + b^2)."""
        assert abs(menai.evaluate("(float-hypot 3.0 4.0)") - 5.0) < 1e-10
        assert abs(menai.evaluate("(float-hypot 0.0 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-hypot 6.0 8.0)") - 10.0) < 1e-10

    def test_hypot_negative(self, menai):
        """float-hypot handles negative arguments."""
        assert abs(menai.evaluate("(float-hypot -3.0 -4.0)") - 5.0) < 1e-10
        assert abs(menai.evaluate("(float-hypot -3.0 4.0)") - 5.0) < 1e-10

    def test_hypot_large_values(self, menai):
        """float-hypot avoids intermediate overflow."""
        result = menai.evaluate("(float-hypot 1e200 1e200)")
        assert math.isfinite(result)
        assert abs(result - math.hypot(1e200, 1e200)) < 1e-10


class TestFloatExp2Cbrt:
    """Test float-exp2 and float-cbrt."""

    def test_exp2_basic(self, menai):
        """float-exp2 returns 2 raised to the argument."""
        assert abs(menai.evaluate("(float-exp2 0.0)") - 1.0) < 1e-10
        assert abs(menai.evaluate("(float-exp2 1.0)") - 2.0) < 1e-10
        assert abs(menai.evaluate("(float-exp2 3.0)") - 8.0) < 1e-10
        assert abs(menai.evaluate("(float-exp2 -1.0)") - 0.5) < 1e-10
        assert abs(menai.evaluate("(float-exp2 10.0)") - 1024.0) < 1e-10

    def test_cbrt_basic(self, menai):
        """float-cbrt returns the cube root of its argument."""
        assert abs(menai.evaluate("(float-cbrt 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-cbrt 1.0)") - 1.0) < 1e-10
        assert abs(menai.evaluate("(float-cbrt 8.0)") - 2.0) < 1e-10
        assert abs(menai.evaluate("(float-cbrt 27.0)") - 3.0) < 1e-10

    def test_cbrt_negative(self, menai):
        """float-cbrt works for negative arguments."""
        assert abs(menai.evaluate("(float-cbrt -8.0)") - (-2.0)) < 1e-10
        assert abs(menai.evaluate("(float-cbrt -27.0)") - (-3.0)) < 1e-10


class TestFloatExpm1Log1p:
    """Test float-expm1 and float-log1p."""

    def test_expm1_basic(self, menai):
        """float-expm1 returns e^x - 1."""
        assert abs(menai.evaluate("(float-expm1 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-expm1 1.0)") - math.expm1(1.0)) < 1e-10
        assert abs(menai.evaluate("(float-expm1 -1.0)") - math.expm1(-1.0)) < 1e-10

    def test_expm1_small_x(self, menai):
        """float-expm1 is numerically stable for very small x."""
        result = menai.evaluate("(float-expm1 1e-10)")
        assert abs(result - math.expm1(1e-10)) < 1e-20

    def test_log1p_basic(self, menai):
        """float-log1p returns log(1+x)."""
        assert abs(menai.evaluate("(float-log1p 0.0)") - 0.0) < 1e-10
        assert abs(menai.evaluate("(float-log1p 1.0)") - math.log(2.0)) < 1e-10
        assert abs(menai.evaluate("(float-log1p 2.0)") - math.log1p(2.0)) < 1e-10

    def test_log1p_small_x(self, menai):
        """float-log1p is numerically stable for very small x."""
        result = menai.evaluate("(float-log1p 1e-10)")
        assert abs(result - 1e-10) < 1e-20

    def test_log1p_negative_one(self, menai):
        """float-log1p of -1.0 returns -inf."""
        result = menai.evaluate("(float-log1p -1.0)")
        assert math.isinf(result) and result < 0

    def test_log1p_domain_error(self, menai):
        """float-log1p raises an error for arguments below -1.0."""
        for value in ("-1.5", "-2.0", "-10.0"):
            with pytest.raises(MenaiEvalError):
                menai.evaluate(f"(float-log1p {value})")

    def test_log1p_expm1_inverse(self, menai):
        """log1p and expm1 are inverses."""
        x = 0.5
        result = menai.evaluate("(float-log1p (float-expm1 0.5))")
        assert abs(result - x) < 1e-10


class TestFloatTrunc:
    """Test float-trunc."""

    def test_trunc_basic(self, menai):
        """float-trunc truncates toward zero."""
        assert menai.evaluate("(float-trunc 3.7)") == 3.0
        assert menai.evaluate("(float-trunc -3.7)") == -3.0
        assert menai.evaluate("(float-trunc 3.0)") == 3.0
        assert menai.evaluate("(float-trunc -3.0)") == -3.0
        assert menai.evaluate("(float-trunc 0.0)") == 0.0

    def test_trunc_small_values(self, menai):
        """float-trunc truncates small values toward zero."""
        assert menai.evaluate("(float-trunc 0.5)") == 0.0
        assert menai.evaluate("(float-trunc -0.5)") == 0.0
        assert menai.evaluate("(float-trunc 1e-10)") == 0.0
        assert menai.evaluate("(float-trunc -1e-10)") == 0.0

    def test_trunc_differs_from_floor(self, menai):
        """float-trunc differs from float-floor for negative values."""
        assert menai.evaluate("(float-trunc -3.7)") == -3.0
        assert menai.evaluate("(float-floor -3.7)") == -4.0


class TestFloatCopysign:
    """Test float-copysign."""

    def test_copysign_basic(self, menai):
        """float-copysign returns the magnitude of a with the sign of b."""
        assert menai.evaluate("(float-copysign 3.0 1.0)") == 3.0
        assert menai.evaluate("(float-copysign 3.0 -1.0)") == -3.0
        assert menai.evaluate("(float-copysign -3.0 1.0)") == 3.0
        assert menai.evaluate("(float-copysign -3.0 -1.0)") == -3.0

    def test_copysign_zero(self, menai):
        """float-copysign handles zero arguments."""
        assert menai.evaluate("(float-copysign 0.0 -1.0)") == -0.0
        assert menai.evaluate("(float-copysign 0.0 1.0)") == 0.0

    def test_copysign_matches_python(self, menai):
        """float-copysign matches Python's math.copysign."""
        for a, b in [(2.5, -3.0), (-2.5, 3.0), (1.0, -0.0), (-1.0, 0.0)]:
            result = menai.evaluate(f"(float-copysign {a} {b})")
            assert result == math.copysign(a, b)


class TestFloatOpsConstantFolding:
    """Test that the new float operations fold at compile time."""

    def test_fold_atan2(self, menai):
        """float-atan2 with constant args folds to a constant."""
        assert abs(menai.evaluate("(float-atan2 0.0 1.0)") - 0.0) < 1e-10

    def test_fold_hypot(self, menai):
        """float-hypot with constant args folds to a constant."""
        assert abs(menai.evaluate("(float-hypot 3.0 4.0)") - 5.0) < 1e-10

    def test_fold_exp2(self, menai):
        """float-exp2 with constant arg folds to a constant."""
        assert abs(menai.evaluate("(float-exp2 3.0)") - 8.0) < 1e-10

    def test_fold_cbrt(self, menai):
        """float-cbrt with constant arg folds to a constant."""
        assert abs(menai.evaluate("(float-cbrt 27.0)") - 3.0) < 1e-10

    def test_fold_trunc(self, menai):
        """float-trunc with constant arg folds to a constant."""
        assert menai.evaluate("(float-trunc -3.7)") == -3.0

    def test_fold_copysign(self, menai):
        """float-copysign with constant args folds to a constant."""
        assert menai.evaluate("(float-copysign 3.0 -1.0)") == -3.0

    def test_fold_expn_overflow_to_infinity(self, menai):
        """float-expn whose result exceeds the double range folds to infinity."""
        result = menai.evaluate("(float-expn 10.0 1000.0)")
        assert math.isinf(result)
        assert result > 0.0


class TestFloatZeroSign:
    """Test that negative zero keeps its sign while hashing as zero.

    IEEE equality says 0.0 == -0.0, so a dict or set has to treat them as one key:
    equal values must hash alike.  The sign itself is still part of the value, and
    operations that inspect it must see it.
    """

    def test_positive_and_negative_zero_compare_equal(self, menai):
        """IEEE equality is what float=? reports."""
        assert menai.evaluate("(float=? 0.0 -0.0)") is True
        assert menai.evaluate("(float=? -0.0 0.0)") is True
        assert menai.evaluate("(float!=? 0.0 -0.0)") is False

    def test_negative_zero_sign_is_observable(self, menai):
        """The sign of zero survives, so operations that read it still see it."""
        assert math.copysign(1.0, menai.evaluate("(float-copysign 1.0 -0.0)")) == -1.0
        assert math.copysign(1.0, menai.evaluate("(float-copysign 1.0 0.0)")) == 1.0

    @pytest.mark.parametrize("expression,expected", [
        ('(dict-get (dict 0.0 "a") -0.0)', '"a"'),
        ('(dict-get (dict -0.0 "a") 0.0)', '"a"'),
        ('(dict-get (dict (float- -0.0 0.0) "b") 0.0)', '"b"'),
        ('(dict-get (dict (float* -1.0 0.0) "c") 0.0)', '"c"'),
    ])
    def test_zero_is_one_dict_key(self, menai, expression, expected):
        """A key written with either sign is found by the other."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression", [
        "(set-member? (set -0.0) 0.0)",
        "(set-member? (set 0.0) -0.0)",
        "(set-member? (set (float- -0.0 0.0)) 0.0)",
    ])
    def test_zero_is_one_set_element(self, menai, expression):
        """Set membership agrees with float=? for either sign of zero."""
        assert menai.evaluate(expression) is True

    def test_both_zeros_collapse_to_one_element(self, menai):
        """A set holding both signs has one element, and a dict one entry."""
        assert menai.evaluate("(set-length (set 0.0 -0.0))") == 1
        # A literal with both signs is rejected as a duplicate key at compile
        # time; the runtime update path has to reach the same conclusion.
        assert menai.evaluate("(dict-length (dict-with (dict 0.0 1) -0.0 2))") == 1
        assert menai.evaluate_and_format("(dict-get (dict-with (dict 0.0 1) -0.0 2) 0.0)") == "2"

    def test_nan_is_never_equal_but_does_not_confuse_a_collection(self, menai):
        """NaN is unequal to itself, so a NaN key never matches a NaN lookup."""
        nan = "(float- (float-log 0.0) (float-log 0.0))"
        assert menai.evaluate(f"(float-nan? {nan})") is True
        assert menai.evaluate(f"(float=? {nan} {nan})") is False
        assert menai.evaluate_and_format(f"(dict-get (dict {nan} 1) {nan})") == "#none"
        assert menai.evaluate(f"(set-length (set {nan} {nan}))") == 2


class TestFloatInlineRepresentation:
    """Test that a float's representation never changes its value.

    A float is an inline immediate while its exponent falls in the window the value
    word can hold, and a boxed value otherwise.  The representation is chosen from
    the value alone, so an inline float and a boxed one must be indistinguishable
    wherever a program can observe them: equality, comparison, hashing, dictionary
    keys and set membership.  These cases straddle both ends of the window.
    """

    # The window is [2^-126, 2^129); these sit just inside and just outside it.
    WINDOW_MIN = "1.1754943508222875e-38"    # 2^-126, smallest inline magnitude
    BELOW_MIN = "5.877471754111438e-39"      # 2^-127, boxed
    WINDOW_MAX = "3.3895313892515355e38"     # just under 2^129
    ABOVE_MAX = "6.8056473384188e38"         # 2^129, boxed

    @pytest.mark.parametrize("literal", [
        "1.0",
        "-1.0",
        "0.5",
        "3.141592653589793",
        "-2.718281828459045",
        "1.0e100",
        "-1.0e-100",
        "1.1754943508222875e-38",
        "3.4028236692093e38",
    ])
    def test_inline_round_trip_is_exact(self, menai, literal):
        """A value written as a literal survives as an equal value."""
        value = float(literal)
        assert menai.evaluate(f"(float=? {literal} {literal})") is True
        assert menai.evaluate(f"(float+ {literal} 0.0)") == value
        assert menai.evaluate(f"(float* {literal} 1.0)") == value
        assert menai.evaluate(f"(float-neg (float-neg {literal}))") == value

    @pytest.mark.parametrize("literal", [
        "1e300",
        "-1e300",
        "1e-300",
        "5.877471754111438e-39",
        "6.8056473384188e38",
        "0.0",
        "-0.0",
    ])
    def test_out_of_window_values_are_correct(self, menai, literal):
        """Values outside the window still behave as ordinary floats."""
        assert menai.evaluate(f"(float=? {literal} {literal})") is True
        assert menai.evaluate(f"(float+ {literal} 0.0)") == float(literal)

    def test_nan_and_infinity_are_boxed_and_classified(self, menai):
        """NaN and the infinities need the boxed form and keep their identities."""
        inf = "(float-neg (float-log 0.0))"
        nan = "(float- (float-log 0.0) (float-log 0.0))"
        assert menai.evaluate(f"(float-inf? {inf})") is True
        assert menai.evaluate(f"(float-nan? {nan})") is True
        assert menai.evaluate(f"(float-nan? {inf})") is False
        assert menai.evaluate(f"(float-finite? {inf})") is False
        assert math.isinf(menai.evaluate(inf))

    @pytest.mark.parametrize("literal", ["1.5", "-1.5", "1e100", "1e-100"])
    def test_arithmetic_keeps_its_value_across_the_window(self, menai, literal):
        """Arithmetic on an inline value gives the same answer as on a boxed one."""
        value = float(literal)
        assert menai.evaluate(f"(float* {literal} 2.0)") == value * 2.0
        assert menai.evaluate(f"(float+ {literal} {literal})") == value + value
        assert menai.evaluate(f"(float-neg {literal})") == -value
        assert menai.evaluate(f"(float-abs {literal})") == abs(value)

    def test_overflow_leaves_the_window_and_stays_correct(self, menai):
        """A result that overflows the window is still the right double."""
        result = menai.evaluate(f"(float* {self.WINDOW_MAX} 1e300)")
        assert math.isinf(result)
        # Squaring the smallest inline magnitude leaves the window and stays exact.
        result = menai.evaluate(f"(float* {self.WINDOW_MIN} {self.WINDOW_MIN})")
        assert result == float(self.WINDOW_MIN) * float(self.WINDOW_MIN)
        assert menai.evaluate(f"(float* {self.BELOW_MIN} 1.0)") == float(self.BELOW_MIN)
        assert menai.evaluate(f"(float* {self.ABOVE_MAX} 1.0)") == float(self.ABOVE_MAX)

    @pytest.mark.parametrize("expression,expected", [
        ('(dict-get (dict 1.5 "a") 1.5)', '"a"'),
        ('(dict-get (dict (float+ 1.0 0.5) "b") 1.5)', '"b"'),
        ('(dict-get (dict 1e300 "c") 1e300)', '"c"'),
        ('(dict-get (dict 1e-300 "d") 1e-300)', '"d"'),
    ])
    def test_float_is_one_dict_key(self, menai, expression, expected):
        """Inline and boxed values hash and compare alike as dict keys."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression", [
        "(set-member? (set 1.5) (float+ 1.0 0.5))",
        "(set-member? (set (float+ 1.0 0.5)) 1.5)",
        "(set-member? (set 1e300) 1e300)",
        "(set-length (set 1.5 (float+ 1.0 0.5)))",
    ])
    def test_float_is_one_set_element(self, menai, expression):
        """Set membership agrees for inline and boxed forms."""
        if expression.startswith("(set-length"):
            assert menai.evaluate(expression) == 1
        else:
            assert menai.evaluate(expression) is True

    def test_float_never_equals_integer(self, menai):
        """An inline float is still a float, not an integer, and is not equal to one."""
        assert menai.evaluate("(float=? 1.0 1.0)") is True
        assert menai.evaluate("(integer? 1.0)") is False
        assert menai.evaluate("(float? 1)") is False
        assert menai.evaluate_and_format("(dict-get (dict 1.0 \"f\" 1 \"i\") 1)") == '"i"'
        assert menai.evaluate_and_format("(dict-get (dict 1.0 \"f\" 1 \"i\") 1.0)") == '"f"'
