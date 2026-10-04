"""
Tests for folding constants that reach their use through a MOVE.

The fold_constants pass rewrites a foldable operand from a register to a
constant when the register has a unique constant definition.  A constant
frequently reaches its use through a phi-elimination MOVE rather than directly
from the LOAD_CONST or MAKE_CLOSURE that produced it.  The canonical case is a
loop-carried variable that is never reassigned: an inlined higher-order
function such as fold-list binds its function argument once before the loop,
and the loop back-edge is a self-move that the VCode builder drops, so the
argument register has a unique definition that is a MOVE from the constant.

The pass propagates foldability along MOVE edges to a fixed point, so a chain
of moves is followed to its constant source.  A defining instruction is removed
only when every use of its register has been folded, and removal itself runs to
a fixed point so that removing a carrying MOVE can make the constant's own
definition removable.

Covers:
  1. A capture-less lambda passed to an inlined higher-order function folds
     into the CALL callee operand.
  2. A chain of moves is followed to its constant source.
  3. A register with more than one definition is not folded.
  4. Correctness of results across the optimised code.
"""

import pytest
from menai import Menai
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.menai_compiler import MenaiCompiler


def _compile(src: str):
    return MenaiCompiler().compile(src)


def _find_lambda(code, name: str):
    """Return the first nested code object whose name contains `name`."""
    for co in code.code_objects:
        if name in co.name:
            return co
        r = _find_lambda(co, name)
        if r is not None:
            return r
    return None


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)
    return n


def _call_constant_operands(code) -> list[int]:
    """
    Return the src0 field of every CALL whose function operand is a constant.

    A CALL folds its callee in src0; tag bit 0 set means src0 is a
    constant-pool index rather than a register slot.
    """
    result = []
    for word in code.instructions:
        unpacked = unpack_instruction(word)
        if unpacked.opcode == Opcode.CALL and unpacked.tag & 1:
            result.append(unpacked.src0)
    return result


@pytest.fixture
def menai():
    return Menai()


class TestConstantMoveFolding:
    """Constants reaching a use through a MOVE are folded into the use."""

    def test_lambda_arg_to_inlined_hof_folds_into_call(self):
        """
        A capture-less lambda passed to fold-list, a prelude higher-order
        function, becomes the callee of a CALL after inlining.  The lambda
        value reaches that CALL through a phi-elimination MOVE (the inlined
        function argument is a loop-carried variable initialised once before
        the loop).  The constant must be folded into the CALL's callee operand,
        so no LOAD_CONST or MAKE_CLOSURE remains for it.
        """
        src = """
        (let ((max-length (lambda (lst)
                            (fold-list (lambda (acc len) (integer-max acc len)) 0 lst))))
          (max-length (list 3 1 4 1 5)))
        """
        code = _find_lambda(_compile(src), "max-length")
        assert code is not None
        # The lambda is folded into the CALL callee: the CALL has a constant
        # function operand and no MAKE_CLOSURE or function LOAD_CONST remains.
        assert len(_call_constant_operands(code)) == 1
        assert _count_op(code, Opcode.MAKE_CLOSURE) == 0
        # The only remaining constant load is the integer 0 accumulator seed.
        assert _count_op(code, Opcode.LOAD_CONST) == 1

    def test_lambda_arg_to_inlined_hof_correct_result(self, menai):
        """The optimised fold-list loop computes the correct maximum."""
        result = menai.evaluate("""
            (let ((max-length (lambda (lst)
                                (fold-list (lambda (acc len) (integer-max acc len)) 0 lst))))
              (list (max-length (list 3 1 4 1 5))
                    (max-length (list 7))
                    (max-length (list 2 9 4))))
        """)
        assert result == [5, 7, 9]

    def test_move_chain_followed_to_constant_source(self):
        """
        A constant that reaches its use through more than one MOVE is folded.

        The lambda is bound to a local, which is then passed to fold-list, so
        the value passes through a chain of moves before reaching the CALL
        inside the inlined loop.
        """
        src = """
        (let ((max-length (lambda (lst)
                            (let ((combine (lambda (acc len) (integer-max acc len))))
                              (fold-list combine 0 lst)))))
          (max-length (list 3 1 4 1 5)))
        """
        code = _find_lambda(_compile(src), "max-length")
        assert code is not None
        assert len(_call_constant_operands(code)) == 1
        assert _count_op(code, Opcode.MAKE_CLOSURE) == 0

    def test_move_chain_correct_result(self, menai):
        """A constant reaching its use through a move chain is correct."""
        result = menai.evaluate("""
            (let ((max-length (lambda (lst)
                                (let ((combine (lambda (acc len) (integer-max acc len))))
                                  (fold-list combine 0 lst)))))
              (max-length (list 3 1 4 1 5)))
        """)
        assert result == 5

    def test_function_selected_by_branch_not_folded(self):
        """
        When the function passed to an inlined higher-order function is chosen
        by a branch, the callee register has more than one definition — one
        per branch arm — so no single constant reaches the CALL and the callee
        stays a register.
        """
        src = """
        (let ((pick (lambda (b)
                      (let ((f (if b
                                   (lambda (acc x) (integer+ acc x))
                                   (lambda (acc x) (integer- acc x)))))
                        (fold-list f 0 (list 1 2 3))))))
          (list (pick #t) (pick #f)))
        """
        code = _find_lambda(_compile(src), "pick")
        assert code is not None
        # Neither lambda is folded: the callee register is defined once per
        # branch arm, so it has more than one definition.
        assert _call_constant_operands(code) == []

    def test_function_selected_by_branch_correct_result(self, menai):
        """A branch-selected function passed to fold-list produces correct results."""
        result = menai.evaluate("""
            (let ((pick (lambda (b)
                          (let ((f (if b
                                       (lambda (acc x) (integer+ acc x))
                                       (lambda (acc x) (integer- acc x)))))
                            (fold-list f 0 (list 1 2 3))))))
              (list (pick #t) (pick #f)))
        """)
        assert result == [6, -6]


class TestConstantMoveFoldingCorrectness:
    """End-to-end correctness across a range of fold-list uses."""

    def test_sum_via_fold_list(self, menai):
        """A lambda folded into an inlined fold-list computes a correct sum."""
        result = menai.evaluate("""
            (let ((total (lambda (lst)
                           (fold-list (lambda (acc x) (integer+ acc x)) 0 lst))))
              (list (total (list 1 2 3 4))
                    (total (list))
                    (total (list 10))))
        """)
        assert result == [10, 0, 10]

    def test_string_fold_list(self, menai):
        """A lambda folded into an inlined fold-list works for strings."""
        result = menai.evaluate("""
            (let ((join (lambda (lst)
                          (fold-list (lambda (acc s) (string-concat acc s)) "" lst))))
              (join (list "a" "b" "c")))
        """)
        assert result == "abc"

    def test_filter_list_constant_predicate(self, menai):
        """A constant predicate folded into an inlined filter-list is correct."""
        result = menai.evaluate("""
            (let ((evens (lambda (lst)
                           (filter-list (lambda (x) (integer=? (integer% x 2) 0)) lst))))
              (evens (list 1 2 3 4 5 6)))
        """)
        assert result == [2, 4, 6]
