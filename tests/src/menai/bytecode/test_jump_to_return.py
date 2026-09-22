"""
Tests for the jump-to-return inlining VCode peephole optimisation.

When a label is immediately followed by a RETURN, every unconditional JUMP
targeting that label can be replaced by the RETURN itself, eliminating the
jump.  After inlining, labels that are no longer targeted by any jump (and
are not reachable via fall-through) are removed along with their now-dead
RETURN instruction.

This pattern arises when a CFG join block whose only content is a return is
lowered to a label followed by RETURN, with predecessor blocks jumping to
it.  It is the terminal-instruction analogue of jump threading, which
handles the case where the intermediate block ends in a JUMP instead.

Only unconditional jumps are handled — a conditional jump to a RETURN is
not inlined.

Covers:
  1. Basic inlining — a join block containing only a RETURN
  2. Correctness — results are identical with the optimisation
  3. Dead label removal — the unreferenced label and RETURN are dropped
  4. Conditional jumps to a RETURN are left alone
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


_READ_BIT = """
(lambda (state)
  (let ((b (dict-get state "bytes"))
        (pos (dict-get state "pos"))
        (bit (dict-get state "bit")))
    (if (integer>=? pos (bytes-length b))
        (error "unexpected end of input")
        (let ((value (integer-bit-and
                       (integer-bit-shift-right (bytes-ref b pos) bit)
                       1)))
          (if (integer=? bit 7)
              (list value (dict "bytes" b "pos" (integer+ pos 1) "bit" 0))
              (list value (dict "bytes" b "pos" pos "bit" (integer+ bit 1))))))))
"""


class TestJumpToReturnBasic:
    """A CFG join block containing only a RETURN."""

    def test_jump_to_join_is_inlined(self):
        """
        The inner if has one arm that must jump to the join block and one
        arm that falls through to it.  The join block contains only a
        RETURN.  Without inlining the arm that jumps emits a JUMP targeting
        the join label; after inlining the JUMP becomes the RETURN directly.

        The lambda contains no self-loop, so after inlining there should be
        no JUMP at all.
        """
        code = _compile(_READ_BIT)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        jump_count = _count_op(lam, Opcode.JUMP)
        assert jump_count == 0, (
            f"Expected 0 JUMP (join jump inlined), got {jump_count}.\n"
            f"Disassembly:\n{chr(10).join(_disasm(lam))}"
        )

    def test_dead_label_and_return_removed(self):
        """
        After inlining, the join label is no longer targeted by any jump and
        the RETURN it introduced is unreachable, so both are removed.
        """
        code = _compile(_READ_BIT)
        lam = _find_lambda(code, "<lambda")
        assert lam is not None, "lambda not found"
        disasm = "\n".join(_disasm(lam))
        assert "join" not in disasm, (
            f"Expected the dead join label to be removed.\nDisassembly:\n{disasm}"
        )


class TestJumpToReturnCorrectness:
    """Verify the optimisation preserves program behaviour."""

    @pytest.fixture
    def menai(self):
        return Menai()

    def test_result_correct_high_bit(self, menai):
        """
        With bit == 7 the state position advances and the bit counter resets.
        """
        result = menai.evaluate(
            f'({_READ_BIT} (dict "bytes" (string-hex->bytes "81") "pos" 0 "bit" 7))'
        )
        # The high bit of 0x81 is 1; pos advances to 1 and bit resets to 0.
        assert result == [1, {"bytes": b"\x81", "pos": 1, "bit": 0}]

    def test_result_correct_low_bit(self, menai):
        """
        With bit < 7 the state position is unchanged and the bit counter
        advances.
        """
        result = menai.evaluate(
            f'({_READ_BIT} (dict "bytes" (string-hex->bytes "02") "pos" 0 "bit" 0))'
        )
        # bit 0 of 0x02 is 0; pos stays 0 and bit advances to 1.
        assert result == [0, {"bytes": b"\x02", "pos": 0, "bit": 1}]

    def test_error_raised_on_eof(self, menai):
        """Reading past the end of the input raises an error."""
        with pytest.raises(Exception):
            menai.evaluate(
                f'({_READ_BIT} (dict "bytes" (string-hex->bytes "00") "pos" 1 "bit" 0))'
            )


class TestConditionalJumpToReturnNotInlined:
    """Conditional jumps to a RETURN are left in place."""

    def test_conditional_jump_preserved(self):
        """
        A conditional jump whose target is a RETURN cannot be inlined
        without duplicating the RETURN on the fall-through path, so the
        conditional jump must remain.
        """
        src = """
(lambda (x)
  (if (integer>? x 0)
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
