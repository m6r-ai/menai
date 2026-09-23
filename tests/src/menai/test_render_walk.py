"""
Tests for the deterministic CodeObject tree walk.

The walk defines the canonical ordering of code objects and instructions within
a compiled program.  That ordering is the contract shared with the C VM's
instruction tracer: the C VM assigns the same ordinals during conversion so
that execution counts collected in C can be attributed back to individual
instructions.  These tests pin the ordering down.
"""

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai_render.menai_render_walk import (
    count_code_objects,
    count_instructions,
    walk_code_objects,
)


def _leaf(name: str, instruction_count: int = 1) -> CodeObject:
    """Build a code object with no children and the given instruction count."""
    return CodeObject(
        instructions=[Instruction(opcode=Opcode.RETURN, src0=0) for _ in range(instruction_count)],
        constants=[],
        code_objects=[],
        name=name,
    )


def _tree() -> CodeObject:
    """
    Build a tree with two levels of nesting.

    root
      child 0
        grandchild 0
      child 1
    """
    grandchild = _leaf("grandchild")
    child0 = CodeObject(
        instructions=[Instruction(opcode=Opcode.RETURN, src0=0)],
        constants=[],
        code_objects=[grandchild],
        name="child0",
    )
    child1 = _leaf("child1")
    return CodeObject(
        instructions=[Instruction(opcode=Opcode.RETURN, src0=0) for _ in range(2)],
        constants=[],
        code_objects=[child0, child1],
        name="root",
    )


class TestWalkOrder:
    """Code objects are visited depth-first, root first, children in list order."""

    def test_root_is_visited_first(self):
        visits = list(walk_code_objects(_tree()))
        assert visits[0].path == ()
        assert visits[0].code.name == "root"

    def test_depth_first_pre_order(self):
        """A child is visited before its own children, which precede later siblings."""
        visits = list(walk_code_objects(_tree()))
        names = [v.code.name for v in visits]
        assert names == ["root", "child0", "grandchild", "child1"]

    def test_paths_record_child_indices(self):
        visits = list(walk_code_objects(_tree()))
        paths = [v.path for v in visits]
        assert paths == [(), (0,), (0, 0), (1,)]

    def test_code_ordinals_are_visit_order(self):
        visits = list(walk_code_objects(_tree()))
        assert [v.code_ordinal for v in visits] == [0, 1, 2, 3]


class TestInstructionOrdinals:
    """Every instruction gets a global ordinal, contiguous across the tree."""

    def test_instr_base_is_contiguous(self):
        visits = list(walk_code_objects(_tree()))
        expected = 0
        for visit in visits:
            assert visit.instr_base == expected
            expected += visit.instruction_count

    def test_instr_base_accounts_for_sibling_instructions(self):
        """child1's base is offset by the root's and child0's instructions."""
        visits = list(walk_code_objects(_tree()))
        by_name = {v.code.name: v for v in visits}
        assert by_name["root"].instr_base == 0
        assert by_name["child0"].instr_base == 2
        assert by_name["grandchild"].instr_base == 3
        assert by_name["child1"].instr_base == 4

    def test_instruction_count_matches_code_object(self):
        visits = list(walk_code_objects(_tree()))
        for visit in visits:
            assert visit.instruction_count == len(visit.code.instructions)


class TestCounts:
    """The counting helpers agree with the walk."""

    def test_count_instructions(self):
        assert count_instructions(_tree()) == 5

    def test_count_code_objects(self):
        assert count_code_objects(_tree()) == 4

    def test_single_code_object(self):
        assert count_instructions(_leaf("only")) == 1
        assert count_code_objects(_leaf("only")) == 1


class TestEmptyCodeObject:
    """A code object with no instructions contributes nothing to the ordinal space."""

    def test_zero_instruction_code_object(self):
        empty = CodeObject(instructions=[], constants=[], code_objects=[], name="empty")
        visits = list(walk_code_objects(empty))
        assert len(visits) == 1
        assert visits[0].instruction_count == 0
        assert count_instructions(empty) == 0

    def test_zero_instruction_parent_does_not_offset_child(self):
        child = _leaf("child")
        parent = CodeObject(instructions=[], constants=[], code_objects=[child], name="parent")
        visits = list(walk_code_objects(parent))
        assert visits[1].instr_base == 0
