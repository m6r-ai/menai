"""
Tests for the jump threading VCode peephole optimisation.

When a label is immediately followed by an unconditional JUMP, every jump
targeting that label can be rewritten to target the JUMP's destination
directly, bypassing the intermediate jump.  After rewriting, labels that
are no longer targeted by any jump (or reachable via fall-through) are
removed along with their now-dead JUMP instruction.

This pattern arises when a CFG block that could not be bypassed at the CFG
level (e.g. because it had phi instructions and a BranchTerm predecessor)
is lowered to an empty block containing only a JUMP after phi elimination.

Covers:
  1. Basic threading — self-recursive loop with conditional accumulator
  2. Correctness — results are identical with the optimisation
  3. Transitive chain — A -> B -> C resolved to A -> C
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
    """Count occurrences of `opcode` in `code` (not nested)."""
    return sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)


def _disasm(code) -> list[str]:
    """Return a list of disassembled instruction strings for `code`."""
    from menai_disassemble.disassemble import disassemble

    return disassemble(code).splitlines()


@pytest.fixture
def menai():
    return Menai()


_COLLECT_SRC = """
(letrec ((collect (lambda (lst acc)
  (if (list-null? lst)
    acc
    (let ((h (list-first lst)))
      (collect (list-rest lst)
        (if (integer=? h 0) acc (list-prepend acc h))))))))
  (collect (list 0 3 0 7 0) (list)))
"""


class TestJumpThreadingBasic:
    """Self-recursive loop with conditional accumulator update."""

    def test_intermediate_jump_eliminated(self):
        """
        The inner (if (integer=? h 0) acc (list-prepend acc h)) creates a
        branch where both arms converge on a loop-back block that jumps to
        the entry.  After phi elimination, the loop-back block is an empty
        block containing only JUMP __entry__.  Without jump threading, both
        the JUMP_IF_TRUE (for h==0) and the JUMP after the prepend target
        this intermediate label, which then jumps to __entry__.

        After threading, both jumps target __entry__ directly and the
        intermediate label+JUMP are removed, reducing the JUMP count.
        """
        code = _compile(_COLLECT_SRC)
        collect = _find_lambda(code, "collect")
        assert collect is not None, "collect lambda not found"
        # The loop-back block's JUMP to __entry__ should be threaded away.
        # Count JUMP opcodes — the only JUMP should be the self-loop back-edge.
        # Without threading there would be an additional JUMP for the
        # intermediate loop-back block.
        jump_count = _count_op(collect, Opcode.JUMP)
        assert jump_count == 1, (
            f"Expected 1 JUMP (self-loop back-edge), got {jump_count}.\n"
            f"Disassembly:\n{chr(10).join(_disasm(collect))}"
        )

    def test_result_correct(self, menai):
        """The optimisation preserves the result of the loop."""
        result = menai.evaluate(_COLLECT_SRC)
        # Non-zero elements collected in order: 3, 7
        # list-prepend builds the list in reverse: [7, 3]
        assert result == [7, 3]

    def test_result_correct_all_zeros(self, menai):
        """All zeros — accumulator stays empty."""
        src = """
(letrec ((collect (lambda (lst acc)
  (if (list-null? lst)
    acc
    (let ((h (list-first lst)))
      (collect (list-rest lst)
        (if (integer=? h 0) acc (list-prepend acc h))))))))
  (collect (list 0 0 0) (list)))
"""
        result = menai.evaluate(src)
        assert result == []

    def test_result_correct_no_zeros(self, menai):
        """No zeros — all elements collected."""
        src = """
(letrec ((collect (lambda (lst acc)
  (if (list-null? lst)
    acc
    (let ((h (list-first lst)))
      (collect (list-rest lst)
        (if (integer=? h 0) acc (list-prepend acc h))))))))
  (collect (list 1 2 3) (list)))
"""
        result = menai.evaluate(src)
        assert result == [3, 2, 1]


class TestJumpThreadingCorrectness:
    """Verify correctness on more complex patterns."""

    def test_nested_conditional_in_loop(self, menai):
        """
        A loop with two nested conditionals, creating multiple potential
        jump-to-jump patterns.
        """
        src = """
(letrec ((loop (lambda (n sum evens odds)
  (if (integer>=? n 10)
    (list sum evens odds)
    (let ((is-even (integer=? (integer% n 2) 0)))
      (loop (integer+ n 1)
            (integer+ sum n)
            (if is-even (integer+ evens 1) evens)
            (if is-even odds (integer+ odds 1))))))))
  (loop 0 0 0 0))
"""
        result = menai.evaluate(src)
        # sum 0..9 = 45, evens = 5 (0,2,4,6,8), odds = 5 (1,3,5,7,9)
        assert result == [45, 5, 5]

    def test_multiple_branch_paths(self, menai):
        """
        A loop with three-way branching via nested ifs, creating multiple
        jump-to-jump convergence points.
        """
        src = """
(letrec ((classify (lambda (lst pos neg zero)
  (if (list-null? lst)
    (list pos neg zero)
    (let ((h (list-first lst)))
      (classify (list-rest lst)
        (if (integer>? h 0) (integer+ pos 1) pos)
        (if (integer<? h 0) (integer+ neg 1) neg)
        (if (integer=? h 0) (integer+ zero 1) zero)))))))
  (classify (list 1 -2 0 3 0 -1) 0 0 0))
"""
        result = menai.evaluate(src)
        assert result == [2, 2, 2]


class TestJumpThreadingTransitive:
    """Transitive chains A -> B -> C are resolved to A -> C."""

    def test_transitive_chain_correctness(self, menai):
        """
        A function with nested conditionals that can create transitive
        jump chains.  The threading pass should resolve these in a single
        iteration to a fixed point.
        """
        src = """
(letrec ((process (lambda (lst a b c)
  (if (list-null? lst)
    (list a b c)
    (let ((h (list-first lst)))
      (process (list-rest lst)
        (if (integer=? h 0) (integer+ a 1) a)
        (if (integer=? h 1) (integer+ b 1) b)
        (if (integer=? h 2) (integer+ c 1) c)))))))
  (process (list 0 1 2 0 1 2 0) 0 0 0))
"""
        result = menai.evaluate(src)
        assert result == [3, 2, 2]
