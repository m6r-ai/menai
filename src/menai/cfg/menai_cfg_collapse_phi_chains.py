"""
CFG pass: collapse phi chains.

Eliminates phi-of-phi redundancy that arises from nested `if` expressions.
When the result of a phi node is used *only* as an incoming value in one or
more other phi nodes, the intermediate phi can be bypassed: each consuming
phi absorbs the intermediate's incoming entries in its place.

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

Safety
------
The transformation is valid when:
  1. The intermediate phi result (%v1) is used *only* as a phi incoming
     value — never in a builtin, call, return, branch condition, etc.
  2. No consuming phi already has an entry from one of the intermediate
     phi's predecessor blocks (which would create a duplicate predecessor,
     violating the one-entry-per-predecessor invariant for phi nodes).

Condition 2 can arise when a consuming phi has multiple entries that would
expand to the same predecessor block.  The pass skips any collapse that
would produce such a conflict.

Menai is pure, so dead-code elimination is always safe (AGENTS.md).
"""


from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGJumpTerm,
    MenaiCFGPhiInstr,
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
    whose results are never used.

    For each phi P1 whose result is used *only* as an incoming value in
    other phi nodes (or not at all), expand each consuming phi by
    substituting P1's incoming entries for the P1 reference, then remove
    P1.

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
        while True:
            func, round_changed = self._run_one_round(func)
            if not round_changed:
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
