"""
Tests for folding a branch on a register a preceding load set to a constant.

A rotated loop evaluates its test twice: once in the peeled header test and
once in the rotated test block at the bottom.  When the test's operands are
constants the header test folds to a load of a known boolean, which makes the
branch on it a branch on a constant — the header test either always jumps or
never jumps.

The fold_constants pass materialises that boolean as a LOAD_CONST, and the
peephole pass then folds the branch on it: a branch that is always taken
becomes an unconditional JUMP, and a branch that is never taken is removed
along with the load.  The load is removed only when the branch was its sole
remaining use, so a register read elsewhere keeps its definition.

Covers:
  1. A never-taken header test leaves no dead boolean load behind.
  2. A branch on a loaded constant becomes an unconditional jump.
  3. A load whose register is read elsewhere is kept.
  4. Correctness of results across the optimised code.
"""

import pytest
from menai import Menai
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiBoolean
from menai.vcode.menai_vcode import (
    MenaiVCodeFunction,
    MenaiVCodeJumpIfTrue,
    MenaiVCodeLabel,
    MenaiVCodeLoadConst,
    MenaiVCodeOperand,
    MenaiVCodeReg,
    MenaiVCodeReturn,
)
from menai.vcode.menai_vcode_allocator import SlotMap
from menai.vcode.menai_vcode_peephole import peephole


def _compile(src: str):
    return MenaiCompiler().compile(src)


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` (not nested)."""
    return sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)


def _disasm(code) -> list[str]:
    """Return a list of disassembled instruction strings for `code`."""
    from menai_disassemble.disassemble import disassemble

    return disassemble(code).splitlines()


_SINK = """
(let
  ((f
    (lambda (n)
      (let ((loop
             (lambda (i acc)
               (if (integer>=? i n)
                   acc
                   (loop (integer+ i 1) (integer+ acc i))))))
        (loop 0 0)))))

  (f 5))
"""


class TestNeverTakenHeaderTest:
    """A header test whose operands are constants never jumps."""

    def test_no_dead_boolean_load(self):
        """
        The loop is called with a constant bound, so the peeled header test
        compares two literals and folds to a known false.  The branch on it
        never jumps, so the folded boolean is never loaded.
        """
        code = _compile(_SINK)
        assert _count_op(code, Opcode.LOAD_FALSE) == 0, (
            f"Expected no load of a folded-false header test.\n"
            f"Disassembly:\n{chr(10).join(_disasm(code))}"
        )

    def test_no_conditional_branch(self):
        """
        The never-taken header test is removed outright, so no conditional
        branch remains in the function.
        """
        code = _compile(_SINK)
        cond_count = (
            _count_op(code, Opcode.JUMP_IF_TRUE)
            + _count_op(code, Opcode.RETURN_IF_TRUE)
        )
        assert cond_count == 0, (
            f"Expected the never-taken header test to be removed.\n"
            f"Disassembly:\n{chr(10).join(_disasm(code))}"
        )


class TestBranchOnLoadedConstant:
    """A branch on a register a preceding load set to a constant is folded."""

    def test_always_taken_becomes_jump(self):
        """
        LOAD_CONST #t r / JUMP_IF_TRUE r @L is an unconditional jump: the
        condition is known, so the load and the conditional are replaced by a
        single JUMP.
        """
        r = MenaiVCodeReg(id=0)
        func = MenaiVCodeFunction(
            instrs=(
                MenaiVCodeLoadConst(dst=r, value=MenaiBoolean(True)),
                MenaiVCodeJumpIfTrue(cond=r, label="target"),
                MenaiVCodeLabel(name="target"),
                MenaiVCodeReturn(value=MenaiVCodeOperand.of_const(MenaiBoolean(True))),
            ),
            params=(),
            free_vars=(),
        )
        slot_map = SlotMap(slots={0: 0}, slot_count=1, local_count=1)

        result = peephole(func, slot_map)

        assert not any(isinstance(i, MenaiVCodeLoadConst) for i in result.instrs), (
            "the folded load should be removed"
        )
        assert not any(isinstance(i, MenaiVCodeJumpIfTrue) for i in result.instrs), (
            "the conditional should become unconditional"
        )

    def test_never_taken_removes_both(self):
        """
        LOAD_CONST #f r / JUMP_IF_TRUE r @L never jumps, so the load and the
        conditional are both removed and control falls through.
        """
        r = MenaiVCodeReg(id=0)
        func = MenaiVCodeFunction(
            instrs=(
                MenaiVCodeLoadConst(dst=r, value=MenaiBoolean(False)),
                MenaiVCodeJumpIfTrue(cond=r, label="target"),
                MenaiVCodeLabel(name="target"),
                MenaiVCodeReturn(value=MenaiVCodeOperand.of_const(MenaiBoolean(True))),
            ),
            params=(),
            free_vars=(),
        )
        slot_map = SlotMap(slots={0: 0}, slot_count=1, local_count=1)

        result = peephole(func, slot_map)

        assert not any(isinstance(i, MenaiVCodeLoadConst) for i in result.instrs)
        assert not any(isinstance(i, MenaiVCodeJumpIfTrue) for i in result.instrs)


class TestLoadKeptWhenRegisterUsedElsewhere:
    """A load is not removed while its register still has a reader."""

    def test_load_kept(self):
        """
        A boolean constant read by more than the branch keeps its load: the
        branch is folded but the load is not removed, because removing it
        would strand the other reader.
        """
        r = MenaiVCodeReg(id=0)
        func = MenaiVCodeFunction(
            instrs=(
                MenaiVCodeLoadConst(dst=r, value=MenaiBoolean(True)),
                MenaiVCodeJumpIfTrue(cond=r, label="target"),
                MenaiVCodeLabel(name="target"),
                MenaiVCodeReturn(value=MenaiVCodeOperand.of_reg(r)),
            ),
            params=(),
            free_vars=(),
        )
        slot_map = SlotMap(slots={0: 0}, slot_count=1, local_count=1)

        result = peephole(func, slot_map)

        assert any(isinstance(i, MenaiVCodeLoadConst) for i in result.instrs), (
            "the load must be kept while the register still has a reader"
        )


class TestCorrectness:
    """Verify the optimisation preserves program behaviour."""

    @pytest.fixture
    def menai(self):
        return Menai()

    def test_sink_correct(self, menai):
        """
        The loop sums the integers below its bound.
        """
        result = menai.evaluate(_SINK)
        assert result == 10

    def test_sink_zero_bound_correct(self, menai):
        """
        A loop whose bound is zero runs its body no times.
        """
        result = menai.evaluate(_SINK.replace("(f 5)", "(f 0)"))
        assert result == 0
