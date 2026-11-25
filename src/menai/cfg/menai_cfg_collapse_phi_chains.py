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
  3. The intermediate block is actually bypassed, which needs every phi in
     it to be removed by the same round.  A block that keeps a live phi is
     still on every path, so the expanded entries would name predecessor
     blocks that do not jump to the consuming block.
  4. The intermediate phi is not a loop-carried variable (named by a
     MenaiCFGSelfLoopTerm.param_vals).  A back edge carries its updates in
     that list rather than in the target block's phis, so the phi is named
     by identity: removing it loses the back edge's definition of the
     variable, and bypassing its block skips the move that supplies it.

Condition 2 can arise when a consuming phi has multiple entries that would
expand to the same predecessor block.  The pass skips any collapse that
would produce such a conflict.

Conditions 3 and 4 are independent: a block may keep a survivor that is not
loop-carried, and a loop-carried phi may be the only phi in its block.  Both
are ordinary in real code — an enclosing loop's accumulator is a live sibling,
and its other parameter is used only as the inner loop's initial value.

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
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGJumpTerm,
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    replace_block_instrs,
    replace_blocks,
    substitute_value_in_block,
)


class MenaiCFGCollapsePhiChains(MenaiCFGPerFunctionPass):
    """
    Replace phi-of-phi chains with a single flat phi, and remove phi nodes
    that are redundant.

    For each phi P1 whose result is used *only* as an incoming value in other
    phi nodes (or not at all), and which is not a loop-carried variable,
    expand each consuming phi by substituting P1's incoming entries for the
    P1 reference, then remove P1.  A consuming phi is expanded only when P1's
    block loses every phi it holds, so that the block is bypassed and P1's
    predecessors really do become predecessors of the consuming block.

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

    def _can_bypass(self, block: MenaiCFGBlock, candidates: set[int]) -> bool:
        """
        Return True if *block* is removed from the graph once this round's
        collapses are applied.

        The expanded entries name the intermediate phi's predecessor blocks as
        predecessors of the consuming block, which they only become when the
        intermediate block is bypassed.  A pass-through block is bypassed only
        when every phi in it is removed, so a block that keeps a live phi — an
        enclosing loop's accumulator, say — must not be collapsed through.
        """
        return self._is_pass_through(block) and all(
            isinstance(instr, MenaiCFGPhiInstr) and instr.result.id in candidates
            for instr in block.instrs
        )

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
        loop_carried_ids = _loop_carried_value_ids(func)

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
            # Drop the phis whose result is being replaced, then substitute
            # through the rest of the block.
            kept = tuple(
                instr for instr in block.instrs
                if not (
                    isinstance(instr, MenaiCFGPhiInstr)
                    and instr.result.id in replacements
                )
            )
            new_blocks.append(
                substitute_value_in_block(replace(block, instrs=kept), replacements)
            )

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
        # (including zero uses — those are simply dead).  A loop-carried phi is
        # never a candidate: a back edge names it in param_vals rather than
        # deriving the update from the target block's phis, so it is referenced
        # by identity and cannot be removed or bypassed.
        loop_carried_ids = _loop_carried_value_ids(func)
        candidates: set[int] = {
            vid
            for vid in phi_defs
            if total_uses[vid] == phi_uses[vid] and vid not in loop_carried_ids
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

                    # The collapse is only valid when the intermediate block is
                    # bypassed on every path, so the consuming phi's incoming
                    # entries can be replaced by the intermediate phi's entries.
                    # That needs a block that does nothing but forward its phis
                    # *and* that loses all of them this round: a block that keeps
                    # a live phi (e.g. a loop pre-header that also carries the
                    # enclosing loop's accumulator) still sits on the path, so
                    # the expanded predecessor blocks would not be predecessors
                    # of the consuming block.
                    if not self._can_bypass(phi_blocks[incoming_val.id], candidates):
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


def _loop_carried_value_ids(func: MenaiCFGFunction) -> set[int]:
    """
    Return the value ids that a back edge names as loop-carried variables.

    A MenaiCFGSelfLoopTerm carries its updates directly in `param_vals` rather
    than deriving them from the target block's phis, so the phi at the loop
    header is named by identity: the VCode builder emits one move per
    (param_val, arg) pair, and the slot allocator gives each param_val a fixed
    slot.  Removing such a phi, or bypassing its block, leaves the back edge
    writing a value that nothing defines for the next iteration.
    """
    loop_carried: set[int] = set()
    for block in func.blocks:
        term = block.terminator
        if isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is not None:
            for param_val in term.param_vals:
                loop_carried.add(param_val.id)

    return loop_carried
