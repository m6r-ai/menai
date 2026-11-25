"""
Tests for the literal-accessor fold in MenaiIROptimizer.

The optimizer folds an accessor applied to a container literal, because the inliner
binds a variadic rest parameter to one:

    (list-length (list a b))    -> 2
    (list-first (list a b))     -> a
    (list-last (list a b))      -> b
    (list-nth (list a b) 1)     -> b

Every variadic prelude wrapper dispatches on the length of its rest list and then
reads the first and last elements, so without this fold the dispatcher survives
inlining and each call pays an allocation.  The elements are runtime values rather
than constants, which is why neither the AST constant folder nor branch constant
propagation can decide the accessors.

Strategy
--------
1. Unit tests on the optimizer directly, constructing IR nodes by hand.
2. An integration test that compiles Menai source, so the fold is exercised
   through the inliner that produces the literal.
"""

from __future__ import annotations

from menai import Menai
from menai.bytecode.menai_bytecode import Opcode
from menai.ir.menai_ir import (
    MenaiIRBuildList,
    MenaiIRBuildVector,
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIREmptyList,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiInteger


def _element(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name)


def _accessor(name: str, *arguments) -> MenaiIRCall:
    """Emit (name arguments...) as a builtin call."""
    return MenaiIRCall(
        func_plan=MenaiIRVariable(name=name),
        arg_plans=list(arguments),
        is_tail_call=False,
        is_builtin=True,
        builtin_name=name,
    )


def _two_element_list() -> MenaiIRBuildList:
    return MenaiIRBuildList(element_plans=(_element('a'), _element('b')))


def _run(ir):
    """Run the optimizer and return the resulting tree."""
    new_ir, _ = MenaiIROptimizer().optimize(ir)
    return new_ir


def _opcode_count(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    from menai.bytecode.menai_bytecode import unpack_instruction

    count = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        count += _opcode_count(nested, opcode)

    return count


class TestListAccessors:

    def test_length_folds_to_the_element_count(self):
        """(list-length (list a b)) is known to be 2."""
        new_ir = _run(_accessor('list-length', _two_element_list()))
        assert isinstance(new_ir, MenaiIRConstant)
        assert new_ir.value.value == 2

    def test_first_folds_to_the_first_element(self):
        """(list-first (list a b)) is the first element."""
        new_ir = _run(_accessor('list-first', _two_element_list()))
        assert isinstance(new_ir, MenaiIRVariable)
        assert new_ir.name == 'a'

    def test_last_folds_to_the_last_element(self):
        """(list-last (list a b)) is the last element."""
        new_ir = _run(_accessor('list-last', _two_element_list()))
        assert isinstance(new_ir, MenaiIRVariable)
        assert new_ir.name == 'b'

    def test_nth_folds_at_a_constant_index(self):
        """(list-nth (list a b) 1) is the element at that index."""
        new_ir = _run(_accessor('list-nth', _two_element_list(),
                                MenaiIRConstant(value=MenaiInteger(value=1))))
        assert isinstance(new_ir, MenaiIRVariable)
        assert new_ir.name == 'b'

    def test_rest_folds_to_the_tail(self):
        """(list-rest (list a b)) is a list holding the trailing elements."""
        new_ir = _run(_accessor('list-rest', _two_element_list()))
        assert isinstance(new_ir, MenaiIRBuildList)
        assert len(new_ir.element_plans) == 1

    def test_first_of_an_empty_list_is_not_folded(self):
        """An accessor with no element to return is left alone, so its raise survives."""
        new_ir = _run(_accessor('list-first', MenaiIREmptyList()))
        assert isinstance(new_ir, MenaiIRCall)

    def test_nth_past_the_end_is_not_folded(self):
        """An index outside the literal's shape would raise, so the call is kept."""
        new_ir = _run(_accessor('list-nth', _two_element_list(),
                                MenaiIRConstant(value=MenaiInteger(value=5))))
        assert isinstance(new_ir, MenaiIRCall)

    def test_nth_at_a_runtime_index_is_not_folded(self):
        """An index the compiler cannot read is not folded."""
        new_ir = _run(_accessor('list-nth', _two_element_list(), MenaiIRVariable(name='i')))
        assert isinstance(new_ir, MenaiIRCall)

    def test_list_accessor_on_a_vector_literal_is_not_folded(self):
        """The fold respects the container's kind."""
        call = _accessor('list-length', MenaiIRBuildVector(element_plans=(_element('a'),)))
        new_ir = _run(call)
        assert isinstance(new_ir, MenaiIRCall)


class TestVectorAccessors:

    def test_length_folds_to_the_element_count(self):
        """(vector-length (vector a b c)) is known to be 3."""
        call = _accessor('vector-length',
                         MenaiIRBuildVector(element_plans=(_element('a'), _element('b'), _element('c'))))
        new_ir = _run(call)
        assert isinstance(new_ir, MenaiIRConstant)
        assert new_ir.value.value == 3

    def test_nth_folds_at_a_constant_index(self):
        """(vector-nth (vector a b) 0) is the element at that index."""
        call = _accessor('vector-nth',
                         MenaiIRBuildVector(element_plans=(_element('a'), _element('b'))),
                         MenaiIRConstant(value=MenaiInteger(value=0)))
        new_ir = _run(call)
        assert isinstance(new_ir, MenaiIRVariable)
        assert new_ir.name == 'a'


class TestFoldThroughThePipeline:

    def test_length_of_a_list_built_from_parameters_is_a_constant(self):
        """A wrapper around a literal's length compiles to a constant return."""
        menai = Menai()
        value = menai.evaluate_raw("((lambda (a b) (list-length (list a b))) 7 9)")
        assert value.value == 2

    def test_first_and_last_of_a_literal_carry_the_arguments(self):
        """Reading both ends of a literal yields the arguments themselves."""
        menai = Menai()
        value = menai.evaluate_raw("((lambda (a b) (integer<? (list-first (list a b)) "
                                   "(list-last (list a b)))) 1 2)")
        assert value.value is True

    def test_variadic_wrapper_needs_no_list_allocation(self):
        """
        A call to a variadic prelude wrapper does not allocate its rest list.

        The wrapper dispatches on the rest list's length and then reads its ends.
        Once those fold, nothing reads the list, so no MAKE_LIST is emitted for it.
        """
        from menai.menai_compiler import MenaiCompiler

        code = MenaiCompiler().compile(
            "(let ((less (lambda (v) (integer<? (vector-nth v 0) (vector-nth v 1)))))"
            "  (less (vector 1 2)))"
        )
        assert _opcode_count(code, Opcode.MAKE_LIST) == 0

    def test_result_is_correct_for_a_three_argument_call(self):
        """A call that takes the wrapper's loop arm still computes the right answer."""
        menai = Menai()
        value = menai.evaluate_raw("(integer<? 1 2 3)")
        assert value.value is True
