"""
CFG pass: simplify blocks.

Three sub-passes run to a joint fixed point:

1. Empty-block bypass
   Eliminates blocks that are pure indirections — no instructions, no
   patch_instrs, and an unconditional jump terminator — by re-pointing
   their predecessors directly at their target.

2. Trivial return inlining
   Inlines trivial terminal blocks directly into their jump predecessors,
   eliminating the jump entirely.  A trivial terminal block has no
   patch_instrs, a MenaiCFGReturnTerm, and either no instructions or a
   single MenaiCFGConstInstr.  For each predecessor that reaches such a
   block via an unconditional jump, the jump is replaced by a copy of the
   block's content (with a fresh SSA value when a const is involved).  If
   all jump predecessors are inlined the terminal block itself is removed.

3. Equivalent raise merging
   Folds duplicate raise blocks into one.  A trivial raise block has no
   patch_instrs, a MenaiCFGRaiseTerm, and either no instructions or a single
   MenaiCFGConstInstr.  When several such blocks raise the same message —
   the same constant value, or the same SSA value — every edge that targets
   one of them is redirected to a single canonical block and the rest are
   removed.  This is the raise analogue of sub-pass 2: a raise block reached
   by a branch or switch edge cannot be inlined into its predecessor, so the
   duplication is removed by merging the blocks instead.
"""


from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGJumpTerm,
    MenaiCFGPhiInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    predecessors_by_block,
    remap_term,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
)
from menai.menai_value import MenaiValue


class MenaiCFGSimplifyBlocks(MenaiCFGPerFunctionPass):
    """
    Simplify the CFG by eliminating unnecessary block boundaries.

    Sub-pass 1 — empty-block bypass:
      For each non-entry block E with no instructions, no patch_instrs, and
      an unconditional jump terminator, re-point E's predecessors directly
      at E's target and remove E.

      Bypass is conditional on phi-store safety: if E's ultimate target has
      phi instructions, E may only be bypassed when no block in the bypass
      chain has a BranchTerm predecessor (otherwise the phi store emitted by
      the JumpTerm would be lost).

    Sub-pass 2 — trivial return inlining:
      For each block T with no patch_instrs, a MenaiCFGReturnTerm, and at
      most one MenaiCFGConstInstr or exactly one MenaiCFGPhiInstr (and no
      other instructions), inline T into every predecessor that reaches it
      via an unconditional jump.

      For the const case a fresh SSA value is allocated so that SSA
      single-definition is preserved.  For the phi case each predecessor
      already holds its contributing value, so that value becomes the return
      directly — no fresh allocation needed.  If all jump predecessors are
      inlined, T is removed from the function.

    Sub-pass 3 — equivalent raise merging:
      For each group of blocks with no patch_instrs, a MenaiCFGRaiseTerm, at
      most one MenaiCFGConstInstr, and the same raise message, keep the first
      block in block-list order and redirect every edge that targets the
      others at it.  The others are removed.

      Two blocks raise the same message when both raise the same SSA value,
      or both materialise equal constant values.  A raise block reached by a
      branch or switch edge cannot be inlined into its predecessor the way a
      trivial return is, so folding the duplicates means merging the blocks.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        changed = False

        func, c = self._bypass_empty_blocks(func)
        changed = changed or c

        func, c = self._inline_trivial_returns(func)
        changed = changed or c

        func, c = self._merge_equivalent_raises(func)
        changed = changed or c

        return func, changed

    def _bypass_empty_blocks(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Bypass blocks that are pure indirections: no instructions, no
        patch_instrs, and an unconditional jump terminator.

        For each such empty block E with ``MenaiCFGJumpTerm(T)``, E can be
        bypassed only if the bypass is safe with respect to the VM codegen's
        phi store mechanism.

        Phi stores are emitted only when a block emits its own ``JumpTerm``.
        If a BranchTerm block is re-pointed to jump directly to a phi-bearing
        block, no phi store is emitted for the branch's contribution.
        Therefore:

        A bypass chain E1 -> E2 -> ... -> T is safe if and only if:
          - T has no phi instructions, OR
          - No block in the chain {E1, E2, ...} has a BranchTerm predecessor.

        The entry block (blocks[0]) is never bypassed even if empty.
        """
        entry_id = func.blocks[0].id
        block_map: dict[int, MenaiCFGBlock] = {b.id: b for b in func.blocks}

        pred_map: dict[int, list[MenaiCFGBlock]] = {b.id: [] for b in func.blocks}
        for block in func.blocks:
            term = block.terminator
            if isinstance(term, MenaiCFGJumpTerm):
                if term.target in pred_map:
                    pred_map[term.target].append(block)

            elif isinstance(term, MenaiCFGBranchTerm):
                if term.true_block in pred_map:
                    pred_map[term.true_block].append(block)

                if term.false_block in pred_map:
                    pred_map[term.false_block].append(block)

            elif isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
                if term.target in pred_map:
                    pred_map[term.target].append(block)

        def is_empty(block: MenaiCFGBlock) -> bool:
            return (
                block.id != entry_id
                and not block.instrs
                and not block.patch_instrs
                and isinstance(block.terminator, MenaiCFGJumpTerm)
            )

        def has_phi(block: MenaiCFGBlock) -> bool:
            return any(isinstance(i, MenaiCFGPhiInstr) for i in block.instrs)

        def chain_has_branch_predecessor(start: MenaiCFGBlock) -> bool:
            seen: set[int] = set()
            block = start
            while is_empty(block) and block.id not in seen:
                if any(
                    isinstance(pred.terminator, MenaiCFGBranchTerm)
                    for pred in pred_map.get(block.id, [])
                ):
                    return True

                seen.add(block.id)
                assert isinstance(block.terminator, MenaiCFGJumpTerm)
                next_b = block_map.get(block.terminator.target)
                if next_b is None:
                    break

                block = next_b

            return False

        def ultimate_target(block: MenaiCFGBlock) -> MenaiCFGBlock:
            seen: set[int] = set()
            while is_empty(block) and block.id not in seen:
                seen.add(block.id)
                assert isinstance(block.terminator, MenaiCFGJumpTerm)
                next_b = block_map.get(block.terminator.target)
                if next_b is None:
                    break

                block = next_b

            return block

        bypass: dict[int, int] = {}
        for block in func.blocks:
            if is_empty(block):
                target = ultimate_target(block)
                if target.id != block.id and not (
                    has_phi(target) and chain_has_branch_predecessor(block)
                ):
                    bypass[block.id] = target.id

        if not bypass:
            return func, False

        def remap_block(block_id: int) -> int:
            return bypass.get(block_id, block_id)

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            if block.id in bypass:
                continue

            new_instrs: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGPhiInstr):
                    new_instrs.append(instr)
                    continue

                new_incoming: list[tuple[MenaiCFGValue, int]] = []
                for val, pred in instr.incoming:
                    if pred in bypass:
                        actual_preds = pred_map.get(pred, [])
                        for actual_pred in actual_preds:
                            for remapped in _find_non_empty_preds(actual_pred, bypass, pred_map):
                                new_incoming.append((val, remapped.id))

                    else:
                        new_incoming.append((val, pred))

                new_instrs.append(MenaiCFGPhiInstr(
                    result=instr.result,
                    incoming=tuple(new_incoming),
                ))

            terminator = block.terminator
            if block.terminator is not None:
                terminator = remap_term(block.terminator, remap_block)

            new_blocks.append(replace(
                block,
                instrs=tuple(new_instrs),
                terminator=terminator,
            ))

        return replace(func, blocks=tuple(new_blocks)), True

    def _inline_trivial_returns(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Inline trivial terminal blocks into their unconditional-jump predecessors.

        A trivial terminal block T qualifies when:
          - T has no patch_instrs
          - T has a MenaiCFGReturnTerm
          - T's instrs are one of:
              (a) empty
              (b) exactly one MenaiCFGConstInstr (no phi)
              (c) exactly one MenaiCFGPhiInstr whose result is the return value

        For each predecessor of T that reaches it via a MenaiCFGJumpTerm, the
        jump is replaced by an inlined copy of T's content.  When T contains a
        MenaiCFGConstInstr, a fresh SSA value is created for each copy to
        preserve the single-definition invariant of SSA form.  When T contains
        a MenaiCFGPhiInstr, each predecessor already holds its contributing
        value, which becomes the return value directly.

        If every predecessor that reaches T via a jump is inlined, T is removed
        from the function.  Predecessors that reach T via a branch are not
        inlined (a branch cannot be replaced by a return in place).
        """
        next_id = _max_value_id(func) + 1

        def fresh_value(hint: str) -> MenaiCFGValue:
            nonlocal next_id
            v = MenaiCFGValue(id=next_id, hint=hint)
            next_id += 1
            return v

        def trivial_return_content(
            block: MenaiCFGBlock,
        ) -> tuple[object, MenaiCFGReturnTerm] | None:
            """
            Return (content, return_term) if block is a trivial terminal block,
            or None if it does not qualify.

            content is one of:
              None                 — empty block (no instructions)
              MenaiCFGConstInstr   — single const instruction
              MenaiCFGPhiInstr     — single phi whose result is the return value
            """
            if block.patch_instrs:
                return None

            if not isinstance(block.terminator, MenaiCFGReturnTerm):
                return None

            if len(block.instrs) == 0:
                return (None, block.terminator)

            if len(block.instrs) == 1 and isinstance(block.instrs[0], MenaiCFGConstInstr):
                return (block.instrs[0], block.terminator)

            if (
                len(block.instrs) == 1
                and isinstance(block.instrs[0], MenaiCFGPhiInstr)
                and block.instrs[0].result.id == block.terminator.value.id
            ):
                return (block.instrs[0], block.terminator)

            return None

        changed = False
        inlined_block_ids: set[int] = set()
        # The predecessor relation is queried for every target, so build it
        # once rather than rescanning the block list per target.
        preds_by_block = predecessors_by_block(func)
        # Rebuilt predecessors, keyed by block id.  A predecessor may be
        # modified for more than one target, so accumulate changes here and
        # rebuild each block once at the end.
        rebuilt: dict[int, MenaiCFGBlock] = {}

        for target in list(func.blocks):
            content = trivial_return_content(target)
            if content is None:
                continue

            instr, return_term = content

            # Find predecessors that reach target via an unconditional jump.
            jump_preds = [
                b for b in preds_by_block[target.id]
                if isinstance(b.terminator, MenaiCFGJumpTerm)
                and b.terminator.target == target.id
            ]

            if not jump_preds:
                continue

            actually_inlined: set[int] = set()
            for pred in jump_preds:
                pred = rebuilt.get(pred.id, pred)
                if isinstance(instr, MenaiCFGConstInstr):
                    # Duplicate the const with a fresh SSA value.
                    new_val = fresh_value(instr.result.hint)
                    pred = replace(
                        pred,
                        instrs=pred.instrs + (
                            MenaiCFGConstInstr(result=new_val, value=instr.value),
                        ),
                        terminator=MenaiCFGReturnTerm(value=new_val),
                    )

                elif isinstance(instr, MenaiCFGPhiInstr):
                    # Each predecessor already holds its contributing value.
                    # Look up the incoming entry for this predecessor.
                    contributing = next(
                        (val for val, blk in instr.incoming if blk == pred.id),
                        None,
                    )
                    if contributing is None:
                        # Predecessor not listed in phi incomings — skip.
                        continue

                    pred = replace(pred, terminator=MenaiCFGReturnTerm(value=contributing))

                else:
                    pred = replace(pred, terminator=MenaiCFGReturnTerm(value=return_term.value))

                rebuilt[pred.id] = pred
                actually_inlined.add(pred.id)
                changed = True

            # Remove target if every predecessor was a jump predecessor that
            # we just inlined, and no predecessor reaches target via a branch
            # or other non-jump edge.
            if (len(actually_inlined) == len(jump_preds)
                    and len(jump_preds) == len(preds_by_block[target.id])):
                inlined_block_ids.add(target.id)

        if not changed:
            return func, False

        new_blocks = [
            rebuilt.get(b.id, b)
            for b in func.blocks
            if b.id not in inlined_block_ids
        ]
        return replace(func, blocks=tuple(new_blocks)), True

    def _merge_equivalent_raises(
        self,
        func: MenaiCFGFunction,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Fold duplicate raise blocks into a single canonical block.

        Every non-entry block that is a trivial raise block (see
        _trivial_raise_key) is grouped by the message it raises.  Within a
        group the first block in block-list order is the canonical block;
        every edge that targets another block in the group is redirected at
        the canonical block, and the other blocks are removed.

        The entry block is never merged: it is pinned first in the block list
        and must remain the entry.

        A trivial raise block's only instruction is its message constant, and
        its only reference is its own terminator, so removing a non-canonical
        block leaves no dangling value reference.  The block has no
        successors, so no edge leaves the group and redirection never needs to
        chase a chain.
        """
        entry_id = func.blocks[0].id

        groups: dict[tuple[str, MenaiValue] | tuple[str, int], _RaiseGroup] = {}
        for block in func.blocks:
            if block.id == entry_id:
                continue

            key = _trivial_raise_key(block)
            if key is None:
                continue

            group = groups.get(key)
            if group is None:
                groups[key] = _RaiseGroup(block)

            else:
                group.removed_ids.append(block.id)

        redirect: dict[int, int] = {}
        for group in groups.values():
            for removed_id in group.removed_ids:
                redirect[removed_id] = group.canonical_id

        if not redirect:
            return func, False

        def remap_block(block_id: int) -> int:
            return redirect.get(block_id, block_id)

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            if block.id in redirect:
                continue

            terminator = block.terminator
            if terminator is not None:
                terminator = remap_term(terminator, remap_block)

            new_blocks.append(replace(block, terminator=terminator))

        return replace(func, blocks=tuple(new_blocks)), True


def _trivial_raise_key(
    block: MenaiCFGBlock,
) -> tuple[str, MenaiValue] | tuple[str, int] | None:
    """
    Return a key identifying the message a trivial raise block raises.

    A trivial raise block has no patch_instrs, a MenaiCFGRaiseTerm, and either
    no instructions or a single MenaiCFGConstInstr whose result is the raise
    message.  Any other block returns None.

    The key is ("const", value) when the block materialises the message as a
    constant, so that two blocks raising equal constants compare equal even
    though their SSA values differ, and ("value", id) when the message is an
    SSA value defined elsewhere, so that two blocks raising the same value
    compare equal.  The two forms are tagged so a constant can never compare
    equal to an SSA value id.
    """
    if block.patch_instrs:
        return None

    term = block.terminator
    if not isinstance(term, MenaiCFGRaiseTerm):
        return None

    if len(block.instrs) == 0:
        return ("value", term.message.id)

    if len(block.instrs) == 1 and isinstance(block.instrs[0], MenaiCFGConstInstr):
        const = block.instrs[0]
        if const.result.id == term.message.id:
            return ("const", const.value)

    return None


class _RaiseGroup:
    """The canonical block for a group of equivalent raise blocks."""

    def __init__(self, canonical: MenaiCFGBlock) -> None:
        self.canonical_id = canonical.id
        self.removed_ids: list[int] = []


def _max_value_id(func: MenaiCFGFunction) -> int:
    """Return the highest SSA value id present anywhere in func."""
    max_id = -1

    def _check(vid: int) -> None:
        nonlocal max_id
        max_id = max(max_id, vid)

    for block in func.blocks:
        for instr in block.instrs:
            result = getattr(instr, 'result', None)
            if result is not None:
                _check(result.id)

            if isinstance(instr, MenaiCFGPhiInstr):
                for incoming_val, _ in instr.incoming:
                    _check(incoming_val.id)

            for vid in value_ids_in_instr(instr):
                _check(vid)

        for patch in block.patch_instrs:
            _check(patch.closure.id)
            _check(patch.value.id)

        if block.terminator is not None:
            for vid in value_ids_in_term(block.terminator):
                _check(vid)

    return max_id


def _find_non_empty_preds(
    block: MenaiCFGBlock,
    bypass: dict[int, int],
    pred_map: dict[int, list[MenaiCFGBlock]],
) -> list[MenaiCFGBlock]:
    """
    Walk up the predecessor chain until non-bypassed blocks are found.

    Returns every non-bypassed predecessor reachable by threading through
    bypassed blocks.  A bypassed block with more than one predecessor
    contributes all of them: dropping any would leave a phi missing an
    incoming entry for a real predecessor.
    """
    result: list[MenaiCFGBlock] = []
    seen: set[int] = set()
    stack = [block]
    while stack:
        current = stack.pop()
        if current.id not in bypass:
            result.append(current)
            continue

        if current.id in seen:
            continue

        seen.add(current.id)
        stack.extend(pred_map.get(current.id, []))

    return result
