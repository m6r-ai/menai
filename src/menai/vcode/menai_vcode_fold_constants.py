"""
Constant folding into immediate operands for VCode.

Runs *before* slot allocation, immediately after coalesce_constants.

Motivation
----------
A constant that is not a singleton is materialised by the VCode builder as a
MenaiVCodeLoadConst into a scratch register, and that register is then read by
the consuming instruction.  For an opcode that can take a constant operand this
is pure overhead: a register slot is allocated, a LOAD_CONST is executed, and
the register file is read, all to obtain a value that is known at compile time.

This pass rewrites a foldable builtin operand from a register to a constant
operand.  The bytecode emitter then encodes the constant-pool index directly in
the instruction word and sets the corresponding tag bit, so the VM reads the
constant pool instead of the register file.  Because the rewrite happens before
slot allocation, the constant's register is never assigned a slot: the win is
both an eliminated instruction and eliminated register pressure.

Which positions are foldable
----------------------------
Each Opcode member carries a const_mask recording which source positions may
hold a constant-pool index.  This pass only folds a position the opcode's mask
permits; a position outside the mask is left as a register.  The mask is the
single source of truth shared with the bytecode emitter, the validator, and the
C VM.

Safety
------
A use of a constant register is folded only when the register has a *unique*
definition in the function and that definition is a MenaiVCodeLoadConst.

VCode registers are SSA value ids (see the allocator's module docstring), so a
register with a unique definition is dominated by that definition at every
use: the loaded constant is the value the register holds wherever it is read.
A register with more than one definition — which arises when phi elimination
redefines a register in each arm — has no single reaching definition, so it is
left as a register.

This rule is deliberately weaker than "no label between the load and the use",
which the coalesce_constants pass uses.  That rule is too strong here: a
LOAD_CONST in a straight-line prefix is not re-executed on a loop back-edge but
still dominates every use inside the loop, because it is the register's only
definition.  Folding must still recognise that case to reach loop counters,
which is the primary target of this optimisation.

A MenaiVCodeLoadConst instruction is removed only when every use of its
definition has been folded.  Because folding requires a unique definition, the
definition's lifetime is the whole function, so "every use" is simply every use
of the register id.

The pass is a pure transformation: it returns a new MenaiVCodeFunction and
never mutates its input.
"""

from menai.menai_builtin_registry import BUILTINS
from menai.menai_value import MenaiValue
from menai.vcode.menai_vcode import (
    MenaiVCodeBuiltin,
    MenaiVCodeFunction,
    MenaiVCodeInstr,
    MenaiVCodeLoadConst,
    MenaiVCodeOperand,
)
from menai.vcode.menai_vcode_allocator import _defs_uses


def fold_constants(func: MenaiVCodeFunction) -> MenaiVCodeFunction:
    """
    Fold constant loads into builtin operands where the opcode permits it.

    For each MenaiVCodeBuiltin whose opcode permits a constant operand in a
    given source position, when that operand's register has a unique
    MenaiVCodeLoadConst definition, the operand is rewritten to a constant and
    the load is removed if no uses of the register remain.

    Args:
        func: The VCode function to optimise (before slot allocation).

    Returns:
        A new MenaiVCodeFunction with constant operands folded.
        Returns func unchanged if no folding applies.
    """
    instrs = func.instrs

    # reg_id → constant value, for every register with a unique LOAD_CONST
    # definition.  A register defined more than once is absent.
    foldable: dict[int, MenaiValue] = {}
    # reg_id → index of the LOAD_CONST that uniquely defines it.
    load_index: dict[int, int] = {}
    # reg_id → number of definitions seen so far.
    def_counts: dict[int, int] = {}

    for idx, instr in enumerate(instrs):
        defs, _ = _defs_uses(instr)
        for reg_id in defs:
            def_counts[reg_id] = def_counts.get(reg_id, 0) + 1

        if isinstance(instr, MenaiVCodeLoadConst):
            load_index[instr.dst.id] = idx

    for reg_id, count in def_counts.items():
        if count == 1 and reg_id in load_index:
            load_idx = load_index[reg_id]
            load_instr = instrs[load_idx]
            assert isinstance(load_instr, MenaiVCodeLoadConst)
            foldable[reg_id] = load_instr.value

    if not foldable:
        return func

    # Total uses of each foldable register, and how many were folded.
    total_uses: dict[int, int] = {}
    folded_uses: dict[int, int] = {}

    new_instrs: list[MenaiVCodeInstr] = []
    changed = False
    for instr in instrs:
        if isinstance(instr, MenaiVCodeBuiltin):
            new_instr, folded = _fold_builtin(instr, foldable, folded_uses)
            new_instrs.append(new_instr)
            changed = changed or folded

        else:
            new_instrs.append(instr)

    if not changed:
        return func

    # Count total uses of each foldable register so a load can be dropped when
    # all of its register's uses were folded.
    for instr in instrs:
        _, uses = _defs_uses(instr)
        for reg_id in uses:
            if reg_id in foldable:
                total_uses[reg_id] = total_uses.get(reg_id, 0) + 1

    remove_indices = {
        load_index[reg_id]
        for reg_id in foldable
        if folded_uses.get(reg_id, 0) >= total_uses.get(reg_id, 0)
    }

    final_instrs = [
        instr for idx, instr in enumerate(new_instrs) if idx not in remove_indices
    ]

    return MenaiVCodeFunction(
        instrs=tuple(final_instrs),
        params=func.params,
        free_vars=func.free_vars,
        param_reg_ids=func.param_reg_ids,
        free_var_reg_ids=func.free_var_reg_ids,
        loop_param_reg_ids=func.loop_param_reg_ids,
        hoisted_reg_ids=func.hoisted_reg_ids,
        is_variadic=func.is_variadic,
        binding_name=func.binding_name,
        reg_count=func.reg_count,
        source_line=func.source_line,
        source_file=func.source_file,
    )


def _fold_builtin(
    instr: MenaiVCodeBuiltin,
    foldable: dict[int, MenaiValue],
    folded_uses: dict[int, int],
) -> tuple[MenaiVCodeBuiltin, bool]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten.

    An operand is foldable when its register has a unique LOAD_CONST definition
    and the opcode's const_mask permits a constant in that position.  Each fold
    increments the register's folded-use count in folded_uses.
    """
    const_mask = BUILTINS[instr.op].opcode.const_mask()
    if const_mask == 0:
        return instr, False

    new_args: list[MenaiVCodeOperand] = []
    folded = False
    for position, arg in enumerate(instr.args):
        if arg.is_const() or not (const_mask >> position) & 1:
            new_args.append(arg)
            continue

        assert arg.reg is not None
        value = foldable.get(arg.reg.id)
        if value is None:
            new_args.append(arg)
            continue

        new_args.append(MenaiVCodeOperand.of_const(value))
        folded_uses[arg.reg.id] = folded_uses.get(arg.reg.id, 0) + 1
        folded = True

    if not folded:
        return instr, False

    return MenaiVCodeBuiltin(dst=instr.dst, op=instr.op, args=tuple(new_args)), True
