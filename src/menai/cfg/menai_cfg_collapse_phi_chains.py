"""
CFG pass: collapse phi chains and eliminate trivial phis.

Two transformations run to a joint fixed point.

1. Phi-chain collapsing
   Eliminates phi-of-phi redundancy that arises from nested `if`
   expressions.  When the result of a phi node is used *only* as an incoming
   value in one or more other phi nodes, the intermediate phi can be
   bypassed: each consuming phi absorbs the intermediate's incoming entries
   in its place.

Example before:

    block A:  jump → join1
    block B:  jump → join1
    join1:    %v1 = phi [(%a, A), (%b, B)]
              jump → join2
    join2:    %v2 = phi [(%v1, join1), (%c, C)]

Example after:

    join2:    %v2 = phi [(%a, A), (%b, B), (%c, C)]

join1's phi is removed.  If join1 now has no instructions it becomes an
empty block, which MenaiCFGSimplifyBlocks will then eliminate.

2. Trivial-phi elimination
   Removes a phi node whose incoming values are all the same SSA value.
   Such a phi always yields that value, so every use of the phi result is
   replaced by the value and the phi is deleted.  The classic case is a
   loop-exit join where both exits carry the same variable:

       then block:  jump → join
       else block:  jump → join
       join:  %r = phi [(%i, then), (%i, else)]
              ... use %r ...

   Here %r is just %i.  Removing the phi makes the two predecessor blocks
   empty indirections, which MenaiCFGSimplifyBlocks then eliminates, so the
   VCode builder emits no redundant phi-elimination MOVE for them.

Safety
------
The chain-collapse transformation is valid when:
  1. The intermediate phi result (%v1) is used *only* as a phi incoming
     value — never in a builtin, call, return, branch condition, etc.
  2. No consuming phi already has an entry from one of the intermediate
     phi's predecessor blocks (which would create a duplicate predecessor,
     violating the one-entry-per-predecessor invariant for phi nodes).

Condition 2 can arise when a consuming phi has multiple entries that would
expand to the same predecessor block.  The pass skips any collapse that
would produce such a conflict.

The trivial-phi transformation is unconditionally valid: a phi whose every
incoming value is the same SSA value denotes that value on every path that
reaches it, so substituting it at every use preserves the program's meaning.
A phi that is a loop-carried variable (named by a
MenaiCFGSelfLoopTerm.param_vals) is never removed: the back-edge machinery
and the slot allocator name it by identity.

Menai is pure, so dead-code elimination is always safe (AGENTS.md).
"""


from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGApplyInstr,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGPhiInstr,
    MenaiCFGGuardInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructWithIndexedInstr,
    MenaiCFGSwitchTerm,
    MenaiCFGTailApplyTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGTerminator,
    MenaiCFGValue,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    replace_block_instrs,
    replace_blocks,
)


class MenaiCFGCollapsePhiChains(MenaiCFGPerFunctionPass):
    """
    Replace phi-of-phi chains with a single flat phi, and remove phi nodes
    that are redundant.

    For each phi P1 whose result is used *only* as an incoming value in
    other phi nodes (or not at all), expand each consuming phi by
    substituting P1's incoming entries for the P1 reference, then remove
    P1.

    A phi whose incoming values are all the same SSA value is also redundant:
    it always yields that value, so every use of its result is replaced by
    the value and the phi is removed.  A phi that is a loop-carried variable
    (named by a MenaiCFGSelfLoopTerm.param_vals) is never removed this way.

    After collapsing, blocks that contained only the now-removed phi (and
    an unconditional jump) become empty and will be eliminated by
    MenaiCFGSimplifyBlocks in the next pass.

    Rebuilds any block whose instruction list changes; the input is not mutated.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        changed_overall = False

        # Iterate to fixed point: each round may expose new candidates.
        # Trivial-phi elimination runs first because removing a trivial phi
        # can expose a phi-chain collapse (its result may have fed a
        # consuming phi) and vice versa.
        while True:
            func, trivial_changed = self._eliminate_trivial_phis(func)
            func, chain_changed = self._run_one_round(func)
            if not (trivial_changed or chain_changed):
                break

            changed_overall = True

        return func, changed_overall

    @staticmethod
    def _is_pass_through(block: MenaiCFGBlock) -> bool:
        """
        Return True if a block is a pass-through: it contains only phi
        instructions and ends in an unconditional jump.

        Collapsing a phi chain through a non-pass-through block is unsound:
        the intermediate block does real work, so control still flows through
        it and the intermediate phi's predecessor blocks are not predecessors
        of the consuming block.
        """
        if block.patch_instrs:
            return False

        if not isinstance(block.terminator, MenaiCFGJumpTerm):
            return False

        return all(isinstance(instr, MenaiCFGPhiInstr) for instr in block.instrs)

    def _eliminate_trivial_phis(
        self,
        func: MenaiCFGFunction,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Remove phi nodes whose incoming values are all the same SSA value.

        Such a phi always yields that value, so every use of the phi result
        is replaced by the value and the phi is deleted.  This is the classic
        loop-exit join where both exits carry the same variable, and it makes
        the predecessor blocks' phi-elimination moves disappear from the
        VCode.

        A phi whose result is a loop-carried variable (named by a
        MenaiCFGSelfLoopTerm.param_vals) is skipped: the back-edge machinery
        and the slot allocator name it by identity.

        Returns the (possibly new) function and whether any change was made.
        """
        loop_carried_ids: set[int] = set()
        for block in func.blocks:
            term = block.terminator
            if isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is not None:
                for param_val in term.param_vals:
                    loop_carried_ids.add(param_val.id)

        # Map phi result id -> the single incoming value it always yields.
        replacements: dict[int, MenaiCFGValue] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGPhiInstr):
                    continue

                if instr.result.id in loop_carried_ids:
                    continue

                incoming_ids = {val.id for val, _ in instr.incoming}
                if len(incoming_ids) != 1:
                    continue

                only_id = next(iter(incoming_ids))
                if only_id == instr.result.id:
                    # A self-referential single incoming is degenerate; leave it.
                    continue

                replacements[instr.result.id] = next(
                    val for val, _ in instr.incoming if val.id == only_id
                )

        if not replacements:
            return func, False

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            new_instrs: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr) and instr.result.id in replacements:
                    continue

                new_instrs.append(_substitute_value_in_instr(instr, replacements))

            terminator = block.terminator
            if terminator is not None:
                terminator = _substitute_value_in_term(terminator, replacements)

            new_blocks.append(replace(
                block,
                instrs=tuple(new_instrs),
                terminator=terminator,
            ))

        return replace_blocks(func, tuple(new_blocks)), True

    def _run_one_round(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Execute one round of phi-chain collapsing.

        Returns the (possibly new) function and whether any change was made.
        """
        # Build a map: value id → the phi instruction that defines it, and a
        # map: value id → the block containing that phi.
        phi_defs: dict[int, MenaiCFGPhiInstr] = {}
        phi_blocks: dict[int, MenaiCFGBlock] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr):
                    phi_defs[instr.result.id] = instr
                    phi_blocks[instr.result.id] = block

        if not phi_defs:
            return func, False

        # Count all uses of each phi result, distinguishing phi-incoming
        # uses from all other uses.
        total_uses: dict[int, int] = {vid: 0 for vid in phi_defs}
        phi_uses: dict[int, int] = {vid: 0 for vid in phi_defs}

        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr):
                    for incoming_val, _ in instr.incoming:
                        if incoming_val.id in phi_defs:
                            total_uses[incoming_val.id] += 1
                            phi_uses[incoming_val.id] += 1

                else:
                    for vid in value_ids_in_instr(instr):
                        if vid in phi_defs:
                            total_uses[vid] += 1

            for patch in block.patch_instrs:
                for vid in (patch.closure.id, patch.value.id):
                    if vid in phi_defs:
                        total_uses[vid] += 1

            term = block.terminator
            if term is not None:
                for vid in value_ids_in_term(term):
                    if vid in phi_defs:
                        total_uses[vid] += 1

        # Candidates: phi results whose every use is as a phi incoming value
        # (including zero uses — those are simply dead).
        candidates: set[int] = {
            vid
            for vid in phi_defs
            if total_uses[vid] == phi_uses[vid]
        }

        if not candidates:
            return func, False

        changed = False

        # Phase 1: expand candidate phi references in consuming phis.
        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            new_instrs: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGPhiInstr):
                    new_instrs.append(instr)
                    continue

                # Build the full set of predecessor block ids that this phi
                # will have after expansion, for conflict detection.
                # We compute it upfront from all non-candidate entries plus
                # the expanded entries of each candidate entry.
                non_candidate_preds = {
                    pred
                    for val, pred in instr.incoming
                    if val.id not in candidates
                }

                expanded_incoming: list[tuple[MenaiCFGValue, int]] = []
                instr_changed = False

                for incoming_val, pred_block in instr.incoming:
                    if incoming_val.id not in candidates:
                        expanded_incoming.append((incoming_val, pred_block))
                        continue

                    src_phi = phi_defs[incoming_val.id]

                    # The collapse is only valid when the intermediate phi's
                    # block is a pass-through: it contains nothing but phi
                    # instructions and an unconditional jump.  Only then is the
                    # intermediate block bypassed on every path, so the
                    # consuming phi's incoming entries can be replaced by the
                    # intermediate phi's entries.  If the intermediate block
                    # does real work (e.g. a loop pre-header that computes the
                    # loop's initial values), control still flows through it and
                    # the expanded predecessor blocks would not be predecessors
                    # of the consuming block.
                    if not self._is_pass_through(phi_blocks[incoming_val.id]):
                        expanded_incoming.append((incoming_val, pred_block))
                        continue

                    # Conflict check: would any of src_phi's predecessor blocks
                    # already appear in the final phi (from non-candidate entries
                    # or from already-expanded candidate entries)?
                    already_present = {b for _, b in expanded_incoming}
                    src_pred_ids = {b for _, b in src_phi.incoming}
                    if already_present & src_pred_ids or non_candidate_preds & src_pred_ids:
                        # Conflict: keep this entry unexpanded.
                        expanded_incoming.append((incoming_val, pred_block))
                        continue

                    expanded_incoming.extend(src_phi.incoming)
                    instr_changed = True

                if instr_changed:
                    new_instrs.append(
                        MenaiCFGPhiInstr(result=instr.result, incoming=tuple(expanded_incoming))
                    )
                    changed = True

                else:
                    new_instrs.append(instr)

            new_blocks.append(replace_block_instrs(block, tuple(new_instrs)))

        if not changed:
            # No expansions happened, but there may be zero-use candidates
            # to remove. Check for those.
            dead = {vid for vid in candidates if total_uses[vid] == 0}
            if dead:
                pruned = tuple(
                    replace_block_instrs(
                        block,
                        tuple(
                            instr for instr in block.instrs
                            if not (
                                isinstance(instr, MenaiCFGPhiInstr)
                                and instr.result.id in dead
                            )
                        ),
                    )
                    for block in func.blocks
                )
                return replace_blocks(func, pruned), True

            return func, False

        # Phase 2: remove phi instructions that are now unreferenced.
        # Recount uses after the expansions to find newly-dead phis.
        new_phi_uses: dict[int, int] = {vid: 0 for vid in phi_defs}
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr):
                    for incoming_val, _ in instr.incoming:
                        if incoming_val.id in new_phi_uses:
                            new_phi_uses[incoming_val.id] += 1

        dead_phis: set[int] = {
            vid for vid in candidates if new_phi_uses[vid] == 0
        }

        if dead_phis:
            new_blocks = [
                replace_block_instrs(
                    block,
                    tuple(
                        instr for instr in block.instrs
                        if not (
                            isinstance(instr, MenaiCFGPhiInstr)
                            and instr.result.id in dead_phis
                        )
                    ),
                )
                for block in func.blocks
            ]

        return replace_blocks(func, tuple(new_blocks)), True


def _substitute_value_in_instr(
    instr: MenaiCFGInstr,
    replacements: dict[int, MenaiCFGValue],
) -> MenaiCFGInstr:
    """
    Return a copy of instr with every referenced value id replaced.

    A value id present in `replacements` is replaced by the mapped value;
    ids not present are left alone.  The instruction's own result is never
    a substitution target — a phi defining a replaced id has already been
    dropped by the caller.
    """
    def sub(v: MenaiCFGValue) -> MenaiCFGValue:
        return replacements.get(v.id, v)

    if isinstance(instr, MenaiCFGBuiltinInstr):
        return replace(instr, args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGCallInstr):
        return replace(instr, func=sub(instr.func), args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGApplyInstr):
        return replace(instr, func=sub(instr.func), arg_list=sub(instr.arg_list))

    if isinstance(instr, MenaiCFGMakeClosureInstr):
        return replace(instr, captures=tuple(sub(c) for c in instr.captures))

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return replace(instr, args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGMakeListInstr):
        return replace(instr, args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGMakeVectorInstr):
        return replace(instr, args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return replace(instr, args=tuple(sub(a) for a in instr.args))

    if isinstance(instr, MenaiCFGMakeDictInstr):
        return replace(instr, pairs=tuple((sub(k), sub(v)) for k, v in instr.pairs))

    if isinstance(instr, MenaiCFGStructGetIndexedInstr):
        return replace(instr, struct=sub(instr.struct))

    if isinstance(instr, MenaiCFGStructWithIndexedInstr):
        return replace(instr, struct=sub(instr.struct), value=sub(instr.value))

    if isinstance(instr, MenaiCFGPatchClosureInstr):
        return replace(instr, closure=sub(instr.closure), value=sub(instr.value))

    if isinstance(instr, MenaiCFGGuardInstr):
        return replace(instr, value=sub(instr.value))

    if isinstance(instr, MenaiCFGPhiInstr):
        return MenaiCFGPhiInstr(
            result=instr.result,
            incoming=tuple((sub(val), pred) for val, pred in instr.incoming),
        )

    # MenaiCFGConstInstr, MenaiCFGParamInstr, MenaiCFGFreeVarInstr: no input
    # value references.
    return instr


def _substitute_value_in_term(
    term: MenaiCFGTerminator,
    replacements: dict[int, MenaiCFGValue],
) -> MenaiCFGTerminator:
    """Return a copy of term with every referenced value id replaced."""
    def sub(v: MenaiCFGValue) -> MenaiCFGValue:
        return replacements.get(v.id, v)

    if isinstance(term, MenaiCFGBranchTerm):
        return replace(term, cond=sub(term.cond))

    if isinstance(term, MenaiCFGSwitchTerm):
        return replace(term, value=sub(term.value))

    if isinstance(term, MenaiCFGReturnTerm):
        return replace(term, value=sub(term.value))

    if isinstance(term, MenaiCFGTailCallTerm):
        return replace(term, func=sub(term.func), args=tuple(sub(a) for a in term.args))

    if isinstance(term, MenaiCFGTailApplyTerm):
        return replace(term, func=sub(term.func), arg_list=sub(term.arg_list))

    if isinstance(term, MenaiCFGSelfLoopTerm):
        return replace(term, args=tuple(sub(a) for a in term.args))

    if isinstance(term, MenaiCFGRaiseTerm):
        return replace(term, message=sub(term.message))

    # MenaiCFGJumpTerm: no value references.
    return term
