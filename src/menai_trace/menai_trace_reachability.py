"""
Static reachability over a code object's bytecode.

Coverage answers which instructions executed; reachability answers which
instructions the VM could have executed, by following the control-flow edges the
VM itself follows.  Comparing the two splits the instructions a coverage report
lists as unexecuted into two populations: those the tests missed, and those no
execution can reach.

Reachability is deliberately syntactic.  A branch whose condition is constant
still has both successors, because the bytecode that remains after optimisation
is the program the VM ran; deciding that one edge is dead would be a second
constant-propagation pass with a different notion of "the program", and would
report an already-optimised branch as unreachable rather than reporting the
optimisation that failed to remove it.

The successor model classifies opcodes explicitly and treats every unclassified
opcode as falling through to the next instruction.  That is the shape of every
load, arithmetic, allocation and call instruction, so a newly added opcode is
modelled correctly without further work.  A newly added *control-flow* opcode
would not be, so `successor_kind` names the classification and the tests assert
that every opcode whose name denotes control flow has an explicit entry.
"""

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai_render.menai_render_instruction import instructions

JUMP = "jump"
BRANCH = "branch"
SWITCH = "switch"
TERMINATOR = "terminator"
FALLTHROUGH = "fallthrough"

_SUCCESSOR_KIND: dict[Opcode, str] = {
    Opcode.JUMP: JUMP,
    Opcode.JUMP_IF_FALSE: BRANCH,
    Opcode.JUMP_IF_TRUE: BRANCH,
    Opcode.RETURN_IF_FALSE: FALLTHROUGH,
    Opcode.RETURN_IF_TRUE: FALLTHROUGH,
    Opcode.SWITCH_INTEGER: SWITCH,
    Opcode.SWITCH_ENUM: SWITCH,
    Opcode.RETURN: TERMINATOR,
    Opcode.RAISE_ERROR: TERMINATOR,
    Opcode.TAIL_CALL: TERMINATOR,
    Opcode.TAIL_APPLY: TERMINATOR,
}

# The opcodes classified explicitly in the table above.  `RETURN_IF_FALSE` and
# `RETURN_IF_TRUE` are included because they return on one edge, even though
# their only in-function successor is the next instruction.
CONTROL_FLOW_OPCODES = frozenset(_SUCCESSOR_KIND)


def successor_kind(opcode: Opcode) -> str:
    """
    Return how control leaves an instruction with the given opcode.

    The result is one of `JUMP` (one target, no fallthrough), `BRANCH` (one
    target plus fallthrough), `SWITCH` (jump-table targets, no fallthrough),
    `TERMINATOR` (no in-function successor) or `FALLTHROUGH` (the next
    instruction only).

    `RETURN_IF_FALSE` and `RETURN_IF_TRUE` are fallthrough: the condition either
    leaves the function or continues at the next instruction, and the edge that
    leaves the function is not an in-function successor.
    """
    return _SUCCESSOR_KIND.get(opcode, FALLTHROUGH)


def successor_indices(code: CodeObject, index: int, instruction: Instruction) -> frozenset[int]:
    """
    Return the in-function instructions that can execute after `index`.

    Targets outside the instruction array are dropped rather than raising: a
    malformed jump target is a compiler fault, and a coverage report should not
    fail because of one.  The caller sees such a target simply as absent.

    Args:
        code: The code object the instruction belongs to.
        index: Index of the instruction within `code`.
        instruction: The already-unpacked instruction at `index`.

    Returns:
        The set of successor instruction indices, possibly empty.
    """
    count = len(code.instructions)
    opcode = Opcode(instruction.opcode)
    kind = successor_kind(opcode)

    targets: tuple[int, ...]
    if kind == JUMP:
        targets = (instruction.src0,)

    elif kind == BRANCH:
        targets = (instruction.src1,)

    elif kind == SWITCH:
        if instruction.src1 < len(code.jump_tables):
            _minimum, default_target, arms = code.jump_tables[instruction.src1]
            targets = (*arms, default_target)

        else:
            targets = ()

    else:
        targets = ()

    successors = {target for target in targets if 0 <= target < count}

    if kind in (FALLTHROUGH, BRANCH) and index + 1 < count:
        successors.add(index + 1)

    return frozenset(successors)


def reachable_indices(code: CodeObject) -> frozenset[int]:
    """
    Return every instruction index reachable from the function's entry point.

    Entry is index 0, which is where the VM begins a frame.  The walk is a
    depth-first search over `successor_indices`, so it costs one pass over the
    reachable instructions.

    Returns:
        The reachable indices as a frozenset, empty for a code object with no
        instructions.
    """
    count = len(code.instructions)
    if count == 0:
        return frozenset()

    unpacked = tuple(instructions(code))
    reachable: set[int] = set()
    pending = [0]

    while pending:
        index = pending.pop()
        if index in reachable:
            continue

        reachable.add(index)
        pending.extend(successor_indices(code, index, unpacked[index]) - reachable)

    return frozenset(reachable)
