"""
Tests that every foldable opcode reads its constant-capable operands correctly.

An opcode whose Opcode.const_mask is non-zero may be emitted with a tag bit set
for a source position, meaning that position holds a constant-pool index rather
than a register slot.  The C VM must therefore resolve that position through the
operand() helper, which selects between the register file and the constant pool
according to the tag bit.

A case that reads frame_regs[srcN] directly for a position its mask permits to
be a constant reads a register slot that holds an unrelated value, and does so
silently: the opcode produces a wrong result rather than raising.  These tests
assert the C dispatch cases and the Python-side mask agree, so a newly foldable
opcode cannot be added without its C case being updated.
"""

import re
from pathlib import Path

import pytest

from menai.bytecode.menai_bytecode import Opcode


def _vm_source() -> str:
    """Return the text of the C VM dispatch source."""
    base = Path(__file__).resolve().parents[4]
    return (base / "src" / "menai" / "vm" / "menai_vm_c.c").read_text(encoding="utf-8")


def _case_body(source: str, opcode: Opcode) -> str | None:
    """
    Return the body of the `case OP_<NAME>: { ... }` block for opcode.

    The body runs from the case label to the closing brace at the same
    indentation.  Returns None if the opcode has no case block.
    """
    label = f"case OP_{opcode.name}:"
    start = source.find(label)
    if start < 0:
        return None

    end = source.find("\n        }", start)
    if end < 0:
        return None

    return source[start:end]


def _foldable_opcodes() -> list[Opcode]:
    """Return every opcode whose const_mask permits a constant operand."""
    return [op for op in Opcode if op.const_mask() != 0]


def test_at_least_one_foldable_opcode():
    """The foldable set is non-empty, so the guard tests below are meaningful."""
    assert _foldable_opcodes()


@pytest.mark.parametrize("opcode", _foldable_opcodes(), ids=lambda op: op.name)
def test_foldable_opcode_resolves_operands_through_helper(opcode: Opcode):
    """
    Every source position the opcode's mask permits to be a constant is read
    through operand() with the matching tag bit.
    """
    body = _case_body(_vm_source(), opcode)
    assert body is not None, f"{opcode.name} has no case block in menai_vm_c.c"

    mask = opcode.const_mask()
    for position in range(3):
        if not (mask >> position) & 1:
            continue

        expected = f"operand(frame, frame_regs, src{position}, tag & {1 << position})"
        assert expected in body, (
            f"{opcode.name} permits a constant in source position {position} "
            f"but its case does not read it through operand()"
        )


@pytest.mark.parametrize("opcode", _foldable_opcodes(), ids=lambda op: op.name)
def test_foldable_opcode_does_not_read_permitted_position_from_registers(opcode: Opcode):
    """
    A position the mask permits to be a constant must not be read directly from
    the register file, which would ignore the tag.
    """
    body = _case_body(_vm_source(), opcode)
    assert body is not None, f"{opcode.name} has no case block in menai_vm_c.c"

    mask = opcode.const_mask()
    for position in range(3):
        if not (mask >> position) & 1:
            continue

        direct = re.search(rf"frame_regs\[src{position}\]", body)
        assert direct is None, (
            f"{opcode.name} permits a constant in source position {position} "
            f"but its case also reads frame_regs[src{position}] directly"
        )
