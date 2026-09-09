"""
Peephole optimiser for MenaiVCodeFunction.

Runs local pattern-matching optimisations over the flat VCode
instruction list.  Each sub-pass is applied repeatedly until no further
changes occur.

Sub-passes
----------
1. Redundant move elimination
   Removes MenaiVCodeMove instructions where src and dst have been assigned
   the same slot by the allocator.  These are no-ops produced by phi
   elimination when the incoming value and the phi result already share a
   slot.

   Requires a SlotMap — must run after slot allocation.

2. Jump-over-jump elimination
   Replaces the pattern:

       JUMP_IF_TRUE  r, @L1
       JUMP @L2

   with:

       JUMP_IF_FALSE r, @L2

   (and symmetrically for JUMP_IF_FALSE followed by JUMP).

   This eliminates the redundant unconditional jump that arises when the
   CFG has an empty arm block that was not bypassed (e.g. because it had a
   BranchTerm predecessor and a phi-bearing successor).  With the VCode
   pipeline this pattern can still arise when the false block falls through
   but the true block does not, and neither is the immediately next block
   in RPO order.

   Does not require a SlotMap — can run before or after slot allocation,
   but running after ensures the instruction indices used for jump targets
   are already resolved.  In practice this pass operates on label strings,
   not instruction indices, so it is independent of allocation.

3. Conditional-branch / load-const / return folding
   Replaces the pattern:

       JUMP_IF_TRUE  r, @L
       LOAD_CONST #f
       RETURN r2          (r2 is the same slot as r)

   with:

       JUMP_IF_TRUE  r, @L
       RETURN r

   (and symmetrically: JUMP_IF_FALSE / LOAD_CONST #t / RETURN → JUMP_IF_FALSE / RETURN)

   After BranchConstProp rewires a constant-false else-block to return #f
   directly, the vcode builder emits LOAD_CONST #f into a fresh register
   followed by RETURN that register.  But the condition register that was
   just tested already holds #f (we only reach this point because the branch
   was not taken, i.e. the condition was false).  The LOAD_CONST is therefore
   redundant — the RETURN can reuse the condition register directly.

   Requires a SlotMap — the slot comparison is needed to confirm that the
   LOAD_CONST destination and the RETURN value share the same slot as the
   branch condition (or that the RETURN value already is the condition
   register).

The three post-allocation sub-passes are composed and iterated to a joint
fixed point.

Pre-allocation pass
-------------------
schedule_self_loop_moves runs *before* slot allocation.  It reorders
independent instructions immediately before a self-loop move group so that
temp definitions move past reads of the param registers they will be moved
into.  This shortens the temp's live range, enabling the allocator's Phase 3b
to assign the temp directly to the param slot, which makes the MOVE a no-op
that the post-allocation redundant-move pass then eliminates.

The pattern (common in variadic prelude functions):

    t  = OP(... p ...)       ; defines temp t, reads param p
    p2 = OP(... p ...)       ; reads p (last use)
    p  = MOVE t              ; self-loop move
    JUMP __entry__

After reordering:

    p2 = OP(... p ...)       ; reads p (last use)
    t  = OP(... p ...)       ; defines t (p is now dead)
    p  = MOVE t              ; t can be assigned p's slot → no-op
    JUMP __entry__

The swap is safe because the two instructions are data-independent: neither
reads the other's def.  Menai purity guarantees there are no side-effect
ordering constraints.
"""


from menai.vcode.menai_vcode import (
    MenaiVCodeFunction,
    MenaiVCodeInstr,
    MenaiVCodeJump,
    MenaiVCodeJumpIfFalse,
    MenaiVCodeJumpIfTrue,
    MenaiVCodeLabel,
    MenaiVCodeLoadConst,
    MenaiVCodeLoadName,
    MenaiVCodeMove,
    MenaiVCodeReturn,
    MenaiVCodeApply,
    MenaiVCodeBuiltin,
    MenaiVCodeCall,
    MenaiVCodeGuard,
    MenaiVCodeMakeClosure,
    MenaiVCodeMakeDict,
    MenaiVCodeMakeList,
    MenaiVCodeMakeVector,
    MenaiVCodeMakeSet,
    MenaiVCodeMakeStruct,
    MenaiVCodePatchClosure,
    MenaiVCodeRaise,
    MenaiVCodeTailApply,
    MenaiVCodeTailCall,
)
from menai.menai_value import MenaiBoolean
from menai.vcode.menai_vcode_allocator import SlotMap


def schedule_self_loop_moves(func: MenaiVCodeFunction) -> MenaiVCodeFunction:
    """
    Reorder independent instructions before self-loop move groups.

    This is a pre-allocation pass.  It identifies the contiguous group of
    MenaiVCodeMove instructions that precede each JUMP __entry__ (self-loop
    back-edge) and reorders the instructions immediately before the move
    group so that definitions of move-source temps occur after the last read
    of the corresponding move-destination param.

    This enables the slot allocator's Phase 3b to assign the temp directly
    to the param slot, making the MOVE a same-slot no-op that the
    post-allocation peephole eliminates.

    Args:
        func: The VCode function to optimise (before slot allocation).

    Returns:
        A new MenaiVCodeFunction with instructions reordered.
        Returns func unchanged if no reordering applies.
    """
    instrs = list(func.instrs)
    any_changed = False
    changed = True
    while changed:
        changed = False
        for jump_idx, jump_instr in enumerate(instrs):
            if not isinstance(jump_instr, MenaiVCodeJump):
                continue

            if jump_instr.label != "__entry__":
                continue

            move_start = jump_idx - 1
            while move_start >= 0 and isinstance(instrs[move_start], MenaiVCodeMove):
                move_start -= 1

            move_start += 1
            if move_start == jump_idx:
                continue

            # Build move source → dest param map.
            move_src_to_dst: dict[int, int] = {}
            for idx in range(move_start, jump_idx):
                move = instrs[idx]
                assert isinstance(move, MenaiVCodeMove)
                move_src_to_dst[move.src.id] = move.dst.id

            # Scan backwards from move_start, looking for adjacent pairs
            # (A, B) where A defines a move source and B reads the
            # corresponding move dest.  Swap them if independent.
            scan_end = move_start
            for i in range(scan_end - 1, -1, -1):
                a = instrs[i]
                b = instrs[i + 1]

                if isinstance(a, _BARRIER_TYPES) or isinstance(b, _BARRIER_TYPES):
                    break

                a_defs, a_uses = _defs_uses(a)
                b_defs, b_uses = _defs_uses(b)

                # A must define a register that is a move source.
                swap_src = None
                for d in a_defs:
                    if d in move_src_to_dst:
                        swap_src = d
                        break

                if swap_src is None:
                    continue

                swap_dst = move_src_to_dst[swap_src]

                # B must read the corresponding move dest (param).
                if swap_dst not in b_uses:
                    continue

                # B must not read A's defs (no RAW).
                if any(d in b_uses for d in a_defs):
                    continue

                # A must not read B's defs (no WAR).
                if any(d in a_uses for d in b_defs):
                    continue

                # A and B must not write the same register (no WAW).
                if any(d in b_defs for d in a_defs):
                    continue

                # Safe to swap.
                instrs[i] = b
                instrs[i + 1] = a
                any_changed = True
                changed = True

    if not any_changed:
        return func

    return MenaiVCodeFunction(
        instrs=instrs,
        params=func.params,
        free_vars=func.free_vars,
        param_reg_ids=func.param_reg_ids,
        free_var_reg_ids=func.free_var_reg_ids,
        is_variadic=func.is_variadic,
        binding_name=func.binding_name,
        reg_count=func.reg_count,
        source_line=func.source_line,
        source_file=func.source_file,
    )


# Instruction types that cannot be reordered.
_BARRIER_TYPES = (
    MenaiVCodeLabel,
    MenaiVCodeJump,
    MenaiVCodeJumpIfTrue,
    MenaiVCodeJumpIfFalse,
    MenaiVCodeReturn,
    MenaiVCodeRaise,
)


def _defs_uses(instr: MenaiVCodeInstr) -> tuple[list[int], list[int]]:
    """
    Return (defs, uses) — lists of register ids defined and used by instr.

    This mirrors the allocator's _defs_uses but is duplicated here to keep
    the peephole module self-contained and avoid a cross-module dependency
    on an internal helper.
    """
    if isinstance(instr, MenaiVCodeLabel):
        return [], []

    if isinstance(instr, MenaiVCodeMove):
        return [instr.dst.id], [instr.src.id]

    if isinstance(instr, MenaiVCodeLoadConst):
        return [instr.dst.id], []

    if isinstance(instr, (MenaiVCodeJumpIfTrue, MenaiVCodeJumpIfFalse)):
        return [], [instr.cond.id]

    if isinstance(instr, MenaiVCodeReturn):
        return [], [instr.value.id]

    if isinstance(instr, MenaiVCodeRaise):
        return [], [instr.message.id]

    if isinstance(instr, MenaiVCodeGuard):
        return [], [instr.value.id]

    if isinstance(instr, MenaiVCodeBuiltin):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeCall):
        return [instr.dst.id], [instr.func.id] + [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeTailCall):
        return [], [instr.func.id] + [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeApply):
        return [instr.dst.id], [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiVCodeTailApply):
        return [], [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiVCodeMakeClosure):
        return [instr.dst.id], [r.id for r in instr.captures]

    if isinstance(instr, MenaiVCodePatchClosure):
        return [], [instr.closure.id, instr.value.id]

    if isinstance(instr, MenaiVCodeMakeStruct):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeList):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeVector):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeSet):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeDict):
        return [instr.dst.id], [r.id for k, v in instr.pairs for r in (k, v)]

    # MenaiVCodeJump, MenaiVCodeLoadName: no register references or defs only.
    if isinstance(instr, MenaiVCodeLoadName):
        return [instr.dst.id], []

    # MenaiVCodeJump: no register references.
    return [], []


def peephole(func: MenaiVCodeFunction, slot_map: SlotMap) -> MenaiVCodeFunction:
    """
    Apply peephole optimisations to func, iterating to a fixed point.

    Args:
        func:     The VCode function to optimise.
        slot_map: The slot allocation for func (needed for move elimination).

    Returns:
        A new MenaiVCodeFunction with redundant instructions removed.
        Returns func unchanged if no optimisations apply.
    """
    instrs = list(func.instrs)
    changed = True
    while changed:
        changed = False
        instrs, c = _eliminate_redundant_moves(instrs, slot_map)
        changed = changed or c
        instrs, c = _eliminate_jump_over_jump(instrs)
        changed = changed or c
        instrs, c = _fold_branch_load_return(instrs, slot_map)
        changed = changed or c

    if instrs is func.instrs:
        return func

    return MenaiVCodeFunction(
        instrs=instrs,
        params=func.params,
        free_vars=func.free_vars,
        is_variadic=func.is_variadic,
        binding_name=func.binding_name,
        reg_count=func.reg_count,
        source_line=func.source_line,
        source_file=func.source_file,
    )


def _eliminate_redundant_moves(
    instrs: list[MenaiVCodeInstr],
    slot_map: SlotMap,
) -> tuple[list[MenaiVCodeInstr], bool]:
    """
    Remove MenaiVCodeMove instructions where src and dst share a slot.

    These are produced by phi elimination when the incoming value and the
    phi result were assigned the same slot by the allocator.
    """
    result: list[MenaiVCodeInstr] = []
    changed = False
    for instr in instrs:
        if (
            isinstance(instr, MenaiVCodeMove)
            and slot_map.slots.get(instr.dst.id) == slot_map.slots.get(instr.src.id)
        ):
            changed = True

        else:
            result.append(instr)

    return result, changed


def _eliminate_jump_over_jump(
    instrs: list[MenaiVCodeInstr],
) -> tuple[list[MenaiVCodeInstr], bool]:
    """
    Replace JUMP_IF_TRUE/FALSE @L1 immediately followed by JUMP @L2 with
    the inverted conditional JUMP_IF_FALSE/TRUE @L2, removing the JUMP.

    Skips over intervening labels when determining adjacency — a label
    between the conditional and the unconditional jump is just a marker and
    does not affect the pattern.

    After removing the unconditional jump, any labels that pointed to it
    are left in place (they now point to the next real instruction), which
    is correct.
    """
    result: list[MenaiVCodeInstr] = []
    changed = False
    i = 0
    while i < len(instrs):
        instr = instrs[i]
        if isinstance(instr, (MenaiVCodeJumpIfTrue, MenaiVCodeJumpIfFalse)):
            # Look ahead past any labels to find the next non-label instruction.
            j = i + 1
            while j < len(instrs) and isinstance(instrs[j], MenaiVCodeLabel):
                j += 1

            if j < len(instrs) and isinstance(instrs[j], MenaiVCodeJump):
                jump = instrs[j]
                assert isinstance(jump, MenaiVCodeJump)
                if jump.label == "__entry__":
                    result.append(instr)
                    i += 1
                    continue

                # Found the pattern — invert the conditional and drop the JUMP.
                if isinstance(instr, MenaiVCodeJumpIfTrue):
                    result.append(MenaiVCodeJumpIfFalse(
                        cond=instr.cond, label=jump.label
                    ))

                else:
                    result.append(MenaiVCodeJumpIfTrue(
                        cond=instr.cond, label=jump.label
                    ))

                # Emit any intervening labels (they now sit between the new
                # conditional and whatever follows the removed JUMP).
                for k in range(i + 1, j):
                    result.append(instrs[k])

                # Skip past the JUMP.
                i = j + 1
                changed = True
                continue

        result.append(instr)
        i += 1

    return result, changed


def _fold_branch_load_return(
    instrs: list[MenaiVCodeInstr],
    slot_map: SlotMap,
) -> tuple[list[MenaiVCodeInstr], bool]:
    """
    Fold JUMP_IF_TRUE/FALSE r / LOAD_CONST bool / RETURN r2 into
    JUMP_IF_TRUE/FALSE r / RETURN r.

    When a conditional branch is immediately followed by a LOAD_CONST of the
    complementary boolean and then a RETURN of that constant, the load is
    redundant: the condition register already holds the correct value (we only
    reach the load because the branch was not taken, meaning the condition was
    false for JUMP_IF_TRUE, or true for JUMP_IF_FALSE).

    The LOAD_CONST destination and RETURN source must share a slot so that
    we know the RETURN is returning the loaded constant and nothing else.
    The loaded constant must be the boolean value the condition register is
    known to hold at that point (#f after JUMP_IF_TRUE, #t after
    JUMP_IF_FALSE).  Intervening labels are skipped when scanning ahead.
    """
    result: list[MenaiVCodeInstr] = []
    changed = False
    i = 0

    while i < len(instrs):
        instr = instrs[i]

        if not isinstance(instr, (MenaiVCodeJumpIfTrue, MenaiVCodeJumpIfFalse)):
            result.append(instr)
            i += 1
            continue

        # JUMP_IF_TRUE  r: taken when r is true  → fall-through when r is #f
        # JUMP_IF_FALSE r: taken when r is false → fall-through when r is #t
        expected_bool = not isinstance(instr, MenaiVCodeJumpIfTrue)

        # Scan ahead past labels to find LOAD_CONST then RETURN.
        j = i + 1
        while j < len(instrs) and isinstance(instrs[j], MenaiVCodeLabel):
            j += 1

        if j >= len(instrs) or not isinstance(instrs[j], MenaiVCodeLoadConst):
            result.append(instr)
            i += 1
            continue

        load = instrs[j]
        assert isinstance(load, MenaiVCodeLoadConst)

        # The loaded constant must be the expected boolean.
        if not (isinstance(load.value, MenaiBoolean) and load.value.value == expected_bool):
            result.append(instr)
            i += 1
            continue

        # Scan past any labels after the LOAD_CONST to find the RETURN.
        k = j + 1
        while k < len(instrs) and isinstance(instrs[k], MenaiVCodeLabel):
            k += 1

        if k >= len(instrs) or not isinstance(instrs[k], MenaiVCodeReturn):
            result.append(instr)
            i += 1
            continue

        ret = instrs[k]
        assert isinstance(ret, MenaiVCodeReturn)

        # The RETURN source must share a slot with the LOAD_CONST destination
        # (they may be different registers that the allocator assigned the same slot).
        load_slot = slot_map.slots.get(load.dst.id)
        ret_slot = slot_map.slots.get(ret.value.id)
        if load_slot != ret_slot:
            result.append(instr)
            i += 1
            continue

        # Pattern matched.  Emit the branch unchanged, emit any intervening
        # labels between the branch and the LOAD_CONST, drop the LOAD_CONST,
        # emit any intervening labels between LOAD_CONST and RETURN, then
        # emit RETURN using the condition register directly.
        result.append(instr)
        for m in range(i + 1, j):
            result.append(instrs[m])   # labels between branch and load

        for m in range(j + 1, k):
            result.append(instrs[m])   # labels between load and return

        result.append(MenaiVCodeReturn(value=instr.cond))
        i = k + 1
        changed = True

    return result, changed
