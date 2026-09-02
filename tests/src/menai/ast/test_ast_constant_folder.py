"""
Regression tests for MenaiASTConstantFolder through the full pipeline.

Each test compiles a Menai expression whose arguments are all compile-time
literals and asserts two things:

1. The expression evaluates to the correct value (correctness).
2. The compiled bytecode contains only a single load instruction (LOAD_CONST,
   LOAD_TRUE, LOAD_FALSE, or LOAD_NONE) followed by RETURN — no runtime
   opcode for the operation — proving the constant folder actually fired.

Without the bytecode check a broken folder that silently falls through to
runtime would still pass, since the runtime produces the same answer.
"""

import pytest
from menai import Menai
from menai.bytecode.menai_bytecode import (
    Opcode, unpack_instruction,
)


@pytest.fixture
def menai():
    return Menai()


def _assert_folded_to_constant(menai: Menai, expr: str) -> None:
    """Compile expr and assert it was folded to a load + RETURN.

    The load may be LOAD_CONST (integers, floats, strings, etc.),
    LOAD_TRUE, LOAD_FALSE, or LOAD_NONE depending on the folded value type.

    This proves the constant folder fired: if the operation were still
    present as a runtime opcode, there would be more than two instructions.
    """
    code = menai.compile(expr)
    instrs = [unpack_instruction(w) for w in code.instructions]
    opcodes = [i.opcode for i in instrs]
    load_ops = {Opcode.LOAD_CONST, Opcode.LOAD_TRUE, Opcode.LOAD_FALSE, Opcode.LOAD_NONE}
    assert len(opcodes) == 2 and opcodes[0] in load_ops and opcodes[1] == Opcode.RETURN, (
        f"Expected [load, RETURN] for folded expression {expr!r}, "
        f"got opcodes: {[Opcode(o).name for o in opcodes]}"
    )


class TestConstantFolding:
    def test_boolean_not(self, menai):
        assert menai.evaluate("(boolean-not #t)") is False
        _assert_folded_to_constant(menai, "(boolean-not #t)")

    def test_integer_add(self, menai):
        assert menai.evaluate("(integer+ 3 4)") == 7
        _assert_folded_to_constant(menai, "(integer+ 3 4)")

    def test_integer_eq(self, menai):
        assert menai.evaluate("(integer=? 5 5)") is True
        _assert_folded_to_constant(menai, "(integer=? 5 5)")

    def test_integer_neq(self, menai):
        assert menai.evaluate("(integer!=? 3 4)") is True
        _assert_folded_to_constant(menai, "(integer!=? 3 4)")

    def test_float_mul(self, menai):
        assert menai.evaluate("(float* 2.0 3.0)") == 6.0
        _assert_folded_to_constant(menai, "(float* 2.0 3.0)")

    def test_float_sqrt(self, menai):
        assert menai.evaluate("(float-sqrt 9.0)") == 3.0
        _assert_folded_to_constant(menai, "(float-sqrt 9.0)")

    def test_complex_add(self, menai):
        assert menai.evaluate("(complex+ 1+2j 3+4j)") == (4+6j)
        _assert_folded_to_constant(menai, "(complex+ 1+2j 3+4j)")

    def test_string_eq(self, menai):
        assert menai.evaluate('(string=? "hello" "hello")') is True
        _assert_folded_to_constant(menai, '(string=? "hello" "hello")')

    def test_string_length(self, menai):
        assert menai.evaluate('(string-length "hello")') == 5
        _assert_folded_to_constant(menai, '(string-length "hello")')

    def test_string_lt(self, menai):
        assert menai.evaluate('(string<? "abc" "abd")') is True
        _assert_folded_to_constant(menai, '(string<? "abc" "abd")')

    def test_string_gt(self, menai):
        assert menai.evaluate('(string>? "abd" "abc")') is True
        _assert_folded_to_constant(menai, '(string>? "abd" "abc")')

    def test_string_lte(self, menai):
        assert menai.evaluate('(string<=? "abc" "abc")') is True
        _assert_folded_to_constant(menai, '(string<=? "abc" "abc")')

    def test_string_gte(self, menai):
        assert menai.evaluate('(string>=? "abc" "abc")') is True
        _assert_folded_to_constant(menai, '(string>=? "abc" "abc")')

    def test_string_concat(self, menai):
        assert menai.evaluate('(string-concat "foo" "bar")') == "foobar"
        _assert_folded_to_constant(menai, '(string-concat "foo" "bar")')

    def test_string_ref(self, menai):
        assert menai.evaluate('(string-ref "hello" 1)') == "e"
        _assert_folded_to_constant(menai, '(string-ref "hello" 1)')

    def test_string_slice(self, menai):
        assert menai.evaluate('(string-slice "hello" 1 4)') == "ell"
        _assert_folded_to_constant(menai, '(string-slice "hello" 1 4)')

    def test_string_prefix_true(self, menai):
        assert menai.evaluate('(string-prefix? "hello world" "hello")') is True
        _assert_folded_to_constant(menai, '(string-prefix? "hello world" "hello")')

    def test_string_prefix_false(self, menai):
        assert menai.evaluate('(string-prefix? "hello world" "world")') is False
        _assert_folded_to_constant(menai, '(string-prefix? "hello world" "world")')

    def test_string_prefix_empty(self, menai):
        assert menai.evaluate('(string-prefix? "hello world" "")') is True
        _assert_folded_to_constant(menai, '(string-prefix? "hello world" "")')

    def test_string_prefix_longer_than_string(self, menai):
        assert menai.evaluate('(string-prefix? "hello" "hello world")') is False
        _assert_folded_to_constant(menai, '(string-prefix? "hello" "hello world")')

    def test_string_prefix_unicode(self, menai):
        assert menai.evaluate('(string-prefix? "世界你好" "世界")') is True
        _assert_folded_to_constant(menai, '(string-prefix? "世界你好" "世界")')

    def test_string_suffix_true(self, menai):
        assert menai.evaluate('(string-suffix? "hello world" "world")') is True
        _assert_folded_to_constant(menai, '(string-suffix? "hello world" "world")')

    def test_string_suffix_false(self, menai):
        assert menai.evaluate('(string-suffix? "hello world" "hello")') is False
        _assert_folded_to_constant(menai, '(string-suffix? "hello world" "hello")')

    def test_string_suffix_empty(self, menai):
        assert menai.evaluate('(string-suffix? "hello world" "")') is True
        _assert_folded_to_constant(menai, '(string-suffix? "hello world" "")')

    def test_string_suffix_longer_than_string(self, menai):
        assert menai.evaluate('(string-suffix? "hello" "hello world")') is False
        _assert_folded_to_constant(menai, '(string-suffix? "hello" "hello world")')

    def test_string_suffix_unicode(self, menai):
        assert menai.evaluate('(string-suffix? "世界你好" "你好")') is True
        _assert_folded_to_constant(menai, '(string-suffix? "世界你好" "你好")')

    def test_string_index(self, menai):
        assert menai.evaluate('(string-index "hello" "l")') == 2
        _assert_folded_to_constant(menai, '(string-index "hello" "l")')

    def test_string_index_not_found(self, menai):
        assert menai.evaluate('(string-index "hello" "z")') is None
        _assert_folded_to_constant(menai, '(string-index "hello" "z")')

    def test_string_replace(self, menai):
        assert menai.evaluate('(string-replace "hello" "l" "L")') == "heLLo"
        _assert_folded_to_constant(menai, '(string-replace "hello" "l" "L")')

    def test_string_to_integer(self, menai):
        assert menai.evaluate('(string->integer "ff" 16)') == 255
        _assert_folded_to_constant(menai, '(string->integer "ff" 16)')

    def test_string_to_integer_invalid(self, menai):
        assert menai.evaluate('(string->integer "xyz" 10)') is None
        _assert_folded_to_constant(menai, '(string->integer "xyz" 10)')

    def test_string_to_integer_no_prefix(self, menai):
        """C VM does not accept 0x prefixes — folding must match."""
        assert menai.evaluate('(string->integer "0xff" 16)') is None
        _assert_folded_to_constant(menai, '(string->integer "0xff" 16)')

    def test_string_to_float(self, menai):
        assert menai.evaluate('(string->float "3.14")') == 3.14
        _assert_folded_to_constant(menai, '(string->float "3.14")')

    def test_string_to_float_scientific(self, menai):
        assert menai.evaluate('(string->float "1e2")') == 100.0
        _assert_folded_to_constant(menai, '(string->float "1e2")')

    def test_string_to_float_negative(self, menai):
        assert menai.evaluate('(string->float "-5.5")') == -5.5
        _assert_folded_to_constant(menai, '(string->float "-5.5")')

    def test_string_to_float_trimmed(self, menai):
        assert menai.evaluate('(string->float "  42  ")') == 42.0
        _assert_folded_to_constant(menai, '(string->float "  42  ")')

    def test_string_to_float_invalid(self, menai):
        assert menai.evaluate('(string->float "hello")') is None
        _assert_folded_to_constant(menai, '(string->float "hello")')

    def test_string_to_complex(self, menai):
        assert menai.evaluate('(string->complex "1+2j")') == (1 + 2j)
        _assert_folded_to_constant(menai, '(string->complex "1+2j")')

    def test_string_to_complex_pure_imaginary(self, menai):
        assert menai.evaluate('(string->complex "3j")') == 3j
        _assert_folded_to_constant(menai, '(string->complex "3j")')

    def test_string_to_complex_bare_j(self, menai):
        assert menai.evaluate('(string->complex "j")') == 1j
        _assert_folded_to_constant(menai, '(string->complex "j")')

    def test_string_to_complex_real_only(self, menai):
        assert menai.evaluate('(string->complex "1.5")') == (1.5 + 0j)
        _assert_folded_to_constant(menai, '(string->complex "1.5")')

    def test_string_to_complex_invalid(self, menai):
        assert menai.evaluate('(string->complex "hello")') is None
        _assert_folded_to_constant(menai, '(string->complex "hello")')

    def test_string_upcase(self, menai):
        assert menai.evaluate('(string-upcase "hello")') == "HELLO"
        _assert_folded_to_constant(menai, '(string-upcase "hello")')

    def test_string_upcase_expansion(self, menai):
        """ß → SS is a multi-codepoint expansion in the C VM."""
        assert menai.evaluate('(string-upcase "ß")') == "SS"
        _assert_folded_to_constant(menai, '(string-upcase "ß")')

    def test_string_downcase(self, menai):
        assert menai.evaluate('(string-downcase "HELLO")') == "hello"
        _assert_folded_to_constant(menai, '(string-downcase "HELLO")')

    def test_string_trim(self, menai):
        assert menai.evaluate('(string-trim "  hello  ")') == "hello"
        _assert_folded_to_constant(menai, '(string-trim "  hello  ")')

    def test_string_trim_left(self, menai):
        assert menai.evaluate('(string-trim-left "  hello  ")') == "hello  "
        _assert_folded_to_constant(menai, '(string-trim-left "  hello  ")')

    def test_string_trim_right(self, menai):
        assert menai.evaluate('(string-trim-right "  hello  ")') == "  hello"
        _assert_folded_to_constant(menai, '(string-trim-right "  hello  ")')

    def test_nested_folds(self, menai):
        # Verifies that the folder recurses: inner fold feeds outer fold.
        assert menai.evaluate("(integer+ (integer+ 1 2) (integer+ 3 4))") == 10
        _assert_folded_to_constant(menai, "(integer+ (integer+ 1 2) (integer+ 3 4))")

    def test_if_constant_condition(self, menai):
        # The if-elimination path is distinct from the builtin-fold path.
        assert menai.evaluate("(if #t 42 0)") == 42
        _assert_folded_to_constant(menai, "(if #t 42 0)")
