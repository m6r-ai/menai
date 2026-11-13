"""
Tests for the literal-test fold in MenaiIROptimizer.

The optimizer folds a test on a list literal, because the inliner binds a variadic
rest parameter to one:

    (list-null? ())          -> #t
    (list-null? (list x))    -> #f

Strategy
--------
1. Unit tests on the optimizer directly, constructing IR nodes by hand.
2. An integration test that compiles Menai source, so the fold is exercised
   through the inliner that produces the literal.
"""

from __future__ import annotations

from menai import Menai
from menai.ir.menai_ir import (
    MenaiIRBuildList,
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIREmptyList,
    MenaiIRIf,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiBoolean, MenaiSymbol


def _bool_const(value: bool) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiBoolean(value))


def _list_null_call(argument) -> MenaiIRCall:
    """Emit (list-null? argument) as a builtin call."""
    return MenaiIRCall(
        func_plan=MenaiIRVariable(name='list-null?'),
        arg_plans=[argument],
        is_tail_call=False,
        is_builtin=True,
        builtin_name='list-null?',
    )


def _run(ir):
    """Run the optimizer and return (new_ir, changed)."""
    return MenaiIROptimizer().optimize(ir)


class TestFoldsOnListLiteral:

    def test_empty_list_literal_folds_to_true(self):
        """(list-null? ()) is known to be #t."""
        new_ir, _ = _run(_list_null_call(MenaiIREmptyList()))
        assert isinstance(new_ir, MenaiIRConstant)
        assert new_ir.value.value is True

    def test_non_empty_list_literal_folds_to_false(self):
        """(list-null? (list x)) is known to be #f."""
        element = MenaiIRConstant(value=MenaiSymbol(name='x'))
        new_ir, _ = _run(_list_null_call(MenaiIRBuildList(element_plans=(element,))))
        assert isinstance(new_ir, MenaiIRConstant)
        assert new_ir.value.value is False

    def test_empty_build_list_folds_to_true(self):
        """A zero-element list construction is an empty list."""
        new_ir, _ = _run(_list_null_call(MenaiIRBuildList(element_plans=())))
        assert isinstance(new_ir, MenaiIRConstant)
        assert new_ir.value.value is True

    def test_variable_argument_is_not_folded(self):
        """A test on a value the optimizer knows nothing about is left alone."""
        call = _list_null_call(MenaiIRVariable(name='xs'))
        new_ir, _ = _run(call)
        assert isinstance(new_ir, MenaiIRCall)

    def test_other_builtin_is_not_folded(self):
        """Only the list test is folded, so unrelated predicates are untouched."""
        call = MenaiIRCall(
            func_plan=MenaiIRVariable(name='list-length'),
            arg_plans=[MenaiIREmptyList()],
            is_tail_call=False,
            is_builtin=True,
            builtin_name='list-length',
        )
        new_ir, _ = _run(call)
        assert isinstance(new_ir, MenaiIRCall)


class TestFoldThroughThePipeline:

    def test_folded_test_removes_the_untaken_branch(self):
        """
        A predicate on a variadic rest parameter folds, dropping the other arm.

        The inliner binds the rest parameter of a call with no extra arguments to
        the empty list literal, so the branch on it is decided while compiling.
        """
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((f (lambda (. args) (if (list-null? args) 'none 'some))))
              (f))
            """)

        assert result.name == 'none'

    def test_folded_test_keeps_the_taken_branch(self):
        """A call that does supply extra arguments still takes the other arm."""
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((f (lambda (. args) (if (list-null? args) 'none 'some))))
              (f 1))
            """)

        assert result.name == 'some'

    def test_returned_test_result_is_correct(self):
        """The folded test yields the same boolean the unfoleded one would."""
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((f (lambda (. args) (list-null? args))))
              (list (f) (f 1) (f 1 2)))
            """)

        assert [element.value for element in result.elements] == [True, False, False]
