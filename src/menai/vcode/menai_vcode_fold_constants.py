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
from menai.vcode.menai_vcode import (
    MenaiVCodeBuiltin,
    MenaiVCodeCall,
    MenaiVCodeFunction,
    MenaiVCodeInstr,
    MenaiVCodeLoadConst,
    MenaiVCodeMakeClosure,
    MenaiVCodeOperand,
    MenaiVCodePatchClosure,
    MenaiVCodeRaise,
    MenaiVCodeReturn,
    MenaiVCodeReturnIf,
    MenaiVCodeTailCall,
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

    # reg_id → constant operand, for every register whose unique definition is
    # a compile-time constant.  A register defined more than once is absent.
    #
    # Two definitions yield a constant: a LOAD_CONST, and a capture-less
    # MAKE_CLOSURE (a closure with no captures and no patching is a constant
    # MenaiFunction).
    foldable: dict[int, MenaiVCodeOperand] = {}
    # reg_id → index of the instruction that uniquely defines it as a constant.
    const_index: dict[int, int] = {}
    # reg_id → number of definitions seen so far.
    def_counts: dict[int, int] = {}

    for idx, instr in enumerate(instrs):
        defs, _ = _defs_uses(instr)
        for reg_id in defs:
            def_counts[reg_id] = def_counts.get(reg_id, 0) + 1

        if isinstance(instr, MenaiVCodeLoadConst):
            const_index[instr.dst.id] = idx

    for reg_id, count in def_counts.items():
        if count != 1 or reg_id not in const_index:
            continue

        const_instr = instrs[const_index[reg_id]]
        assert isinstance(const_instr, MenaiVCodeLoadConst)
        foldable[reg_id] = MenaiVCodeOperand.of_const(const_instr.value)

    # A capture-less MAKE_CLOSURE defines a constant function.  It is folded
    # only when the register is defined once, so the function value is
    # unambiguous at every use.
    for idx, instr in enumerate(instrs):
        if not isinstance(instr, MenaiVCodeMakeClosure):
            continue

        if instr.captures or instr.needs_patching:
            continue

        if def_counts.get(instr.dst.id, 0) != 1:
            continue

        foldable[instr.dst.id] = MenaiVCodeOperand.of_function(instr.function)
        const_index[instr.dst.id] = idx

    if not foldable:
        return func

    # Total uses of each foldable register, and how many were folded.
    total_uses: dict[int, int] = {}
    folded_uses: dict[int, int] = {}

    new_instrs: list[MenaiVCodeInstr] = []
    changed = False
    for instr in instrs:
        new_instr, folded = _fold_instr(instr, foldable, folded_uses)
        new_instrs.append(new_instr)
        changed = changed or folded

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
        const_index[reg_id]
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


def _fold_operand(
    operand: MenaiVCodeOperand,
    foldable: dict[int, MenaiVCodeOperand],
    folded_uses: dict[int, int],
) -> MenaiVCodeOperand:
    """
    Return operand, folded to a constant if its register has a unique
    constant definition.

    A fold increments the register's folded-use count in folded_uses.  An
    operand that is already a constant, or whose register is not foldable, is
    returned unchanged.
    """
    if operand.is_const() or operand.reg is None:
        return operand

    const_operand = foldable.get(operand.reg.id)
    if const_operand is None:
        return operand

    folded_uses[operand.reg.id] = folded_uses.get(operand.reg.id, 0) + 1
    return const_operand


def _fold_instr(
    instr: MenaiVCodeInstr,
    foldable: dict[int, MenaiVCodeOperand],
    folded_uses: dict[int, int],
) -> tuple[MenaiVCodeInstr, bool]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten.

    Only the instruction types that carry foldable operands are handled; every
    other instruction is returned unchanged.  A builtin's foldable positions
    come from its opcode's const_mask; the other types have a fixed foldable
    position (a call's callee, a patch's value, a return's value).
    """
    if isinstance(instr, MenaiVCodeBuiltin):
        return _fold_builtin(instr, foldable, folded_uses)

    if isinstance(instr, MenaiVCodeCall):
        func = _fold_operand(instr.func, foldable, folded_uses)
        if func is instr.func:
            return instr, False

        return MenaiVCodeCall(dst=instr.dst, func=func, args=instr.args), True

    if isinstance(instr, MenaiVCodeTailCall):
        func = _fold_operand(instr.func, foldable, folded_uses)
        if func is instr.func:
            return instr, False

        return MenaiVCodeTailCall(func=func, args=instr.args), True

    if isinstance(instr, MenaiVCodePatchClosure):
        value = _fold_operand(instr.value, foldable, folded_uses)
        if value is instr.value:
            return instr, False

        return MenaiVCodePatchClosure(
            closure=instr.closure,
            capture_index=instr.capture_index,
            value=value,
        ), True

    if isinstance(instr, MenaiVCodeMakeClosure):
        captures = tuple(
            _fold_operand(c, foldable, folded_uses) for c in instr.captures
        )
        if captures == instr.captures:
            return instr, False

        return MenaiVCodeMakeClosure(
            dst=instr.dst,
            function=instr.function,
            captures=captures,
            needs_patching=instr.needs_patching,
        ), True

    if isinstance(instr, MenaiVCodeReturn):
        value = _fold_operand(instr.value, foldable, folded_uses)
        if value is instr.value:
            return instr, False

        return MenaiVCodeReturn(value=value), True

    if isinstance(instr, MenaiVCodeReturnIf):
        value = _fold_operand(instr.value, foldable, folded_uses)
        if value is instr.value:
            return instr, False

        return MenaiVCodeReturnIf(
            cond=instr.cond, value=value, when_true=instr.when_true,
        ), True

    if isinstance(instr, MenaiVCodeRaise):
        message = _fold_operand(instr.message, foldable, folded_uses)
        if message is instr.message:
            return instr, False

        return MenaiVCodeRaise(message=message), True

    return instr, False


def _fold_builtin(
    instr: MenaiVCodeBuiltin,
    foldable: dict[int, MenaiVCodeOperand],
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
        if not (const_mask >> position) & 1:
            new_args.append(arg)
            continue

        new_arg = _fold_operand(arg, foldable, folded_uses)
        folded = folded or new_arg is not arg
        new_args.append(new_arg)

    if not folded:
        return instr, False

    return MenaiVCodeBuiltin(dst=instr.dst, op=instr.op, args=tuple(new_args)), True
