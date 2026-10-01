"""
Tests for the conditional-return fusion VCode peephole optimisation.

A conditional jump that leads directly to a RETURN is fused into a single
conditional-return instruction (RETURN_IF_FALSE / RETURN_IF_TRUE).  Two shapes
are handled:

Pattern A — the conditional's target is a RETURN block:

    JUMP_IF_FALSE cond, @L        RETURN_IF_FALSE cond, v
    <other arm>              →    <other arm>
    @L: RETURN v                  @L: RETURN v

Pattern B — the conditional is immediately followed by a RETURN:

    JUMP_IF_TRUE cond, @T         RETURN_IF_FALSE cond, v
    RETURN v                 →    ...
    ...                           @T:
    @T:

After fusion, a target label that is no longer referenced and is not reachable
by fall-through is dead, and its RETURN is removed along with it.

Covers:
  1. Pattern A generation
  2. Pattern B generation
  3. Dead RETURN elimination
  4. Correctness — results are identical with the optimisation
  5. Non-fusion — a conditional that does not lead to a RETURN is preserved
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


_PATTERN_A = """
(lambda (c x)
  (if c
      x
      (let ((a (integer+ x 1)))
        (integer+ a 2))))
"""

_PATTERN_B = """
(lambda (c x)
  (if c
      (let ((a (integer+ x 1)))
        (integer+ a 2))
      x))
"""


class TestPatternAGeneration:
    """The conditional's target is a RETURN block."""

    def test_return_if_true_emitted(self):
        """
        The true arm is the simple value x and the false arm is a let block.
        The CFG lowers this to a conditional jump whose target is a block
        containing only RETURN x; the jump is fused into RETURN_IF_TRUE.
        """
        code = _compile(_PATTERN_A)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        assert _count_op(lam, Opcode.RETURN_IF_TRUE) == 1, (
            f"Expected one RETURN_IF_TRUE.\nDisassembly:\n{chr(10).join(_disasm(lam))}"
        )

    def test_no_conditional_jump_remains(self):
        """
        The conditional jump is replaced outright, so no conditional jump
        remains in the function.
        """
        code = _compile(_PATTERN_A)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        cond_count = (
            _count_op(lam, Opcode.JUMP_IF_TRUE) + _count_op(lam, Opcode.JUMP_IF_FALSE)
        )
        assert cond_count == 0, (
            f"Expected no conditional jump.\nDisassembly:\n{chr(10).join(_disasm(lam))}"
        )


class TestPatternBGeneration:
    """The conditional is immediately followed by a RETURN."""

    def test_return_if_false_emitted(self):
        """
        The true arm is a let block and the false arm is the simple value x.
        The CFG lowers this to a conditional jump immediately followed by
        RETURN x, with the target block after it; the jump is fused into
        RETURN_IF_FALSE (the polarity inverts because the fall-through
        returns).
        """
        code = _compile(_PATTERN_B)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        assert _count_op(lam, Opcode.RETURN_IF_FALSE) == 1, (
            f"Expected one RETURN_IF_FALSE.\nDisassembly:\n{chr(10).join(_disasm(lam))}"
        )

    def test_no_conditional_jump_remains(self):
        """
        The conditional jump is replaced outright, so no conditional jump
        remains in the function.
        """
        code = _compile(_PATTERN_B)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        cond_count = (
            _count_op(lam, Opcode.JUMP_IF_TRUE) + _count_op(lam, Opcode.JUMP_IF_FALSE)
        )
        assert cond_count == 0, (
            f"Expected no conditional jump.\nDisassembly:\n{chr(10).join(_disasm(lam))}"
        )


class TestDeadReturnElimination:
    """After fusion, an unreferenced RETURN block is removed."""

    def test_dead_return_removed_pattern_a(self):
        """
        The @L: RETURN x block in Pattern A is no longer targeted once the
        conditional is fused, and is not reachable by fall-through, so the
        RETURN is removed.
        """
        code = _compile(_PATTERN_A)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        assert _count_op(lam, Opcode.RETURN) == 1, (
            f"Expected exactly one plain RETURN (the false arm).\n"
            f"Disassembly:\n{chr(10).join(_disasm(lam))}"
        )

    def test_dead_return_removed_pattern_b(self):
        """
        The RETURN consumed by Pattern B is removed, leaving only the false
        arm's RETURN.
        """
        code = _compile(_PATTERN_B)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        assert _count_op(lam, Opcode.RETURN) == 1, (
            f"Expected exactly one plain RETURN (the true arm).\n"
            f"Disassembly:\n{chr(10).join(_disasm(lam))}"
        )


class TestConditionalReturnCorrectness:
    """Verify the optimisation preserves program behaviour."""

    @pytest.fixture
    def menai(self):
        return Menai()

    def test_pattern_a_both_arms_correct(self, menai):
        """
        Pattern A: the true arm returns x unchanged; the false arm returns
        x + 3.
        """
        result = menai.evaluate(f"(list ({_PATTERN_A} #t 10) ({_PATTERN_A} #f 10))")
        assert result == [10, 13]

    def test_pattern_b_both_arms_correct(self, menai):
        """
        Pattern B: the true arm returns x + 3; the false arm returns x
        unchanged.
        """
        result = menai.evaluate(f"(list ({_PATTERN_B} #t 10) ({_PATTERN_B} #f 10))")
        assert result == [13, 10]


class TestNonFusion:
    """Conditionals that do not lead to a RETURN are preserved."""

    def test_conditional_without_return_preserved(self):
        """
        A conditional whose arms both continue (neither is a bare RETURN of a
        join block) cannot be fused, so the conditional jump must remain.
        """
        src = """
(lambda (c x)
  (if c
      (integer+ x 1)
      (integer- x 1)))
"""
        code = _compile(src)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        cond_count = (
            _count_op(lam, Opcode.JUMP_IF_TRUE) + _count_op(lam, Opcode.JUMP_IF_FALSE)
        )
        assert cond_count >= 1, (
            f"Expected the conditional branch to be preserved.\n"
            f"Disassembly:\n{chr(10).join(_disasm(lam))}"
        )
        assert _count_op(lam, Opcode.RETURN_IF_TRUE) == 0
        assert _count_op(lam, Opcode.RETURN_IF_FALSE) == 0
