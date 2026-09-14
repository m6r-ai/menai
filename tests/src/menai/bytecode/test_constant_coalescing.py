"""
Tests for the constant coalescing VCode optimisation.

When the same constant value is loaded multiple times in a function, the
coalesce_constants pass keeps only the first LOAD_CONST and replaces all
uses of subsequent duplicate registers with the first register.  This
reduces both the number of load instructions emitted and the number of
slots needed.

Safety: a duplicate is only coalesced with an earlier load when no labels
(branch targets) appear between them, ensuring the first load dominates
the duplicate.

Covers:
  1. Basic coalescing — duplicate integer constant in straight-line code
  2. Coalescing of string constants
  3. Coalescing of boolean and none constants (emitted as LOAD_TRUE/FALSE/NONE)
  4. No coalescing across labels — dominance safety
  5. Correctness — results are identical with the optimisation
  6. Distinct constants of the same type are not coalesced
  7. Nested functions coalesce independently
"""

import pytest
from menai import Menai
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.menai_compiler import MenaiCompiler


def _compile(src: str):
    return MenaiCompiler().compile(src)


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)
    return n


def _find_lambda(code, name: str):
    """Return the first nested code object whose name contains `name`."""
    for co in code.code_objects:
        if name in co.name:
            return co
        r = _find_lambda(co, name)
        if r is not None:
            return r
    return None


@pytest.fixture
def menai():
    return Menai()


class TestConstantCoalescingBasic:
    """Duplicate constants in straight-line code are coalesced to one load."""

    def test_integer_constant_coalesced(self):
        """
        The integer 1 is used four times in straight-line code.
        After coalescing, only one LOAD_CONST of integer 1 should remain.
        """
        src = """
        (lambda (x)
          (integer+ (integer+ x 1) (integer+ 1 (integer+ x 1))))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # The integer 1 should appear exactly once in the constants.
        int1_consts = [
            c for c in code.constants
            if type(c).__name__ == "MenaiInteger" and c.value == 1
        ]
        assert len(int1_consts) == 1
        # And there should be exactly one LOAD_CONST for it.
        load_const_count = _count_op(code, Opcode.LOAD_CONST)
        assert load_const_count == 1

    def test_string_constant_coalesced(self):
        """
        The string "x" is used three times in straight-line code.
        After coalescing, only one LOAD_CONST of "x" should remain.
        """
        src = """
        (lambda (s)
          (string-concat (string-concat s "x") (string-concat "x" s)))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        str_x_consts = [
            c for c in code.constants
            if type(c).__name__ == "MenaiString" and c.value == "x"
        ]
        assert len(str_x_consts) == 1
        assert _count_op(code, Opcode.LOAD_CONST) == 1

    def test_multiple_distinct_constants_coalesced_independently(self):
        """
        When multiple distinct constants are each used multiple times,
        each should be coalesced independently — one load per distinct value.
        """
        src = """
        (lambda (x)
          (integer+ (integer+ x 1) (integer+ 2 (integer+ 1 (integer+ x 2)))))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # Two distinct integer constants: 1 and 2.
        assert _count_op(code, Opcode.LOAD_CONST) == 2

    def test_boolean_constant_coalesced_in_straight_line(self):
        """
        #t used multiple times in the entry block (before any branch)
        should coalesce to a single LOAD_TRUE.  Using boolean=? with a
        parameter prevents AST constant folding while keeping all #t
        loads in straight-line code.

        (boolean=? (boolean=? x #t) #t) loads #t twice in the entry
        block before any branching occurs, so they should coalesce to
        one LOAD_TRUE.
        """
        src = """
        (lambda (x)
          (boolean=? (boolean=? x #t) #t))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # Both #t are in straight-line code — coalesce to one LOAD_TRUE.
        assert _count_op(code, Opcode.LOAD_TRUE) == 1

    def test_none_constant_coalesced_in_straight_line(self):
        """
        #none used multiple times in straight-line code (before any branch)
        should coalesce to a single LOAD_NONE.  Using none? prevents AST
        constant folding (none? is not foldable) while keeping all #none
        loads in the entry block with no labels between them.

        (none? (if x #none #none)) would have #none in both branches
        (separated by labels).  Instead, (boolean=? (none? #none) (none? #none))
        loads #none twice in the entry block with no labels between them.
        """
        src = """
        (lambda (x)
          (boolean=? (none? #none) (none? #none)))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # Both #none are in straight-line code — coalesce to one LOAD_NONE.
        assert _count_op(code, Opcode.LOAD_NONE) == 1


class TestConstantCoalescingDominanceSafety:
    """Constants in different branches must not be coalesced across labels."""

    def test_no_coalescing_across_branch_labels(self):
        """
        When the same constant appears in both arms of a conditional,
        the second occurrence is after a label (branch target) and must
        not be coalesced with the first.

        Using a boolean param as the condition avoids introducing an
        extra integer constant that would confuse the count.
        """
        src = """
        (lambda (b)
          (if b "same" "same"))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # The string "same" appears in both branches, separated by a label.
        # They must NOT be coalesced — two LOAD_CONST expected.
        # The constant pool has one entry, but two LOAD_CONST instructions
        # because they're in different branches.
        assert _count_op(code, Opcode.LOAD_CONST) == 2
        # Also verify the constant pool has just one entry for "same".
        str_same = [
            c for c in code.constants
            if type(c).__name__ == "MenaiString" and c.value == "same"
        ]
        assert len(str_same) == 1

    def test_constant_in_straight_line_then_branch_coalesced(self):
        """
        A constant loaded before any branch (in the entry block) can be
        reused in both branch arms because it dominates them.

        (let ((s "x")) (if cond (string-concat s "y") (string-concat s "z")))
        — "x" is loaded once before the branch and used in both arms.
        The "y" and "z" are in different arms and cannot coalesce with
        anything.
        """
        src = """
        (lambda (b)
          (let ((s "x"))
            (if (boolean? b)
                (string-concat s "y")
                (string-concat s "z"))))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # "x" loaded once before the branch, used in both arms.
        # "y" and "z" each loaded once in their respective arms.
        # Total: 3 LOAD_CONST (one per distinct string).
        assert _count_op(code, Opcode.LOAD_CONST) == 3


class TestConstantCoalescingCorrectness:
    """End-to-end correctness with coalesced constants."""

    def test_integer_arithmetic_correct(self, menai):
        """Multiple uses of the same integer constant produce correct results."""
        src = """
        (let ((f (lambda (x) (integer+ (integer+ x 1) (integer+ 1 (integer+ x 1))))))
          (list (f 0) (f 10) (f 100)))
        """
        assert menai.evaluate(src) == [3, 23, 203]

    def test_string_concat_correct(self, menai):
        """Multiple uses of the same string constant produce correct results."""
        src = """
        (let ((f (lambda (s) (string-concat (string-concat s "x") (string-concat "x" s)))))
          (list (f "a") (f "") (f "ab")))
        """
        assert menai.evaluate(src) == ["axxa", "xx", "abxxab"]

    def test_boolean_results_correct(self, menai):
        """Multiple uses of #t/#f in branches produce correct results."""
        src = """
        (let ((f (lambda (x)
                   (if (integer>? x 0)
                       (if (integer<? x 100) #t #f)
                       #f))))
          (list (f 50) (f 200) (f -1)))
        """
        assert menai.evaluate(src) == [True, False, False]

    def test_none_results_correct(self, menai):
        """Multiple uses of #none produce correct results."""
        src = """
        (let ((f (lambda (x)
                   (if (integer>? x 0)
                       (if (integer<? x 100) #none 1)
                       2))))
          (list (f 50) (f 200) (f -1)))
        """
        result = menai.evaluate(src)
        assert result[0] is None
        assert result[1] == 1
        assert result[2] == 2

    def test_letrec_loop_with_repeated_constants(self, menai):
        """
        A self-recursive loop that uses the same constant in multiple
        iterations must produce correct results after coalescing.
        """
        src = """
        (letrec ((loop (lambda (n acc)
                         (if (integer<=? n 0)
                             acc
                             (loop (integer- n 1) (integer+ acc 1))))))
          (loop 1000 0))
        """
        assert menai.evaluate(src) == 1000

    def test_match_with_repeated_string_constants(self, menai):
        """
        A match expression with repeated string constants in different
        arms must produce correct results.
        """
        src = """
        (let ((classify (lambda (ch)
                          (match ch
                            ("a" "vowel")
                            ("e" "vowel")
                            ("i" "vowel")
                            ("o" "vowel")
                            ("u" "vowel")
                            (_ "consonant")))))
          (list (classify "a") (classify "b") (classify "u")))
        """
        assert menai.evaluate(src) == ["vowel", "consonant", "vowel"]


class TestConstantCoalescingDistinctValues:
    """Distinct constant values must not be coalesced with each other."""

    def test_different_integers_not_coalesced(self):
        """Integers 1 and 2 are distinct and must not be coalesced."""
        src = """
        (lambda (x) (integer+ (integer+ x 1) 2))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        assert _count_op(code, Opcode.LOAD_CONST) == 2

    def test_different_strings_not_coalesced(self):
        """Strings "a" and "b" are distinct and must not be coalesced."""
        src = """
        (lambda (s) (string-concat (string-concat s "a") "b"))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        assert _count_op(code, Opcode.LOAD_CONST) == 2

    def test_integer_and_float_not_coalesced(self):
        """Integer 1 and float 1.0 are distinct types and must not coalesced."""
        src = """
        (lambda (x) (list (integer+ x 1) (float+ x 1.0)))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        assert _count_op(code, Opcode.LOAD_CONST) == 2


class TestConstantCoalescingNestedFunctions:
    """Each function's constants are coalesced independently."""

    def test_nested_functions_coalesce_independently(self):
        """
        Two nested lambdas that both use the same constant should each
        coalesce their own copies independently.
        """
        src = """
        (lambda (x)
          (list
            ((lambda (y) (integer+ (integer+ y 1) 1)) x)
            ((lambda (z) (integer+ (integer+ z 1) 1)) x)))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # Find the two nested lambdas.
        nested = [co for co in code.code_objects if "lambda" in co.name]
        assert len(nested) == 2
        for lam in nested:
            # Each nested lambda should have exactly one LOAD_CONST for 1.
            assert _count_op(lam, Opcode.LOAD_CONST) == 1

    def test_nested_function_correct_result(self, menai):
        """Nested functions with coalesced constants produce correct results."""
        src = """
        (lambda (x)
          (list
            ((lambda (y) (integer+ (integer+ y 1) 1)) x)
            ((lambda (z) (integer+ (integer+ z 1) 1)) x)))
        """
        result = menai.evaluate(f"({src} 10)")
        assert result == [12, 12]


class TestConstantCoalescingNoChange:
    """No coalescing when there are no duplicates."""

    def test_no_duplicates_no_change(self):
        """A function with all-distinct constants should not be changed."""
        src = """
        (lambda (x) (integer+ (integer+ x 1) 2))
        """
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        # Two distinct constants, each used once — no coalescing.
        assert _count_op(code, Opcode.LOAD_CONST) == 2

    def test_single_use_no_coalescing(self):
        """A constant used only once is not affected."""
        src = '(lambda (x) (integer+ x 42))'
        code = _find_lambda(_compile(src), "lambda")
        assert code is not None
        assert _count_op(code, Opcode.LOAD_CONST) == 1
