"""
CFG pass: late sinking of single-use values within a loop.

A value's live range is the span between its definition and its last use.  When
a value is defined early in a loop body but consumed late, every instruction in
between must keep it live, which costs a register slot for the whole span.  This
pass moves such a definition as late as it legally can, shortening the range.

The pass is scoped to a single loop region.  Both the definition and the
destination must lie in the same region, which makes the correctness conditions
local and cheap:

  - The definition's block dominates the destination, so the definition's
    operands are still live there.
  - The destination post-dominates the definition within the region, so the
    value is still computed on every path that reaches it.  Within a region with
    a single back-edge this holds for every destination that the back-edge block
    dominates; the pass checks it explicitly rather than assuming it.
  - The destination dominates every use of the value, so each use still sees the
    definition.

Because the value moves to a point that is reached only by paths that already
executed it, sinking never introduces a raise on a path that did not have one.
This is the mirror of the hoisting condition in ADR-0049 and needs no
`may_raise` lookup.

Why this matters to the backend
-------------------------------
The slot allocator coalesces a value into a loop parameter's slot when the value
is defined after the last read of that parameter and is used only by the
back-edge move (Phase 3c of `allocate_slots`).  A loop-carried update that the
CFG builder emitted early in the body -- because `recur` arguments are evaluated
left to right -- is separated from the back-edge by the rest of the body, so the
coalescing declines and a `MOVE` survives.  Sinking the update to the back-edge
block puts its definition immediately before the move group, which is exactly
the shape Phase 3c accepts.

The pass runs after loop rotation, which establishes the final loop shape and
retargets the back-edges, and before exception-block ordering and VCode
lowering.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
    blocks_by_id,
    result_id_in_instr,
    successor_ids,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    compute_dominators_in_subgraph,
    replace_blocks,
)


class MenaiCFGSinkLate(MenaiCFGPerFunctionPass):
    """
    Sink single-use values as late as legally possible within their loop.

    For each loop in the function, every instruction whose result has exactly
    one use is considered.  It is moved to the latest block in the loop region
    that still dominates that use, provided the move satisfies the dominance
    conditions above.  A value is never moved out of its loop region, so the
    pass cannot sink a value into a nested loop or past the loop's exit.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        changed = False
        for header_id, self_loops in self._loops(func):
            func, loop_changed = self._sink_in_loop(func, header_id, self_loops)
            changed = changed or loop_changed

        return func, changed

    def _loops(
        self,
        func: MenaiCFGFunction,
    ) -> list[tuple[int, list[MenaiCFGSelfLoopTerm]]]:
        """
        Return each loop as its header id plus the self-loops that close it.

        Self-loops are grouped by the block they target, which is the loop
        header.  A self-loop with no explicit target targets the entry block.
        """
        groups: dict[int, list[MenaiCFGSelfLoopTerm]] = {}
        for block in func.blocks:
            term = block.terminator
            if not isinstance(term, MenaiCFGSelfLoopTerm):
                continue

            header_id = term.target if term.target is not None else func.entry().id
            groups.setdefault(header_id, []).append(term)

        return list(groups.items())

    def _sink_in_loop(
        self,
        func: MenaiCFGFunction,
        header_id: int,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Sink single-use values within one loop region, to a fixed point.

        Sinking a value changes where its operands are used, which can make an
        operand sinkable in turn: when `%22` moves into the back-edge block,
        `%21`'s only use moves with it, so `%21` becomes sinkable too.  The
        region is therefore re-analysed until nothing more moves.
        """
        changed = False
        while True:
            func, step_changed = self._sink_in_loop_once(func, header_id, self_loops)
            if not step_changed:
                return func, changed

            changed = True

    def _sink_in_loop_once(
        self,
        func: MenaiCFGFunction,
        header_id: int,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> tuple[MenaiCFGFunction, bool]:
        """Run one sinking step over one loop region."""
        region = self._loop_region(func, header_id, self_loops)
        if len(region) < 2:
            return func, False

        by_id = blocks_by_id(func)
        if header_id not in by_id or not region <= set(by_id):
            return func, False

        dom = self._region_dominators(func, header_id, region)
        uses = self._use_sites(func, region)

        # Each entry is (definition block id, instruction index, destination
        # block id).  Collected first so that the block rewrites below are not
        # affected by earlier moves in the same pass.
        moves: list[tuple[int, int, int]] = []
        for block_id in sorted(region):
            block = by_id[block_id]
            for index, instr in enumerate(block.instrs):
                destination = self._sink_destination(
                    instr, block_id, region, dom, uses,
                )
                if destination is not None:
                    moves.append((block_id, index, destination))

        if not moves:
            return func, False

        return self._apply_moves(func, moves), True

    def _sink_destination(
        self,
        instr: MenaiCFGInstr,
        block_id: int,
        region: set[int],
        dom: dict[int, set[int]],
        uses: dict[int, set[int]],
    ) -> int | None:
        """
        Return the block `instr`'s result should sink to, or None.

        The destination is the latest block in the region that dominates the
        value's single use.  It is None when the value does not qualify: no
        result, not exactly one use, a use outside the region, a definition that
        does not dominate the use, or a destination equal to the current block.
        """
        # A phi must stay at the top of its block: its arms are materialised as
        # moves in the predecessors, so moving the phi would invalidate the
        # edges those moves are placed on.
        if isinstance(instr, MenaiCFGPhiInstr):
            return None

        result_id = result_id_in_instr(instr)
        if result_id is None:
            return None

        use_blocks = uses.get(result_id)
        if use_blocks is None or len(use_blocks) != 1:
            return None

        use_block = next(iter(use_blocks))
        if use_block not in region:
            return None

        if block_id not in dom.get(use_block, set()):
            return None

        destination = self._latest_dominating_block(use_block, block_id, region, dom)
        if destination is None or destination == block_id:
            return None

        return destination

    def _latest_dominating_block(
        self,
        use_block: int,
        definition_block: int,
        region: set[int],
        dom: dict[int, set[int]],
    ) -> int | None:
        """
        Return the deepest region block that dominates `use_block` and is
        dominated by `definition_block`.

        The destination must dominate the use, so the use still sees the value,
        and must be dominated by the definition, so the definition's operands
        are still live there.  Among the candidates the deepest is the latest
        legal placement.  Depth is the size of the block's dominator set: a
        dominator of a block is a strict prefix of that block's dominator set,
        so a larger set is deeper.
        """
        use_dominators = dom.get(use_block, set())

        candidates = [
            candidate for candidate in use_dominators
            if candidate in region
            and definition_block in dom.get(candidate, set())
        ]
        if not candidates:
            return None

        return max(candidates, key=lambda candidate: len(dom.get(candidate, set())))

    def _region_dominators(
        self,
        func: MenaiCFGFunction,
        header_id: int,
        region: set[int],
    ) -> dict[int, set[int]]:
        """
        Return dominators within the loop region, rooted at the header.

        Edges entering the region from outside are ignored: the question is
        which blocks every iteration passes through, not which blocks every
        execution of the enclosing function passes through.
        """
        region_preds: dict[int, list[int]] = {}
        for block in func.blocks:
            if block.id not in region:
                continue

            region_preds[block.id] = [
                successor for successor in self._predecessor_ids(func, block.id)
                if successor in region
            ]

        return compute_dominators_in_subgraph(region, header_id, region_preds)

    def _predecessor_ids(self, func: MenaiCFGFunction, block_id: int) -> list[int]:
        """Return the ids of the blocks that can transfer control to `block_id`."""
        return [
            block.id for block in func.blocks
            if block_id in successor_ids(block.terminator)
        ]

    def _loop_region(
        self,
        func: MenaiCFGFunction,
        header_id: int,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> set[int]:
        """
        Return the blocks that make up the loop.

        The region is every block reachable from the header by following
        successors, stopping at each back-edge.  A back-edge block is included
        but its successors are not followed: control leaves the loop there.

        This is a forward walk rather than a backward one from the back-edges.
        A backward walk would also reach the peeled entry test that loop
        rotation leaves behind, because that block branches into the loop body
        as well as to the exit; it runs once, not per iteration, so it is not
        part of the loop.

        A block reachable from the header by a path that leaves the loop and
        re-enters it cannot exist: the only edge out of the loop is a back-edge
        or an exit, and an exit does not re-enter.  The walk is therefore
        exactly the loop body.
        """
        by_id = blocks_by_id(func)
        if header_id not in by_id:
            return set()

        back_edge_ids = {
            block.id for block in func.blocks
            if any(block.terminator is self_loop for self_loop in self_loops)
        }

        region: set[int] = set()
        stack = [header_id]
        while stack:
            block_id = stack.pop()
            if block_id in region:
                continue

            region.add(block_id)
            if block_id in back_edge_ids:
                continue

            for successor in successor_ids(by_id[block_id].terminator):
                if successor in by_id and successor not in region:
                    stack.append(successor)

        return region

    def _use_sites(
        self,
        func: MenaiCFGFunction,
        region: set[int],
    ) -> dict[int, set[int]]:
        """
        Map each value id to the set of blocks in the region that use it.

        Instructions, patch-closure operands, terminators, and phi arms all
        count as uses.  A phi arm is attributed to the predecessor block the arm
        names, not to the block that holds the phi: phi elimination materialises
        the arm as a move in that predecessor, so the value must be defined
        before control leaves the predecessor.  Attributing the arm to the phi's
        own block would let a definition sink into the phi's block, past the
        move that reads it.
        """
        uses: dict[int, set[int]] = {}
        for block in func.blocks:
            if block.id not in region:
                continue

            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr):
                    for value, predecessor_id in instr.incoming:
                        uses.setdefault(value.id, set()).add(predecessor_id)

                    continue

                for value_id in value_ids_in_instr(instr):
                    uses.setdefault(value_id, set()).add(block.id)

            for patch in block.patch_instrs:
                for value_id in value_ids_in_instr(patch):
                    uses.setdefault(value_id, set()).add(block.id)

            if block.terminator is not None:
                for value_id in value_ids_in_term(block.terminator):
                    uses.setdefault(value_id, set()).add(block.id)

        return uses

    def _apply_moves(
        self,
        func: MenaiCFGFunction,
        moves: list[tuple[int, int, int]],
    ) -> MenaiCFGFunction:
        """
        Move each recorded instruction into its destination block.

        Several instructions may sink into the same block, and one may read
        another's result, so they are inserted in dependency order: an
        instruction is placed only once every sunk instruction it reads is
        already in place.  Each is inserted immediately before the first
        instruction of the block that reads it, or appended when the block does
        not read it directly (the use is then in a later block).

        Inserting before the use rather than at the end is required when the
        destination is the block that holds the use: appending would place the
        definition after the read.  Inserting before the use also keeps the sunk
        value as late as possible, which is the point of the pass.
        """
        by_id = blocks_by_id(func)

        removed: dict[int, set[int]] = {}
        pending: dict[int, list[MenaiCFGInstr]] = {}
        for source_id, index, destination_id in moves:
            instr = by_id[source_id].instrs[index]
            if source_id == destination_id:
                # The value is already in the block it sinks to, so it is
                # already positioned after its operands.  Re-inserting it would
                # move it before them, because its first reader may precede
                # those operands.
                continue

            removed.setdefault(source_id, set()).add(index)
            pending.setdefault(destination_id, []).append(instr)

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            dropped = removed.get(block.id, set())
            instrs = [
                instr for index, instr in enumerate(block.instrs)
                if index not in dropped
            ]

            additions = pending.get(block.id)
            if additions is not None:
                instrs = self._insert_in_dependency_order(instrs, additions)

            changed = dropped or additions
            new_blocks.append(replace(block, instrs=tuple(instrs)) if changed else block)

        return replace_blocks(func, tuple(new_blocks))

    def _insert_in_dependency_order(
        self,
        instrs: list[MenaiCFGInstr],
        additions: list[MenaiCFGInstr],
    ) -> list[MenaiCFGInstr]:
        """
        Insert `additions` into `instrs`, respecting dependencies between them.

        An addition that reads another addition's result must be placed after
        it.  The additions are therefore placed in topological order, and each
        is positioned by `_insertion_index` against the list as it stands at
        that moment, so an earlier insertion is accounted for when a later
        position is computed.
        """
        remaining = list(additions)
        placed: set[int] = set()
        while remaining:
            ready = [
                instr for instr in remaining
                if all(
                    operand in placed
                    for operand in value_ids_in_instr(instr)
                    if operand in _result_ids(additions)
                )
            ]
            if not ready:
                # A cycle among the additions cannot occur in SSA, but fall
                # back to source order rather than looping forever.
                ready = remaining

            instr = ready[0]
            remaining.remove(instr)

            result_id = result_id_in_instr(instr)
            assert result_id is not None
            instrs.insert(self._insertion_index(instrs, result_id), instr)
            placed.add(result_id)

        return instrs

    def _insertion_index(self, instrs: list[MenaiCFGInstr], result_id: int) -> int:
        """
        Return the index in `instrs` at which a definition of `result_id` goes.

        The definition goes immediately before the first instruction that reads
        it.  When no instruction in the block reads it, the use is in a later
        block and the definition is appended.
        """
        for index, instr in enumerate(instrs):
            if isinstance(instr, MenaiCFGPhiInstr):
                continue

            if result_id in value_ids_in_instr(instr):
                return index

        return len(instrs)


def _result_ids(instrs: list[MenaiCFGInstr]) -> set[int]:
    """Return the ids of the values defined by `instrs`."""
    return {
        result_id
        for result_id in (result_id_in_instr(instr) for instr in instrs)
        if result_id is not None
    }
