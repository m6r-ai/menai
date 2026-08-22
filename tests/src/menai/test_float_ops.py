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
