"""Tests for the integer-gcd and integer-lcm builtins."""

import pytest

from menai import Menai, MenaiEvalError
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction


def _assert_folded_to_constant(menai: Menai, expr: str) -> None:
    """Assert that expr compiles to a constant load followed by RETURN."""
    code = menai.compile(expr)
    opcodes = [unpack_instruction(w).opcode for w in code.instructions]
    load_ops = {Opcode.LOAD_CONST, Opcode.LOAD_TRUE, Opcode.LOAD_FALSE, Opcode.LOAD_NONE}
    folded = (
        len(opcodes) == 2 and opcodes[0] in load_ops and opcodes[1] == Opcode.RETURN
    ) or (
        len(opcodes) == 1 and opcodes[0] == Opcode.RETURN
    )
    assert folded, (
        f"Expected [load, RETURN] or [RETURN] for folded expression {expr!r}, "
        f"got opcodes: {[Opcode(o).name for o in opcodes]}"
    )


class TestIntegerGcd:
    """Tests for the integer-gcd builtin."""

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd 12 8)', '4'),
        ('(integer-gcd 8 12)', '4'),
        ('(integer-gcd 17 5)', '1'),
        ('(integer-gcd 5 17)', '1'),
        ('(integer-gcd 100 75)', '25'),
        ('(integer-gcd 6 6)', '6'),
        ('(integer-gcd 1 1)', '1'),
    ])
    def test_positive_arguments(self, menai, expression, expected):
        """The gcd of two positive integers is their greatest common divisor."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd -4 6)', '2'),
        ('(integer-gcd 4 -6)', '2'),
        ('(integer-gcd -4 -6)', '2'),
        ('(integer-gcd -12 -8)', '4'),
        ('(integer-gcd -17 5)', '1'),
    ])
    def test_result_is_always_non_negative(self, menai, expression, expected):
        """The gcd is non-negative regardless of the signs of the arguments."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd 0 5)', '5'),
        ('(integer-gcd 5 0)', '5'),
        ('(integer-gcd 0 0)', '0'),
        ('(integer-gcd 0 -5)', '5'),
        ('(integer-gcd -5 0)', '5'),
    ])
    def test_zero_arguments(self, menai, expression, expected):
        """gcd(0, n) is |n|, and gcd(0, 0) is 0."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd (integer-expn 2 100) (integer-expn 2 60))', '1152921504606846976'),
        ('(integer-gcd (integer-expn 2 100) (integer* 3 (integer-expn 2 99)))', '633825300114114700748351602688'),
        ('(integer-gcd (integer-neg (integer-expn 2 100)) (integer-expn 2 60))', '1152921504606846976'),
    ])
    def test_arbitrary_precision(self, menai, expression, expected):
        """The gcd is correct for values that do not fit in a machine word."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd -9223372036854775808 2)', '2'),
        ('(integer-gcd -9223372036854775808 4)', '4'),
        ('(integer-gcd -9223372036854775808 -9223372036854775808)', '9223372036854775808'),
    ])
    def test_long_min_operand(self, menai, expression, expected):
        """A LONG_MIN operand yields the correct gcd, whose magnitude is not a long."""
        assert menai.evaluate_and_format(expression) == expected

    def test_divides_both_arguments(self, menai):
        """The gcd divides both arguments exactly."""
        expression = ('(let ((g (integer-gcd 462 1071))) '
                      '(list (integer=? (integer% 462 g) 0) '
                      '(integer=? (integer% 1071 g) 0)))')
        assert menai.evaluate_and_format(expression) == '(#t #t)'


class TestIntegerLcm:
    """Tests for the integer-lcm builtin."""

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-lcm 4 6)', '12'),
        ('(integer-lcm 6 4)', '12'),
        ('(integer-lcm 7 13)', '91'),
        ('(integer-lcm 6 6)', '6'),
        ('(integer-lcm 1 1)', '1'),
        ('(integer-lcm 5 1)', '5'),
    ])
    def test_positive_arguments(self, menai, expression, expected):
        """The lcm of two positive integers is their least common multiple."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-lcm -4 6)', '12'),
        ('(integer-lcm 4 -6)', '12'),
        ('(integer-lcm -4 -6)', '12'),
        ('(integer-lcm -7 13)', '91'),
    ])
    def test_result_is_always_non_negative(self, menai, expression, expected):
        """The lcm is non-negative regardless of the signs of the arguments."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-lcm 0 5)', '0'),
        ('(integer-lcm 5 0)', '0'),
        ('(integer-lcm 0 0)', '0'),
        ('(integer-lcm 0 -5)', '0'),
    ])
    def test_zero_arguments(self, menai, expression, expected):
        """lcm(0, n) is 0 for every n."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-lcm (integer-expn 2 100) (integer-expn 2 60))', '1267650600228229401496703205376'),
        ('(integer-lcm (integer-neg (integer-expn 2 100)) (integer-expn 2 60))', '1267650600228229401496703205376'),
    ])
    def test_arbitrary_precision(self, menai, expression, expected):
        """The lcm is correct for values that do not fit in a machine word."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-lcm -9223372036854775808 1)', '9223372036854775808'),
        ('(integer-lcm -9223372036854775808 0)', '0'),
    ])
    def test_long_min_operand(self, menai, expression, expected):
        """A LONG_MIN operand yields the correct lcm, whose magnitude is not a long."""
        assert menai.evaluate_and_format(expression) == expected

    def test_is_multiple_of_both_arguments(self, menai):
        """The lcm is an exact multiple of both arguments."""
        expression = ('(let ((m (integer-lcm 462 1071))) '
                      '(list (integer=? (integer% m 462) 0) '
                      '(integer=? (integer% m 1071) 0)))')
        assert menai.evaluate_and_format(expression) == '(#t #t)'


class TestGcdLcmRelation:
    """Tests for the identity relating gcd and lcm."""

    @pytest.mark.parametrize("a,b", [
        (4, 6),
        (12, 8),
        (17, 5),
        (100, 75),
        (1, 1),
        (-4, 6),
        (-4, -6),
        (0, 5),
        (462, 1071),
    ])
    def test_product_identity(self, menai, a, b):
        """gcd(a, b) * lcm(a, b) == |a * b| for all a and b."""
        expression = (f'(let ((g (integer-gcd {a} {b})) (m (integer-lcm {a} {b}))) '
                      f'(integer=? (integer* g m) (integer-abs (integer* {a} {b}))))')
        assert menai.evaluate_and_format(expression) == '#t'

    def test_product_identity_arbitrary_precision(self, menai):
        """gcd(a, b) * lcm(a, b) == |a * b| for values beyond machine-word range."""
        expression = ('(let ((a (integer-expn 3 80)) (b (integer-expn 3 50))) '
                      '(let ((g (integer-gcd a b)) (m (integer-lcm a b))) '
                      '(integer=? (integer* g m) (integer* a b))))')
        assert menai.evaluate_and_format(expression) == '#t'


class TestGcdLcmErrors:
    """Tests for argument validation."""

    @pytest.mark.parametrize("expression", [
        '(integer-gcd 1.0 2)',
        '(integer-gcd 1 2.0)',
        '(integer-gcd "1" 2)',
        '(integer-gcd 1)',
        '(integer-gcd 1 2 3)',
    ])
    def test_gcd_rejects_invalid_arguments(self, menai, expression):
        """A non-integer argument or a wrong arity raises an error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)

    @pytest.mark.parametrize("expression", [
        '(integer-lcm 1.0 2)',
        '(integer-lcm 1 2.0)',
        '(integer-lcm "1" 2)',
        '(integer-lcm 1)',
        '(integer-lcm 1 2 3)',
    ])
    def test_lcm_rejects_invalid_arguments(self, menai, expression):
        """A non-integer argument or a wrong arity raises an error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)


class TestGcdLcmConstantFolding:
    """Tests that constant arguments are folded at compile time."""

    @pytest.mark.parametrize("expression,expected", [
        ('(integer-gcd 12 8)', '4'),
        ('(integer-gcd -4 6)', '2'),
        ('(integer-gcd 0 0)', '0'),
        ('(integer-lcm 4 6)', '12'),
        ('(integer-lcm -4 6)', '12'),
        ('(integer-lcm 0 5)', '0'),
    ])
    def test_constant_arguments_are_folded(self, menai, expression, expected):
        """A call with constant arguments evaluates correctly and compiles to a constant."""
        assert menai.evaluate_and_format(expression) == expected
        _assert_folded_to_constant(menai, expression)

    def test_non_constant_arguments_are_not_folded(self, menai):
        """A call whose arguments are not both constants compiles to a runtime opcode."""
        code = menai.compile('(let ((x 12)) (integer-gcd x 8))')
        opcodes = {unpack_instruction(w).opcode for w in code.instructions}
        assert Opcode.INTEGER_GCD in opcodes
