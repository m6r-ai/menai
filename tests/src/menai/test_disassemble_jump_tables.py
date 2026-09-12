"""Tests for jump table output in the disassembler.

SWITCH_INTEGER instructions reference a jump table held on the code object
rather than carrying their targets inline.  The disassembler must dump every
table as a first-class section (min, default target, and one line per slot)
so arm destinations can be matched against the marked instruction indices,
and the inline SWITCH_INTEGER annotation must stay compact enough to remain
useful for large tables.
"""

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai_disassemble.disassemble import disassemble


def _code_with_table(min_value: int, default_target: int, targets: list[int]) -> CodeObject:
    """Build a minimal code object holding one jump table and a SWITCH_INTEGER."""
    table = (min_value, default_target, targets)
    instrs = [
        Instruction(opcode=Opcode.SWITCH_INTEGER, src0=0, src1=0),
        Instruction(opcode=Opcode.RETURN, src0=0),
    ]
    return CodeObject(
        instructions=instrs,
        constants=[],
        names=[],
        code_objects=[],
        jump_tables=[table],
        name="<test>",
    )


class TestJumpTableDump:
    """The Jump Tables section renders each slot and the default."""

    def test_jump_tables_section_present(self):
        """A code object with a jump table gets a Jump Tables section."""
        output = disassemble(_code_with_table(0, 9, [3, 6, 9]))
        assert "Jump Tables: 1" in output

    def test_header_shows_min_default_and_span(self):
        """The table header shows the minimum value, default target, and span."""
        output = disassemble(_code_with_table(1, 9, [3, 6, 9, 9]))
        assert "jt0: min=1  default=@9  span=1..4" in output

    def test_each_slot_listed_with_target(self):
        """Every non-default slot lists its value and target instruction index."""
        output = disassemble(_code_with_table(1, 9, [3, 6, 9, 9]))
        assert "1 : @3" in output
        assert "2 : @6" in output

    def test_default_collapsed_slots_render_as_wildcard(self):
        """Slots sharing the default target are rendered as a wildcard line."""
        output = disassemble(_code_with_table(1, 9, [3, 9, 9]))
        assert "  _ : @9  (default)" in output

    def test_no_section_without_tables(self):
        """A code object with no jump tables gets no Jump Tables section."""
        code = CodeObject(
            instructions=[Instruction(opcode=Opcode.RETURN, src0=0)],
            constants=[],
            names=[],
            code_objects=[],
            name="<test>",
        )
        assert "Jump Tables" not in disassemble(code)


class TestSwitchAnnotation:
    """The inline SWITCH_INTEGER annotation references the table compactly."""

    def test_annotation_references_table(self):
        """The annotation names the table, its value range, and the default."""
        output = disassemble(_code_with_table(1, 9, [3, 6, 9]))
        assert "SWITCH_INTEGER" in output
        assert "; see jt0: 1..3 -> arms, else @9" in output

    def test_annotation_stays_one_line_for_large_tables(self):
        """The annotation is a single line even for a maximum-size table."""
        targets = [10 * (i + 1) for i in range(4096)]
        output = disassemble(_code_with_table(0, 40960, targets))
        annotation_lines = [ln for ln in output.splitlines() if "; see jt0" in ln]
        assert len(annotation_lines) == 1
