"""
CFG pass: branch and return constant propagation.

When a phi node feeds directly into a MenaiCFGBranchTerm or
only as the value of a MenaiCFGReturnTerm, and some of its incoming values
are statically-known boolean constants, those arms do not need to flow
through the phi join at all.

For each such constant-valued incoming arm, this pass re-wires the *defining
block* of the constant (the block containing the MenaiCFGConstInstr) to jump
directly to the appropriate target, bypassing the phi join block entirely.
Using the defining block rather than the phi's recorded predecessor block
handles the case where empty intermediate blocks (left behind by
MenaiCFGCollapsePhiChains) sit between the defining block and the join.

Branch case (phi → BranchTerm):
  (phi result is the branch condition directly)
  An incoming True value means that predecessor always takes the true branch;
  an incoming False value means it always takes the false branch.  The
  defining block is rewired to jump directly to true_block or false_block.

Return case (phi → ReturnTerm):

  An incoming constant value means that predecessor always returns that
  constant.  The defining block is rewired to return the constant's own SSA
  value directly instead of jumping to the join.

Predicate-branch case (phi → type-predicate builtin → BranchTerm):

  The phi result feeds a single type-predicate builtin (e.g. none?, string?)
  whose result is the branch condition.  For each incoming value that is a
  statically-known constant, the predicate can be evaluated at compile time:
  e.g. (none? #none) is always True, (none? "t") is always False.  The
  defining block is rewired to jump directly to true_block or false_block,
  bypassing both the phi join and the predicate evaluation.

  This catches the common pattern where a match expression with a #none
  wildcard fallback is immediately tested with none?:

    (let ((unescaped (match esc ("\"" "\"") ... (_ #none))))
      (if (none? unescaped) ...))

  The matched arms produce known string constants; (none? <string>) is
  statically False, so they jump directly to the false branch.  The wildcard
  and type-guard-failure arms produce #none; (none? #none) is statically
  True, so they jump directly to the true branch.  The none? check and the
  entire join block become dead and are eliminated.

After all constant arms have been re-wired:

  - If the phi has no remaining incoming entries it is dead and the join
    block becomes unreachable.
  - If exactly one non-constant arm remains the phi is trivial (single
    predecessor) and the join block becomes an empty indirection that
    MenaiCFGSimplifyBlocks will eliminate on the next iteration.
  - If multiple non-constant arms remain the phi is retained with only
    those entries.

The pass runs to a fixed point within each function; a single round may
expose new candidates (e.g. after a join block is reduced to a single
incoming entry and then itself becomes a candidate).

This pass is designed to run after MenaiCFGCollapsePhiChains (which flattens
nested phi chains, producing the wide flat phis that are most amenable to
this optimisation) and before MenaiCFGSimplifyBlocks (which cleans up the
empty or trivial join blocks this pass leaves behind).

Example — branch case, the (or p1 (or p2 (or p3 p4))) pattern:

  Before (after CollapsePhiChains, with empty intermediate blocks still
  present):
    block3 (then for p1): const True  →  jump block9  (empty)
    block5 (then for p2): const True  →  jump block10 (empty)
    block7 (then for p3): const True  →  jump block11
    block8 (else for p3): %r = p4     →  jump block11
    block9:  jump block10  (empty)
    block10: jump block11  (empty)
    block11: %v = phi [True←3, True←5, True←7, %r←8]
             branch %v → loop_body / exit

  After (defining blocks re-wired to loop_body directly):
    block3: (empty) jump → loop_body
    block5: (empty) jump → loop_body
    block7: (empty) jump → loop_body
    block8: jump → block11   (non-constant, untouched)
    block9:  jump block10    (now unreachable — SimplifyBlocks cleans up)
    block10: jump block11    (now unreachable — SimplifyBlocks cleans up)
    block11: %v = phi [%r←8]
             branch %v → loop_body / exit

Example — return case, the (and A B) pattern:

  Before:
    then_block: %r = B  →  jump join
    else_block: %f = #f →  jump join
    join: %v = phi [%r←then, %f←else]
          return %v

  After (else_block rewired to return #f directly):
    then_block: %r = B  →  jump join
    else_block: return %f          (using %f, the SSA value defined in else_block)
    join: %v = phi [%r←then]   (trivial — SimplifyBlocks eliminates join)
          return %v
"""


from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGApplyInstr,
    MenaiCFGCallInstr,
    MenaiCFGBuiltinInstr,
    MenaiCFGBranchTerm,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGPhiInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGTailApplyTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGValue,
    MenaiCFGInstr,
    MenaiCFGTerminator,
    relink_predecessors,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGOptimizationPass
from menai.menai_value import (
    MenaiBoolean,
    MenaiBytes,
    MenaiComplex,
    MenaiDict,
    MenaiFloat,
    MenaiFunction,
    MenaiInteger,
    MenaiList,
    MenaiNone,
    MenaiSet,
    MenaiString,
    MenaiStruct,
    MenaiStructType,
    MenaiSymbol,
    MenaiValue,
)


class MenaiCFGBranchConstProp(MenaiCFGOptimizationPass):
    """
    Re-wire the defining blocks of phi incoming values that are
    statically-known constants, when the phi feeds a branch or return.
    or through a single type-predicate builtin into a branch.
    Branch case: for each phi P whose result is used only as the condition
    of a MenaiCFGBranchTerm in the same block, and for each incoming
    (val, pred) pair where val is a statically-known boolean constant:

      - Find the block that *defines* val (the block containing the
        MenaiCFGConstInstr for val).  This may differ from pred when empty
        intermediate blocks (left by an earlier CollapsePhiChains pass) sit
        between the defining block and the join.
      - Replace the defining block's jump-to-join with a direct jump to the
        branch's true_block (for True) or false_block (for False).
      - Remove the rewired entries from P's incoming list.

    Return case: for each phi P whose result is used only as the value of a
    MenaiCFGReturnTerm in the same block, and for each incoming (val, pred)
    pair where val is a statically-known constant (any type):

      - Find the defining block of val.
      - Replace the defining block's jump-to-join with a direct
        MenaiCFGReturnTerm returning val (the SSA value already defined in
        that block).
      - Remove the rewired entries from P's incoming list.

    In both cases, if P's incoming list becomes empty the phi and terminal
    are removed and the join block becomes dead (no predecessors will jump
    to it).
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        changed_overall = False

        while True:
            def_block_map = _build_def_block_map(func)
            round_changed = self._run_one_round(func, def_block_map)
            if not round_changed:
                break

            changed_overall = True

        if changed_overall:
            relink_predecessors(func)

        return func, changed_overall

    def _run_one_round(
        self,
        func: MenaiCFGFunction,
        def_block_map: dict[int, 'MenaiCFGBlock'],
    ) -> bool:
        """
        Execute one round of constant propagation.

        Scans every block for a qualifying phi+terminal pair and re-wires all
        constant-valued defining blocks that can safely bypass the join.

        Handles three qualification patterns (detected by
        _qualifying_phi_terminal):

        1. phi → BranchTerm:            constant arms must be booleans.
        2. phi → ReturnTerm:             constant arms can be any type.
        3. phi → predicate → BranchTerm: constant arms can be any type;
           the predicate is statically evaluated to determine the branch
           direction.  When the phi result is also used by downstream code
           (outside this block), only arms whose branch target does NOT use
           the phi result are re-wired; the rest stay in the phi so the value
           remains available.

        Returns True if any change was made.
        """
        const_values: dict[int, MenaiValue] = _collect_const_values(func)
        changed = False

        for block in func.blocks:
            result = _qualifying_phi_terminal(block)
            if result is None:
                continue

            phi, terminal, pred_instr = result

            # Determine whether the phi result is used outside this block.
            # If it is, we cannot fully eliminate the phi — downstream code
            # needs the value.  In that case we only re-wire arms to the
            # branch target that does NOT use the phi result, and keep the
            # remaining arms in the phi.
            predicate_name = pred_instr.op if pred_instr is not None else None
            phi_used_outside = (
                pred_instr is not None
                and _is_value_used_outside(func, block, phi.result)
            )

            # Determine which branch targets are safe to re-wire to.
            # A target is safe if the phi result is NOT used by any block
            # reachable from it.  When phi_used_outside is False, both
            # targets are safe (the phi result is only used by the predicate
            # in this block).  When phi_used_outside is True, we need to
            # check each target individually.
            if isinstance(terminal, MenaiCFGBranchTerm) and phi_used_outside:
                true_safe = not _is_value_used_in_subtree(
                    terminal.true_block, phi.result, block
                )
                false_safe = not _is_value_used_in_subtree(
                    terminal.false_block, phi.result, block
                )

            else:
                true_safe = True
                false_safe = True

            # Partition incoming entries into re-wirable and keep.
            # For constants, track both the defining block and the SSA value
            # itself (needed to construct the return terminator).  The defining
            # block may differ from the phi's recorded predecessor when empty
            # intermediate blocks sit between the definer and the join.
            #
            # For the direct branch case (no predicate), only boolean constants
            # are re-wirable — the branch condition must be True or False.
            # For the predicate-branch case, any known constant is re-wirable
            # because the predicate can be statically evaluated on it, BUT only
            # if the target branch doesn't use the phi result.
            # For the return case, any known constant is re-wirable.
            const_arms: list[tuple[MenaiCFGBlock, MenaiCFGValue]] = []
            keep: list[tuple[MenaiCFGValue, MenaiCFGBlock]] = []

            for val, pred in phi.incoming:
                if val.id in const_values and _is_rewirable(
                    const_values[val.id], terminal, predicate_name
                ) and _target_is_safe(
                    const_values[val.id], terminal, predicate_name,
                    true_safe, false_safe,
                ):
                    def_block = def_block_map.get(val.id, pred)
                    if isinstance(def_block.terminator, MenaiCFGJumpTerm):
                        const_arms.append((def_block, val))

                    else:
                        keep.append((val, pred))

                else:
                    keep.append((val, pred))

            if not const_arms:
                continue

            # Re-wire each constant defining block to bypass this join block.
            for def_block, const_ssa_val in const_arms:
                _rewire_predecessor(
                    def_block, terminal, const_ssa_val, const_values, predicate_name
                )

            # Update the phi's incoming list to only the non-constant arms.
            if keep:
                if len(keep) == 1:
                    # Single remaining entry — the phi is now trivial.  Replace
                    # the terminal's value/condition with the sole incoming value
                    # directly and remove the phi instruction entirely.
                    # For the predicate case, update the predicate's argument
                    # to the sole remaining value (the phi is gone but the
                    # predicate must still run at runtime on the non-constant
                    # value).
                    #
                    # However, when the sole remaining value is a known constant
                    # and there's a predicate, the predicate can be statically
                    # evaluated.  If the branch target uses the phi result, we
                    # must keep the phi (it provides the value) and replace the
                    # predicate + branch with a direct jump.  If the target does
                    # NOT use the phi result, we can remove the phi and re-wire
                    # the defining block directly.
                    sole_val, _ = keep[0]
                    if (
                        pred_instr is not None
                        and sole_val.id in const_values
                        and isinstance(terminal, MenaiCFGBranchTerm)
                    ):
                        assert predicate_name is not None
                        pred_result = _eval_predicate(
                            predicate_name, const_values[sole_val.id]
                        )
                        target = (
                            terminal.true_block if pred_result
                            else terminal.false_block
                        )
                        target_uses_phi = _is_value_used_in_subtree(
                            target, phi.result, block
                        )
                        if target_uses_phi:
                            # Keep the phi (needed for the value), but update
                            # its incoming list to only the remaining arms.
                            block.instrs[block.instrs.index(phi)] = MenaiCFGPhiInstr(
                                result=phi.result,
                                incoming=keep,
                            )
                            # Replace predicate + branch with a direct jump.
                            block.instrs.remove(pred_instr)
                            block.terminator = MenaiCFGJumpTerm(target=target)

                        else:
                            # Target doesn't need the phi — remove it and
                            # re-wire the sole remaining defining block directly.
                            block.instrs.remove(phi)
                            block.instrs.remove(pred_instr)
                            sole_def_block = def_block_map.get(sole_val.id)
                            if (
                                sole_def_block is not None
                                and isinstance(
                                    sole_def_block.terminator, MenaiCFGJumpTerm
                                )
                            ):
                                sole_def_block.terminator = (
                                    MenaiCFGJumpTerm(target=target)
                                )

                    else:
                        block.instrs.remove(phi)
                        if pred_instr is not None:
                            block.instrs[block.instrs.index(pred_instr)] = (
                                MenaiCFGBuiltinInstr(
                                    result=pred_instr.result,
                                    op=pred_instr.op,
                                    args=[sole_val],
                                )
                            )

                        elif isinstance(terminal, MenaiCFGBranchTerm):
                            block.terminator = MenaiCFGBranchTerm(
                                cond=sole_val,
                                true_block=terminal.true_block,
                                false_block=terminal.false_block,
                            )

                        else:
                            assert isinstance(terminal, MenaiCFGReturnTerm)
                            block.terminator = MenaiCFGReturnTerm(value=sole_val)

                else:
                    block.instrs[block.instrs.index(phi)] = MenaiCFGPhiInstr(
                        result=phi.result,
                        incoming=keep,
                    )

                    # When the phi feeds a predicate and all remaining arms
                    # are known constants that evaluate to the same predicate
                    # result, the predicate + branch is redundant — it always
                    # goes the same direction.  Replace the predicate and
                    # branch with a direct jump to that target.  The phi stays
                    # because downstream code may need its result.
                    if (
                        pred_instr is not None
                        and isinstance(terminal, MenaiCFGBranchTerm)
                        and all(v.id in const_values for v, _ in keep)
                    ):
                        assert predicate_name is not None
                        results = {
                            _eval_predicate(predicate_name, const_values[v.id])
                            for v, _ in keep
                        }
                        if len(results) == 1:
                            target = (
                                terminal.true_block if results.pop()
                                else terminal.false_block
                            )
                            block.instrs.remove(pred_instr)
                            block.terminator = MenaiCFGJumpTerm(target=target)

            else:
                # All arms were constant — all predecessors have been re-wired
                # away from this block.  Remove the phi so the block is
                # instruction-free (also remove the predicate instruction if
                # present).  Replace the stale terminal with a structurally
                # valid one; the block is now unreachable and SimplifyBlocks
                # will drop it.
                #
                # Note: this can only happen when phi_used_outside is False
                # (or when there's no predicate), because if the phi result is
                # used outside this block, at least one arm will be kept.
                block.instrs.remove(phi)
                if pred_instr is not None:
                    block.instrs.remove(pred_instr)

                if isinstance(terminal, MenaiCFGBranchTerm):
                    block.terminator = MenaiCFGJumpTerm(target=terminal.true_block)

                # For ReturnTerm the existing terminator is already valid as-is.

            changed = True

        return changed


def _build_def_block_map(func: MenaiCFGFunction) -> dict[int, 'MenaiCFGBlock']:
    """
    Return a map from SSA value id to the block that defines it, for every
    instruction in func that produces a result.
    """
    result: dict[int, MenaiCFGBlock] = {}
    for block in func.blocks:
        for instr in block.instrs:
            r = getattr(instr, 'result', None)
            if r is not None:
                result[r.id] = block

    return result


def _collect_const_values(func: MenaiCFGFunction) -> dict[int, MenaiValue]:
    """
    Return a map from SSA value id to MenaiValue for every
    MenaiCFGConstInstr anywhere in func.
    """
    result: dict[int, MenaiValue] = {}
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGConstInstr):
                result[instr.result.id] = instr.value

    return result


def _qualifying_phi_terminal(
    block: MenaiCFGBlock,
) -> 'tuple[MenaiCFGPhiInstr, MenaiCFGBranchTerm | MenaiCFGReturnTerm, MenaiCFGBuiltinInstr | None] | None':
    """
    Return (phi, terminal, pred_instr) if block qualifies for constant
    propagation, where pred_instr is None for the direct cases and a
    MenaiCFGBuiltinInstr for the predicate-branch case.

    Three qualification patterns:

      1. Direct branch: no patch_instrs, exactly one instruction (phi),
         terminator is BranchTerm whose cond is the phi result.
      2. Direct return: no patch_instrs, exactly one instruction (phi),
         terminator is ReturnTerm whose value is the phi result.
      3. Predicate branch: no patch_instrs, exactly two instructions (phi
         then a type-predicate builtin whose sole arg is the phi result),
         terminator is BranchTerm whose cond is the builtin result.

    Returns None if the block does not qualify.
    """
    if block.patch_instrs:
        return None

    if len(block.instrs) == 1:
        instr = block.instrs[0]
        if not isinstance(instr, MenaiCFGPhiInstr):
            return None

        term = block.terminator
        if isinstance(term, MenaiCFGBranchTerm) and term.cond.id == instr.result.id:
            return instr, term, None

        if isinstance(term, MenaiCFGReturnTerm) and term.value.id == instr.result.id:
            return instr, term, None

        return None

    if len(block.instrs) != 2:
        return None

    phi, builtin = block.instrs
    if not isinstance(phi, MenaiCFGPhiInstr):
        return None

    if not isinstance(builtin, MenaiCFGBuiltinInstr):
        return None

    if builtin.op not in _TYPE_PREDICATES:
        return None

    if len(builtin.args) != 1 or builtin.args[0].id != phi.result.id:
        return None

    term = block.terminator
    if isinstance(term, MenaiCFGBranchTerm) and term.cond.id == builtin.result.id:
        return phi, term, builtin

    return None


def _rewire_predecessor(
    def_block: MenaiCFGBlock,
    terminal: MenaiCFGBranchTerm | MenaiCFGReturnTerm,
    const_ssa_val: MenaiCFGValue,
    const_values: dict[int, MenaiValue],
    predicate_name: str | None,
) -> None:
    """
    Replace def_block's unconditional jump terminator with the appropriate
    bypass terminator, determined by the join block's terminal type:

    - BranchTerm: jump directly to true_block or false_block, bypassing the
      join.  When predicate_name is None the constant itself is the boolean
      condition; when it is set, the predicate is statically evaluated on
      the constant to determine the direction.
    - ReturnTerm: return const_ssa_val directly (the SSA value is already
      defined in def_block, so the register allocator can resolve it).

    Only MenaiCFGJumpTerm terminators are rewired.  If def_block already has
    a non-jump terminator (which cannot happen for a well-formed phi join
    produced by MenaiCFGBuilder, but is guarded here for safety) the
    terminator is left unchanged.
    """
    if not isinstance(def_block.terminator, MenaiCFGJumpTerm):
        return

    if isinstance(terminal, MenaiCFGBranchTerm):
        menai_val = const_values[const_ssa_val.id]
        if predicate_name is not None:
            result = _eval_predicate(predicate_name, menai_val)

        else:
            result = isinstance(menai_val, MenaiBoolean) and menai_val.value

        target = terminal.true_block if result else terminal.false_block
        def_block.terminator = MenaiCFGJumpTerm(target=target)
        # The constant instruction is now dead — its result was only ever
        # consumed by the phi, which has been removed.  Drop it so the
        # vcode builder does not emit a spurious LOAD_* instruction.
        def_block.instrs = [
            i for i in def_block.instrs
            if not (isinstance(i, MenaiCFGConstInstr) and i.result.id == const_ssa_val.id)
        ]

    else:
        assert isinstance(terminal, MenaiCFGReturnTerm)
        def_block.terminator = MenaiCFGReturnTerm(value=const_ssa_val)


_TYPE_PREDICATES: dict[str, tuple[type[MenaiValue], ...]] = {
    'none?': (MenaiNone,),
    'boolean?': (MenaiBoolean,),
    'integer?': (MenaiInteger,),
    'float?': (MenaiFloat,),
    'complex?': (MenaiComplex,),
    'string?': (MenaiString,),
    'bytes?': (MenaiBytes,),
    'list?': (MenaiList,),
    'dict?': (MenaiDict,),
    'set?': (MenaiSet,),
    'symbol?': (MenaiSymbol,),
    'function?': (MenaiFunction,),
    'struct?': (MenaiStruct,),
    'structtype?': (MenaiStructType,),
}


def _eval_predicate(name: str, val: MenaiValue) -> bool:
    """Statically evaluate a type predicate on a known constant value."""
    expected_types = _TYPE_PREDICATES[name]
    return isinstance(val, expected_types)


def _is_rewirable(
    val: MenaiValue,
    terminal: MenaiCFGBranchTerm | MenaiCFGReturnTerm,
    predicate_name: str | None,
) -> bool:
    """
    Return True if a known constant value can be re-wired for the given
    terminal pattern.

    - Direct branch (predicate_name is None, BranchTerm): only boolean
      constants are re-wirable — the value IS the branch condition.
    - Predicate branch (predicate_name set, BranchTerm): any constant is
      re-wirable — the predicate can be statically evaluated on it.
    - Return (ReturnTerm): any constant is re-wirable.
    """
    if isinstance(terminal, MenaiCFGReturnTerm):
        return True

    if predicate_name is not None:
        return True

    return isinstance(val, MenaiBoolean)


def _target_is_safe(
    val: MenaiValue,
    terminal: MenaiCFGBranchTerm | MenaiCFGReturnTerm,
    predicate_name: str | None,
    true_safe: bool,
    false_safe: bool,
) -> bool:
    """
    Return True if a known constant value can be safely re-wired to its
    branch target, given the safety flags for each target.

    For the direct branch and return cases, safety flags are always True
    (the phi result is not used outside the block).  For the predicate case,
    a target is safe only if the phi result is not used by any code reachable
    from that target.
    """
    if isinstance(terminal, MenaiCFGReturnTerm):
        return True

    if predicate_name is not None:
        result = _eval_predicate(predicate_name, val)
        return true_safe if result else false_safe

    return true_safe if (isinstance(val, MenaiBoolean) and val.value) else false_safe


def _is_value_used_outside(
    func: MenaiCFGFunction,
    exclude_block: MenaiCFGBlock,
    value: MenaiCFGValue,
) -> bool:
    """
    Return True if the given SSA value is used by any instruction or
    terminator in any block other than exclude_block.
    """
    for block in func.blocks:
        if block is exclude_block:
            continue

        for instr in block.instrs:
            for arg in _instr_args(instr):
                if arg.id == value.id:
                    return True

        term = block.terminator
        if term is not None:
            for arg in _term_args(term):
                if arg.id == value.id:
                    return True

    return False


def _is_value_used_in_subtree(
    entry: MenaiCFGBlock,
    value: MenaiCFGValue,
    exclude_block: MenaiCFGBlock,
) -> bool:
    """
    Return True if the given SSA value is used by any block reachable from
    entry (excluding exclude_block and blocks that jump back to it).

    Performs a BFS from entry, following jump and branch targets, but not
    self-loop or raise terminators.  Stops at exclude_block to avoid
    scanning the join block itself.
    """
    visited: set[int] = set()
    queue: list[MenaiCFGBlock] = [entry]

    while queue:
        block = queue.pop()
        if block.id in visited or block.id == exclude_block.id:
            continue

        visited.add(block.id)

        for instr in block.instrs:
            for arg in _instr_args(instr):
                if arg.id == value.id:
                    return True

        term = block.terminator
        if term is not None:
            for arg in _term_args(term):
                if arg.id == value.id:
                    return True

            for target in _term_targets(term):
                if target.id not in visited and target.id != exclude_block.id:
                    queue.append(target)

    return False


def _instr_args(instr: MenaiCFGInstr) -> list[MenaiCFGValue]:
    """Return all SSA value references in an instruction's arguments."""
    if isinstance(instr, MenaiCFGPhiInstr):
        return [val for val, _ in instr.incoming]

    if isinstance(instr, MenaiCFGBuiltinInstr):
        return list(instr.args)

    if isinstance(instr, MenaiCFGCallInstr):
        return [instr.func] + list(instr.args)

    if isinstance(instr, MenaiCFGApplyInstr):
        return [instr.func, instr.arg_list]

    if isinstance(instr, MenaiCFGMakeClosureInstr):
        return list(instr.captures)

    if isinstance(instr, MenaiCFGMakeListInstr):
        return list(instr.args)

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return list(instr.args)

    if isinstance(instr, MenaiCFGMakeDictInstr):
        result: list[MenaiCFGValue] = []
        for k, v in instr.pairs:
            result.extend([k, v])

        return result

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return list(instr.args)

    return []


def _term_args(term: MenaiCFGTerminator) -> list[MenaiCFGValue]:
    """Return all SSA value references in a terminator."""
    if isinstance(term, MenaiCFGBranchTerm):
        return [term.cond]

    if isinstance(term, MenaiCFGReturnTerm):
        return [term.value]

    if isinstance(term, MenaiCFGTailCallTerm):
        return [term.func] + list(term.args)

    if isinstance(term, MenaiCFGTailApplyTerm):
        return [term.func, term.arg_list]

    if isinstance(term, MenaiCFGSelfLoopTerm):
        return list(term.args)

    if isinstance(term, MenaiCFGRaiseTerm):
        return [term.message]

    return []


def _term_targets(term: MenaiCFGTerminator) -> list[MenaiCFGBlock]:
    """Return all block targets from a terminator."""
    if isinstance(term, MenaiCFGJumpTerm):
        return [term.target]

    if isinstance(term, MenaiCFGBranchTerm):
        return [term.true_block, term.false_block]

    if isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
        return [term.target]

    return []
