"""
Resolution of raw VM trace data into per-instruction and per-function records.

The C VM reports execution counts as two flat arrays in the canonical walk
order defined by menai_render.menai_render_walk.walk_code_objects:

    instr_counts[i] — executions of the instruction with global ordinal i
    call_counts[c]  — calls to the code object with code ordinal c

This module resolves those ordinals back to code objects and instruction
indices, so the counts can be attributed to specific instructions and functions.
"""

from dataclasses import dataclass, field

from menai.bytecode.menai_bytecode import CodeObject, Instruction
from menai_render.menai_render_instruction import instructions
from menai_render.menai_render_walk import walk_code_objects


@dataclass(frozen=True, slots=True)
class InstructionTrace:
    """Execution count for one instruction, resolved to its code object."""

    index: int
    instruction: Instruction
    count: int


@dataclass(frozen=True, slots=True)
class FunctionTrace:
    """
    Trace data for one code object.

    path          — child indices from the root to this code object.
    ordinal       — this code object's code ordinal.
    code          — the code object itself.
    calls         — number of times this code object was called.
    instructions  — per-instruction counts, in instruction order.
    """

    path: tuple[int, ...]
    ordinal: int
    code: CodeObject
    calls: int
    instructions: tuple[InstructionTrace, ...]

    def total_executed(self) -> int:
        """Return the total number of instructions executed in this code object."""
        return sum(trace.count for trace in self.instructions)


@dataclass(frozen=True, slots=True)
class TraceResult:
    """
    A complete instruction and call trace for one execution.

    functions — one FunctionTrace per code object, in canonical walk order.
    """

    functions: tuple[FunctionTrace, ...] = field(default_factory=tuple)

    def total_instructions(self) -> int:
        """Return the total number of instructions executed across the program."""
        return sum(function.total_executed() for function in self.functions)

    def total_calls(self) -> int:
        """Return the total number of function calls across the program."""
        return sum(function.calls for function in self.functions)


def resolve_trace(
    root: CodeObject,
    instr_counts: list[int],
    call_counts: list[int],
) -> TraceResult:
    """
    Resolve flat ordinal arrays into a structured TraceResult.

    Raises:
        ValueError: If the array lengths do not match the code tree.  This
            indicates the counts were collected against a different program
            than the one supplied, which would silently misattribute every
            count, so it is treated as an error rather than tolerated.
    """
    visits = list(walk_code_objects(root))
    expected_instr = sum(visit.instruction_count for visit in visits)
    expected_code = len(visits)

    if len(instr_counts) != expected_instr:
        raise ValueError(
            f"instruction count array has {len(instr_counts)} entries "
            f"but the code tree has {expected_instr} instructions"
        )

    if len(call_counts) != expected_code:
        raise ValueError(
            f"call count array has {len(call_counts)} entries "
            f"but the code tree has {expected_code} code objects"
        )

    functions: list[FunctionTrace] = []
    for visit in visits:
        per_instruction: list[InstructionTrace] = []
        for index, instruction in enumerate(instructions(visit.code)):
            per_instruction.append(
                InstructionTrace(
                    index=index,
                    instruction=instruction,
                    count=instr_counts[visit.instr_base + index],
                )
            )

        functions.append(
            FunctionTrace(
                path=visit.path,
                ordinal=visit.code_ordinal,
                code=visit.code,
                calls=call_counts[visit.code_ordinal],
                instructions=tuple(per_instruction),
            )
        )

    return TraceResult(functions=tuple(functions))
