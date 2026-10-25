"""
CFG pass: local list scheduling within each basic block.

A value's live range is the span between its definition and its last use.  When
a value is defined early in a block but consumed late, every instruction in
between must keep it live, which costs a register slot for the whole span.  This
pass reorders each block's instructions so that every definition sits as late as
its dependents allow, which shortens the ranges.

The objective is "latest legal order".  The block is built front to back, and at
each step the instruction chosen is the ready one on the longest remaining
dependency chain.  Emitting the deepest chain first defers everything else, so
each independent definition lands as close to its reader as the dependencies
permit, and a chain ends adjacent to the instruction that consumes it.

Why this matters to the backend
-------------------------------
The slot allocator coalesces a value into a loop parameter's slot when the value
is defined after the last read of that parameter and is used only by the
back-edge move (Phase 3c of `allocate_slots`).  A loop-carried update that the
CFG builder emitted early in the body -- because `recur` arguments are evaluated
left to right -- is separated from the back-edge by the rest of the body, so the
coalescing declines and a `MOVE` survives.  Scheduling the update to the end of
the block puts its definition immediately before the move group, which is
exactly the shape Phase 3c accepts.

The same pressure exists in blocks that are not loop bodies: a chain of
single-use temporaries computed at the top of a straight-line block and consumed
at the bottom occupies slots for the whole block.

Correctness
-----------
Reordering within a block never changes which paths reach the block, so an
instruction executes on exactly the set of paths it executed on before.  The
hoisting hazard of ADR-0049 -- introducing a raise on a path that did not have
one -- cannot arise, and no `may_raise` lookup is needed.

Within a block the dependency edges are the whole correctness condition: if
every operand is defined before its use on the single path through the block,
the reordering is sound.  The edges are

  - RAW: an instruction that reads a value must follow its definition.
  - WAR: an instruction that reads a value must precede a later redefinition of
    that value.
  - WAW: two definitions of the same value keep their relative order.

WAR and WAW matter because a register id can be defined more than once within a
function (phi-elimination moves and match arms produce this), so a block is not
necessarily single-assignment.

Idempotence
-----------
The pass manager runs each pass to its own fixed point, so the pass must be a
projection: scheduling an already-scheduled block must change nothing.  Two
things are needed for that, and both are load-bearing.

First, the order must be a pure function of the instruction set, never of the
order the instructions arrived in.  The choice among equally deep candidates is
therefore broken by a key derived from the instruction itself (its SSA result
id), and that key must be unique within the block -- a guard defines no result,
so it is keyed in a separate space from the instructions that define values.

Second, the emitted order must be a topological sort: an instruction is only
eligible once every operand it reads is available.  An order that violates this
is not merely wrong, it is unstable, because re-running the pass on it produces
a different order again.

Pinned instructions
-------------------
A phi must stay at the top of its block: its arms are materialised as moves in
the predecessors, so moving it would invalidate the edges those moves are placed
on.  Param and free-var definitions establish slots in the entry block and are
not reorderable.  Guards and patch-closure instructions define no result, so
they are scheduled as ordinary nodes whose position is fixed by their operands.

The pass runs after loop rotation, which establishes the final loop shape and
retargets the back-edges, and before exception-block ordering, which partitions
the emitted layout.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGFreeVarInstr,
    MenaiCFGGuardInstr,
    MenaiCFGInstr,
    MenaiCFGParamInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGPhiInstr,
    result_id_in_instr,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    replace_block_instrs,
)


class MenaiCFGSchedule(MenaiCFGPerFunctionPass):
    """
    Schedule each block's instructions into their latest legal order.

    A block whose instructions are already in that order is returned
    unchanged, so the pass is idempotent and the pass manager's fixed-point
    loop terminates after one no-change call.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        changed = False
        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            scheduled = self._schedule_block(block)
            if scheduled is not block:
                changed = True

            new_blocks.append(scheduled)

        if not changed:
            return func, False

        return MenaiCFGFunction(
            blocks=tuple(new_blocks),
            params=func.params,
            free_vars=func.free_vars,
            is_variadic=func.is_variadic,
            binding_name=func.binding_name,
            source_line=func.source_line,
            source_file=func.source_file,
            fact_key=func.fact_key,
        ), True

    def _schedule_block(self, block: MenaiCFGBlock) -> MenaiCFGBlock:
        """
        Return `block` with its instructions in latest legal order.

        Returns `block` itself when the order is already latest, so a caller
        can detect a no-op by identity.
        """
        instrs = block.instrs
        if len(instrs) < 2:
            return block

        # A block holding patch-closure instructions is left in its original
        # order.  Patches install the sibling captures of a $letrec group, and
        # the whole group must be patched before any closure in it is called:
        # interleaving a patch with a call leaves the callee's capture slot
        # unset.  Pinning the block is the conservative guarantee that the
        # builder's order is preserved; such blocks are rare, holding only the
        # initialisation of a mutually-recursive group.
        if any(isinstance(instr, MenaiCFGPatchClosureInstr) for instr in instrs):
            return block

        pinned = self._pinned_prefix_length(instrs)
        scheduled = (
            list(instrs[:pinned])
            + self._schedule_run(
                list(instrs[pinned:]),
                self._terminator_reads(block),
            )
        )
        if all(new is old for new, old in zip(scheduled, instrs)):
            return block

        return replace_block_instrs(block, tuple(scheduled))

    def _terminator_reads(self, block: MenaiCFGBlock) -> set[int]:
        """
        Return the value ids the block's terminator reads.

        The terminator is the last reader in the block, so a value it consumes
        must be defined before it.  Treating those values as readers is what
        lets a definition sink to the very end of the block: without it, a
        loop-carried update has no reader inside the block at all (its reader
        is the back-edge) and the scheduler has no reason to place it late.
        """
        if block.terminator is None:
            return set()

        return set(value_ids_in_term(block.terminator))

    def _pinned_prefix_length(self, instrs: tuple[MenaiCFGInstr, ...]) -> int:
        """
        Return the number of leading instructions that must keep their position.

        A phi's arms are materialised as moves in the predecessors, so a phi
        must stay at the top of its block.  Param and free-var definitions
        establish the entry block's slots and are likewise fixed.  They form a
        prefix: nothing else is ever emitted before them.
        """
        count = 0
        for instr in instrs:
            if isinstance(instr, (MenaiCFGPhiInstr, MenaiCFGParamInstr, MenaiCFGFreeVarInstr)):
                count += 1
                continue

            break

        return count

    def _schedule_run(
        self,
        instrs: list[MenaiCFGInstr],
        terminator_reads: set[int],
    ) -> list[MenaiCFGInstr]:
        """
        Schedule one run of freely reorderable instructions.

        The run is built front to back.  At each step the ready instructions --
        those whose operands are all already available -- are candidates, and
        the one chosen is the candidate on the longest remaining dependency
        chain.  A definition with a long chain below it must be emitted early or
        its chain cannot complete, so emitting the deepest chain first is what
        defers everything else towards the end of the block.

        A value consumed by the terminator is treated as lying on a chain that
        extends one step past the end of the block, so a definition feeding the
        back-edge is emitted as late as possible -- adjacent to the move group
        the slot allocator coalesces it into.

        `terminator_reads` are the values the block's terminator consumes.
        """
        if len(instrs) < 2:
            return instrs

        defined_in_run = {
            result_id for result_id in (result_id_in_instr(i) for i in instrs)
            if result_id is not None
        }
        depth = self._chain_depths(instrs, terminator_reads)
        guard_targets = self._guard_targets(instrs)

        remaining = list(instrs)
        available: set[int] = set()
        placed: list[MenaiCFGInstr] = []

        while remaining:
            ready = [
                instr for instr in remaining
                if self._operands_available(instr, available, defined_in_run)
                and self._guards_satisfied(instr, remaining, guard_targets)
            ]
            if not ready:
                # Every remaining instruction is waiting on another, so the
                # remaining set contains a dependency cycle.  That cannot happen
                # in a well-formed block, where every edge runs from a
                # definition to a later reader.  Fall back to the canonical
                # order rather than looping forever if one ever does.
                ready = remaining

            chosen = max(
                ready,
                key=lambda instr: (depth[id(instr)], self._tie_break(instr)),
            )
            remaining.remove(chosen)
            placed.append(chosen)

            result_id = result_id_in_instr(chosen)
            if result_id is not None:
                available.add(result_id)

        return placed

    def _guard_targets(self, instrs: list[MenaiCFGInstr]) -> dict[int, int]:
        """
        Map each guarded value id to the number of guards on it in this block.

        A guard defines no result: it validates a value in place, and the
        operations that follow rely on it having done so.  It therefore has to
        execute before every other reader of the value it guards, which is an
        ordering edge the ordinary data-dependency model does not express.
        """
        counts: dict[int, int] = {}
        for instr in instrs:
            if isinstance(instr, MenaiCFGGuardInstr):
                counts[instr.value.id] = counts.get(instr.value.id, 0) + 1

        return counts

    def _guards_satisfied(
        self,
        instr: MenaiCFGInstr,
        remaining: list[MenaiCFGInstr],
        guard_targets: dict[int, int],
    ) -> bool:
        """
        Return True if `instr` may be placed before the guards still unplaced.

        A guard may always be placed.  Any other instruction that reads a value
        with an unplaced guard must wait: the operations that follow a guard
        skip their own type checks, so running one before the guard validates
        its operand reads a value of the wrong type.  That is a miscompile, and
        on the VM it corrupts memory rather than merely giving a wrong answer.
        """
        if not guard_targets:
            return True

        if isinstance(instr, MenaiCFGGuardInstr):
            return True

        for operand_id in value_ids_in_instr(instr):
            if guard_targets.get(operand_id, 0) == 0:
                continue

            for candidate in remaining:
                if not isinstance(candidate, MenaiCFGGuardInstr):
                    continue

                if candidate.value.id == operand_id:
                    return False

        return True


    def _chain_depths(
        self,
        instrs: list[MenaiCFGInstr],
        terminator_reads: set[int],
    ) -> dict[int, int]:
        """
        Return the length of the longest dependency chain from each instruction.

        The depth of an instruction is one more than the greatest depth of the
        instructions that read its result.  A result the terminator reads has a
        depth of one, so a chain ending at the back-edge is the deepest thing in
        the block and is emitted first -- which places it last in the block.

        Keyed by `id(instr)` because MenaiCFGInstr is a dataclass and two
        structurally equal instructions would otherwise share an entry.
        """
        readers: dict[int, list[MenaiCFGInstr]] = {id(instr): [] for instr in instrs}
        by_result: dict[int, MenaiCFGInstr] = {}
        for instr in instrs:
            result_id = result_id_in_instr(instr)
            if result_id is not None:
                by_result[result_id] = instr

        for instr in instrs:
            for operand_id in value_ids_in_instr(instr):
                definer = by_result.get(operand_id)
                if definer is not None and definer is not instr:
                    readers[id(definer)].append(instr)

        depth: dict[int, int] = {}
        for instr in reversed(instrs):
            below = 0
            for reader in readers[id(instr)]:
                below = max(below, depth.get(id(reader), 0))

            # A value the terminator reads has the terminator as one more
            # reader, so it lies on a chain that extends past the end of the
            # block.  That is what makes a definition feeding the back-edge the
            # deepest thing in the block, and therefore emitted last.
            result_id = result_id_in_instr(instr)
            if result_id is not None and result_id in terminator_reads:
                below = max(below, 1)

            depth[id(instr)] = 1 + below

        return depth

    def _operands_available(
        self,
        instr: MenaiCFGInstr,
        available: set[int],
        defined_in_run: set[int],
    ) -> bool:
        """
        Return True if every operand of `instr` is already available.

        An operand is available when the instruction that defines it has already
        been placed, or when nothing in this run defines it (it is a parameter,
        a value from another block, or defined in the pinned prefix).
        """
        for operand_id in value_ids_in_instr(instr):
            if operand_id in defined_in_run and operand_id not in available:
                return False

        return True

    def _tie_break(self, instr: MenaiCFGInstr) -> tuple[int, int]:
        """
        Return a stable ordering key for `instr`, used only to break ties.

        The key must depend only on the instruction, never on its position in
        the list being scheduled, or the pass would not be idempotent.

        It must also be unique within a block.  An instruction that defines a
        result is keyed by that result id.  An instruction that defines nothing
        (a guard or a patch-closure) is keyed by its first operand, in a
        separate key space so that it can never collide with the instruction
        that defines that operand.  A collision would leave `max` choosing
        between equal keys, and its choice would then depend on the order of
        the list it was given, which is exactly the non-idempotence this key
        exists to prevent.
        """
        result_id = result_id_in_instr(instr)
        if result_id is not None:
            return (0, -result_id)

        operands = value_ids_in_instr(instr)
        return (1, -(operands[0] if operands else 0))
