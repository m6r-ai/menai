"""
Tests for static reachability over bytecode.

Reachability is what separates an instruction the tests did not reach from one
no execution could reach.  These tests pin the successor model to the control
flow the VM implements, and pin the walk to following every edge, including
back edges and the arms of a jump table.
"""

import array

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai_render.menai_render_instruction import instructions
from menai_trace.menai_trace_reachability import (
    BRANCH,
    CONTROL_FLOW_OPCODES,
    FALLTHROUGH,
    JUMP,
    SWITCH,
    TERMINATOR,
    reachable_indices,
    successor_indices,
    successor_kind,
)

_CONTROL_FLOW_PREFIXES = ("JUMP", "RETURN", "RAISE", "TAIL", "SWITCH")


def _instruction(opcode: Opcode, dest: int = 0, src0: int = 0, src1: int = 0) -> Instruction:
    """Build an instruction with the given opcode and operands."""
    return Instruction(opcode=opcode.value, dest=dest, src0=src0, src1=src1)


def _code(*built: Instruction, jump_tables: tuple = ()) -> CodeObject:
    """Build a code object from instructions, with an optional jump table."""
    return CodeObject(
        instructions=list(built),
        constants=(),
        code_objects=(),
        jump_tables=jump_tables,
    )


def _successors(code: CodeObject, index: int) -> frozenset[int]:
    """Return the successor set of one instruction in a code object."""
    return successor_indices(code, index, tuple(instructions(code))[index])


class TestSuccessorKind:
    """Every opcode is classified, and control flow is classified explicitly."""

    def test_ordinary_opcodes_fall_through(self):
        assert successor_kind(Opcode.INTEGER_ADD) == FALLTHROUGH
        assert successor_kind(Opcode.LOAD_CONST) == FALLTHROUGH
        assert successor_kind(Opcode.CALL) == FALLTHROUGH

    def test_unconditional_jump_does_not_fall_through(self):
        assert successor_kind(Opcode.JUMP) == JUMP

    def test_conditional_jumps_branch(self):
        assert successor_kind(Opcode.JUMP_IF_FALSE) == BRANCH
        assert successor_kind(Opcode.JUMP_IF_TRUE) == BRANCH

    def test_conditional_returns_fall_through(self):
        assert successor_kind(Opcode.RETURN_IF_FALSE) == FALLTHROUGH
        assert successor_kind(Opcode.RETURN_IF_TRUE) == FALLTHROUGH

    def test_switches_dispatch_through_a_jump_table(self):
        assert successor_kind(Opcode.SWITCH_INTEGER) == SWITCH
        assert successor_kind(Opcode.SWITCH_ENUM) == SWITCH

    def test_returns_and_raises_terminate(self):
        assert successor_kind(Opcode.RETURN) == TERMINATOR
        assert successor_kind(Opcode.RAISE_ERROR) == TERMINATOR
        assert successor_kind(Opcode.TAIL_CALL) == TERMINATOR
        assert successor_kind(Opcode.TAIL_APPLY) == TERMINATOR

    def test_every_control_flow_opcode_is_modelled(self):
        unmodelled = sorted(
            opcode.name
            for opcode in Opcode
            if opcode.name.startswith(_CONTROL_FLOW_PREFIXES) and opcode not in CONTROL_FLOW_OPCODES
        )
        assert not unmodelled, f"control-flow opcodes with no successor model: {unmodelled}"

    def test_classification_values_are_known(self):
        known = {JUMP, BRANCH, SWITCH, TERMINATOR, FALLTHROUGH}
        for opcode in Opcode:
            assert successor_kind(opcode) in known


class TestSuccessors:
    """The successor set of an instruction follows the VM's control flow."""

    def test_fallthrough_is_the_next_instruction(self):
        code = _code(_instruction(Opcode.LOAD_NONE), _instruction(Opcode.LOAD_NONE))
        assert _successors(code, 0) == frozenset({1})

    def test_last_instruction_has_no_successor(self):
        code = _code(_instruction(Opcode.LOAD_NONE), _instruction(Opcode.LOAD_NONE))
        assert _successors(code, 1) == frozenset()

    def test_jump_targets_its_operand_only(self):
        code = _code(
            _instruction(Opcode.JUMP, src0=3),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
        )
        assert _successors(code, 0) == frozenset({3})

    def test_conditional_branch_has_two_successors(self):
        code = _code(
            _instruction(Opcode.JUMP_IF_FALSE, src1=3),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
        )
        assert _successors(code, 0) == frozenset({1, 3})

    def test_conditional_return_falls_through_only(self):
        code = _code(
            _instruction(Opcode.RETURN_IF_FALSE, src0=0, src1=1),
            _instruction(Opcode.RETURN),
        )
        assert _successors(code, 0) == frozenset({1})

    def test_terminator_has_no_successor(self):
        code = _code(_instruction(Opcode.LOAD_NONE), _instruction(Opcode.RETURN))
        assert _successors(code, 1) == frozenset()

    def test_switch_targets_every_arm_and_the_default(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.SWITCH_INTEGER, src1=0),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.RETURN),
            jump_tables=((0, 3, array.array("Q", [2])),),
        )
        assert _successors(code, 1) == frozenset({2, 3})

    def test_switch_does_not_fall_through(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.SWITCH_INTEGER, src1=0),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.RETURN),
            jump_tables=((0, 3, array.array("Q", [3])),),
        )
        assert _successors(code, 1) == frozenset({3})
        assert reachable_indices(code) == frozenset({0, 1, 3})

    def test_switch_without_a_jump_table_has_no_successor(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.SWITCH_INTEGER, src1=4),
            _instruction(Opcode.RETURN),
        )
        assert _successors(code, 1) == frozenset()

    def test_target_outside_the_instruction_array_is_dropped(self):
        code = _code(_instruction(Opcode.JUMP, src0=99), _instruction(Opcode.RETURN))
        assert _successors(code, 0) == frozenset()


class TestReachableIndices:
    """The walk follows every edge from entry and terminates on back edges."""

    def test_empty_code_object_has_no_reachable_instructions(self):
        assert reachable_indices(_code()) == frozenset()

    def test_straight_line_code_is_fully_reachable(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.LOAD_TRUE),
            _instruction(Opcode.RETURN),
        )
        assert reachable_indices(code) == frozenset({0, 1, 2})

    def test_instructions_after_a_return_are_unreachable(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
        )
        assert reachable_indices(code) == frozenset({0, 1})

    def test_instructions_skipped_by_a_jump_are_unreachable(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.JUMP, src0=3),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
        )
        assert reachable_indices(code) == frozenset({0, 1, 3})

    def test_jump_targets_are_reachable(self):
        code = _code(
            _instruction(Opcode.JUMP_IF_FALSE, src1=3),
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.RETURN),
        )
        assert reachable_indices(code) == frozenset({0, 1, 2, 3})

    def test_back_edge_terminates_and_covers_the_loop(self):
        code = _code(
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.JUMP_IF_FALSE, src1=3),
            _instruction(Opcode.JUMP, src0=0),
            _instruction(Opcode.RETURN),
        )
        assert reachable_indices(code) == frozenset({0, 1, 2, 3})

    def test_return_if_does_not_reach_past_the_next_instruction(self):
        code = _code(
            _instruction(Opcode.RETURN_IF_FALSE, src0=0, src1=1),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.LOAD_NONE),
        )
        assert reachable_indices(code) == frozenset({0, 1})
