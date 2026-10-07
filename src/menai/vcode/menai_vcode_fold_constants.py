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
definition in the function and that definition is a constant: a
MenaiVCodeLoadConst, a capture-less MenaiVCodeMakeClosure, or a
MenaiVCodeMove whose source is itself a constant register.

VCode registers are SSA value ids (see the allocator's module docstring), so a
register with a unique definition is dominated by that definition at every
use: the constant is the value the register holds wherever it is read.
A register with more than one definition — which arises when phi elimination
redefines a register in each arm — has no single reaching definition, so it is
left as a register.

The MOVE case matters because a constant frequently reaches its use through a
phi-elimination move.  A loop-carried variable that is never reassigned — for
example the function argument of an inlined higher-order function such as
fold-list — is initialised before the loop by a single move from the constant,
and the loop back-edge is a self-move that the VCode builder drops.  The
register therefore has a unique definition, but that definition is a MOVE, not
the constant load itself.  Propagating foldability along MOVE edges lets the
constant reach the use through such a chain.  Propagation runs to a fixed
point, so a chain of moves is followed to its constant source.

This rule is deliberately weaker than "no label between the load and the use",
which the coalesce_constants pass uses.  That rule is too strong here: a
LOAD_CONST in a straight-line prefix is not re-executed on a loop back-edge but
still dominates every use inside the loop, because it is the register's only
definition.  Folding must still recognise that case to reach loop counters,
which is the primary target of this optimisation.

A defining instruction is removed only when every use of its register has been
folded.  Because folding requires a unique definition, the definition's
lifetime is the whole function, so "every use" is simply every use of the
register id.  Removal runs to a fixed point: removing a MOVE that carried a
constant removes a use of the MOVE's source, which can in turn make the
source's own definition removable.

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
    MenaiVCodeMakeEnum,
    MenaiVCodeMove,
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
    constant definition — a LOAD_CONST, a capture-less MAKE_CLOSURE, or a MOVE
    from another constant register — the operand is rewritten to a constant and
    the defining instruction is removed if no uses of the register remain.

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
    # Three definitions yield a constant: a LOAD_CONST, a capture-less
    # MAKE_CLOSURE (a closure with no captures and no patching is a constant
    # MenaiFunction), and a MOVE from another constant register.
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

    # Propagate foldability along MOVE edges.  A register whose unique
    # definition is a MOVE from a constant register is itself constant.  The
    # propagation runs to a fixed point so a chain of moves is followed to its
    # constant source.
    changed = True
    while changed:
        changed = False
        for idx, instr in enumerate(instrs):
            if not isinstance(instr, MenaiVCodeMove):
                continue

            dst_id = instr.dst.id
            if dst_id in foldable or def_counts.get(dst_id, 0) != 1:
                continue

            src_operand = foldable.get(instr.src.id)
            if src_operand is None:
                continue

            foldable[dst_id] = src_operand
            const_index[dst_id] = idx
            changed = True

    if not foldable:
        return func

    # Fold every foldable operand use, recording the instruction index of each
    # fold so removal can tell which uses survive.
    folded_use_indices: dict[int, set[int]] = {}
    new_instrs: list[MenaiVCodeInstr] = []
    changed = False
    for idx, instr in enumerate(instrs):
        new_instr, folded_regs = _fold_instr(instr, foldable)
        for reg_id in folded_regs:
            folded_use_indices.setdefault(reg_id, set()).add(idx)

        new_instrs.append(new_instr)
        changed = changed or bool(folded_regs)

    if not changed:
        return func

    # A defining instruction is removed when every use of its register has been
    # folded.  Removing a MOVE removes a use of the MOVE's source, which can
    # make the source's definition removable in turn, so this runs to a fixed
    # point.
    use_indices: dict[int, list[int]] = {}
    for idx, instr in enumerate(instrs):
        _, uses = _defs_uses(instr)
        for reg_id in uses:
            if reg_id in foldable:
                use_indices.setdefault(reg_id, []).append(idx)

    remove_indices: set[int] = set()
    changed = True
    while changed:
        changed = False
        for reg_id in foldable:
            def_idx = const_index[reg_id]
            if def_idx in remove_indices:
                continue

            folded = folded_use_indices.get(reg_id, set())
            if all(
                use_idx in folded or use_idx in remove_indices
                for use_idx in use_indices.get(reg_id, [])
            ):
                remove_indices.add(def_idx)
                changed = True

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
) -> tuple[MenaiVCodeOperand, int | None]:
    """
    Return operand, folded to a constant if its register has a unique
    constant definition, together with the folded register's id.

    The register id is None when no fold occurred.  An operand that is already
    a constant, or whose register is not foldable, is returned unchanged.
    """
    if operand.is_const() or operand.reg is None:
        return operand, None

    const_operand = foldable.get(operand.reg.id)
    if const_operand is None:
        return operand, None

    return const_operand, operand.reg.id


def _fold_instr(
    instr: MenaiVCodeInstr,
    foldable: dict[int, MenaiVCodeOperand],
) -> tuple[MenaiVCodeInstr, set[int]]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten
    and folded is the set of register ids that were folded.

    Only the instruction types that carry foldable operands are handled; every
    other instruction is returned unchanged.  A builtin's foldable positions
    come from its opcode's const_mask; the other types have a fixed foldable
    position (a call's callee, a patch's value, a return's value).
    """
    if isinstance(instr, MenaiVCodeBuiltin):
        return _fold_builtin(instr, foldable)

    if isinstance(instr, MenaiVCodeCall):
        func, folded = _fold_operand(instr.func, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeCall(dst=instr.dst, func=func, args=instr.args), {folded}

    if isinstance(instr, MenaiVCodeTailCall):
        func, folded = _fold_operand(instr.func, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeTailCall(func=func, args=instr.args), {folded}

    if isinstance(instr, MenaiVCodePatchClosure):
        value, folded = _fold_operand(instr.value, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodePatchClosure(
            closure=instr.closure,
            capture_index=instr.capture_index,
            value=value,
        ), {folded}

    if isinstance(instr, MenaiVCodeMakeClosure):
        folded_captures: set[int] = set()
        new_captures: list[MenaiVCodeOperand] = []
        for capture in instr.captures:
            new_capture, folded_id = _fold_operand(capture, foldable)
            new_captures.append(new_capture)
            if folded_id is not None:
                folded_captures.add(folded_id)

        if not folded_captures:
            return instr, set()

        return MenaiVCodeMakeClosure(
            dst=instr.dst,
            function=instr.function,
            captures=tuple(new_captures),
            needs_patching=instr.needs_patching,
        ), folded_captures

    if isinstance(instr, MenaiVCodeReturn):
        value, folded = _fold_operand(instr.value, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeReturn(value=value), {folded}

    if isinstance(instr, MenaiVCodeMakeEnum):
        enum_type, folded = _fold_operand(instr.enum_type, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeMakeEnum(
            dst=instr.dst,
            enum_type=enum_type,
            variant_index=instr.variant_index,
        ), {folded}

    if isinstance(instr, MenaiVCodeReturnIf):
        value, folded = _fold_operand(instr.value, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeReturnIf(
            cond=instr.cond, value=value, when_true=instr.when_true,
        ), {folded}

    if isinstance(instr, MenaiVCodeRaise):
        message, folded = _fold_operand(instr.message, foldable)
        if folded is None:
            return instr, set()

        return MenaiVCodeRaise(message=message), {folded}

    return instr, set()


def _fold_builtin(
    instr: MenaiVCodeBuiltin,
    foldable: dict[int, MenaiVCodeOperand],
) -> tuple[MenaiVCodeBuiltin, set[int]]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten
    and folded is the set of register ids that were folded.

    An operand is foldable when its register has a unique constant definition
    and the opcode's const_mask permits a constant in that position.
    """
    const_mask = BUILTINS[instr.op].opcode.const_mask()
    if const_mask == 0:
        return instr, set()

    new_args: list[MenaiVCodeOperand] = []
    folded: set[int] = set()
    for position, arg in enumerate(instr.args):
        if not (const_mask >> position) & 1:
            new_args.append(arg)
            continue

        new_arg, folded_id = _fold_operand(arg, foldable)
        if folded_id is not None:
            folded.add(folded_id)

        new_args.append(new_arg)

    if not folded:
        return instr, set()

    return MenaiVCodeBuiltin(dst=instr.dst, op=instr.op, args=tuple(new_args)), folded
