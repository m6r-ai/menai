"""
CFG pass: loop rotation (loop inversion).

A self-loop (a tail-recursive loop lowered to a `MenaiCFGSelfLoopTerm`)
is emitted with its test at the top of the loop:

    header:  <loop test>
             branch %cond -> exit / body
    body:    ...
             self_loop(args)        ; unconditional back-edge

Every iteration therefore pays for two branch instructions: the
unconditional back-edge jump and the conditional test at the top.

This pass rotates the loop so the test is evaluated at the bottom:

    header:  <loop test>            ; peeled entry test
             branch %cond -> exit / body
    body:    ...
             self_loop(args, target=continue)
    continue: <loop test>           ; rotated test (copy of the header test)
             branch %cond2 -> exit / body

The back-edge now lands on the rotated test, which branches directly back
into the body when the loop should continue.  The unconditional back-edge
jump is replaced by a conditional branch, saving one jump per iteration.
The peeled test in the header is retained so the loop is skipped entirely
when the entry condition is false.

The self-loop is deliberately kept as a `MenaiCFGSelfLoopTerm` and merely
re-targeted, rather than replaced by a plain branch.  The backend relies on
the self-loop to update the loop-carried parameter slots and to drive its
self-loop slot optimisations; a plain branch would silently lose both.

A loop may have more than one back-edge: when both arms of a branch in the
body tail-call the enclosing function, each arm gets its own self-loop.  Each
self-loop gets its own copy of the rotated test, placed so that the
back-edge falls through into it.  A single shared test block would only be
the fall-through successor of one back-edge, leaving the others with an
unconditional jump to reach it — exactly the jump rotation removes.  The
duplicated test blocks are the reason the backend identifies self-loop
back-edges by a flag on the jump rather than by a single shared
`__entry__` label.

Each back-edge is rotated only if the header test can actually change value
on it.  When the test's operands are all passed through unchanged by a
back-edge, the rotated test it would receive is a branch on a condition the
header has already evaluated and the body has not touched, so one of its two
arms can never be taken.  Such a back-edge's continue block is therefore a
plain jump to the arm the header took, rather than a copy of the test.

The decision is per back-edge, not per loop, because a loop's operands need
not all vary on every back-edge.  The inner counter of a loop written as one
flat nested loop advances on one back-edge while the outer counter is passed
through unchanged on it: the outer counter's edge gets the rotated test, and
the inner counter's edge jumps to the inner test, so control reaches a test
that can take either arm and each iteration pays one instruction for the
back-edge rather than two.

The continue block is created for a decided back-edge too, even though it
holds only a jump.  A back-edge is recognised as already rotated by targeting
a block the entry path cannot reach, and pointing one at an existing body
block instead would make it look unrotated on every round, rotating it without
end.

A function may contain more than one loop — whether sequential (a loop after
another) or nested (a loop inside another loop's body).  Each loop is rotated
independently, innermost first: rotating an inner loop only adds a block
inside its own body and retargets its own back-edges, so it cannot invalidate
the analysis of an enclosing loop.  Rotating an outer loop first would move
the region boundaries an inner loop's analysis depends on, so inner-first
ordering avoids having to re-derive every loop's structure after each
rotation.

The rotated test is a full copy of the header block's test instructions
(everything except the param and free-var definitions), not just the branch
condition.  The header block also holds the guards and intermediate
computations the test depends on, and those must be re-executed on every
iteration just as the condition must.  Copying the whole test and remapping
its result SSA values to fresh ids keeps the copy self-contained while its
operands continue to reference the parameter and free-var values, which the
back-edge moves update in place.

Applicability
-------------
The pass handles the canonical shape only.  Each loop is considered on its
own; a function with several loops has each rotatable one rotated and the
rest left alone:

  - one or more `MenaiCFGSelfLoopTerm` terminators, all sharing a single
    loop header (a tail-recursive loop with both arms of a branch calling
    the enclosing function has one self-loop per arm),
  - the loop header (the self-loops' target, or the entry block when the
    target is unset) ends in a `MenaiCFGBranchTerm`,
  - the header test's operands are all params, free vars, or values defined
    outside the header block (so they are valid at the back-edge), and
  - every self-loop block is reachable from the header without passing back
    through it (so it is a back-edge of this loop; the region it is found in
    may span several blocks, and may hang off either arm of the header
    branch — a phase test that loops out of both arms is rotatable too), and
  - no header test result is consumed anywhere in the body region.

Anything else is left unrotated.  The pass is idempotent: an already-rotated
back-edge (one whose self-loop target block is the pass's own continue block,
which the entry path cannot reach) is detected and skipped.  The same
detection covers a decided back-edge, whose continue block holds only a jump,
so no round of the fixed-point driver retargets the same edge twice.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGApplyInstr,
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGuardInstr,
    MenaiCFGInstr,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGParamInstr,
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructWithIndexedInstr,
    MenaiCFGSwitchIntegerTerm,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGValue,
    blocks_by_id,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    compute_dominators,
)
from menai.menai_error import MenaiInternalError


class MenaiCFGLoopRotation(MenaiCFGPerFunctionPass):
    """
    CFG optimization pass that rotates a self-loop so its test is at the
    bottom, replacing the unconditional back-edge with a conditional one.

    See the module docstring for the algorithm and applicability conditions.
    """

    def _optimize_function(
        self, func: MenaiCFGFunction, context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Rotate every rotatable loop in the function.

        A function may contain more than one loop — sequential loops, nested
        loops, or both.  Each loop is rotated independently, innermost first,
        so that rotating an inner loop (which only adds a block inside its own
        body and retargets its own back-edges) cannot invalidate the structure
        an enclosing loop's analysis was derived from.
        """
        changed = False
        for loop in self._find_loops(func):
            func, rotated = self._rotate_loop(func, loop)
            if rotated:
                changed = True

        return func, changed

    def _rotate_loop(
        self,
        func: MenaiCFGFunction,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Rotate a single loop, given its self-loop terminators.

        Returns the (possibly new) function and whether the loop was rotated.
        The loop's header, body, and test are re-derived from the current CFG,
        so this is safe to call after other loops in the same function have
        been rotated.
        """
        by_id = blocks_by_id(func)
        header = self._self_loop_header(func, self_loops[0], by_id)

        if not isinstance(header.terminator, MenaiCFGBranchTerm):
            return func, False

        branch = header.terminator
        body = by_id[branch.false_block]

        # Every self-loop must be reachable from the header without passing
        # back through it, so that it is a back-edge of this loop rather than
        # of some enclosing loop.  Both arms of the header branch are
        # searched, not just the false one: a loop whose test is a phase test
        # rather than a termination test can loop out of either arm, and the
        # merge step of the prelude's sort-vector is exactly that shape
        # (`(if (= k hi) <advance the pass, or finish> <merge one element>)`).
        # Rotation is sound for such a loop for the same reason it is sound
        # for the narrower shape: every back-edge re-evaluates the test, so
        # it does not matter which arm of the test the back-edge sits under.
        body_region = self._body_region(by_id, body, header)
        body_region |= self._body_region(by_id, by_id[branch.true_block], header)
        for self_loop in self_loops:
            self_loop_block = self._self_loop_block(func, self_loop)
            if self_loop_block is None or self_loop_block.id not in body_region:
                return func, False

        test_instrs = self._header_test_instrs(header)
        if not test_instrs:
            return func, False

        if not self._operands_safe_at_back_edge(func, test_instrs, header):
            return func, False

        if self._test_results_used_by_body(
            func, test_instrs, body_region, self_loops,
        ):
            return func, False

        # Decide what each back-edge does once its loop-carried moves have run,
        # per back-edge rather than per loop.  A loop written as one flat
        # nested loop advances its inner counter on one back-edge while passing
        # its outer counter through unchanged on the same edge, so the header
        # test can be undecided on one back-edge and decided on another.
        #
        # An undecided test gets a rotated copy, as before.  A decided test is
        # not re-executed and not copied: its operands are unchanged, so its
        # outcome is the one the header already took, and the back-edge's
        # successor jumps straight at that arm.  Rotating would instead emit a
        # branch whose other arm can never be taken.
        #
        # The continue block is created either way.  A back-edge is recognised
        # as already rotated by targeting a block the entry path cannot reach,
        # and pointing a back-edge at an existing body block would make it look
        # unrotated on every round, rotating it without end.
        rotatable: list[tuple[MenaiCFGSelfLoopTerm, MenaiCFGBlock, bool]] = []
        for self_loop in self_loops:
            if self._is_back_edge_rotated(func, self_loop):
                continue

            self_loop_block = self._self_loop_block(func, self_loop)
            assert self_loop_block is not None
            decided = self._is_test_constant_on_back_edge(
                func, test_instrs, self_loop,
            )
            rotatable.append((self_loop, self_loop_block, decided))

        if not rotatable:
            return func, False

        # Build one continue block per self-loop and retarget that self-loop to
        # it.  For an undecided test the block is a copy of the header test
        # with fresh SSA result ids, so every back-edge falls through into a
        # test rather than jumping to a shared one.  For a decided test the
        # block is a jump to the arm the header took, so the test is neither
        # re-executed nor branched on twice.
        next_id = self._max_value_id(func) + 1
        next_block_id = self._next_block_id(func)
        new_blocks: list[MenaiCFGBlock] = list(func.blocks)
        retarget: dict[int, int] = {}
        for self_loop, self_loop_block, decided in rotatable:
            if decided:
                new_blocks.append(
                    MenaiCFGBlock(
                        id=next_block_id,
                        label="loop_continue",
                        terminator=MenaiCFGJumpTerm(
                            target=self._decided_successor(
                                func, header, self_loop_block,
                            ),
                        ),
                    ),
                )
                next_block_id += 1
                retarget[self_loop_block.id] = next_block_id - 1
                continue

            remap: dict[int, MenaiCFGValue] = {}
            for instr in test_instrs:
                result = getattr(instr, 'result', None)
                if result is not None:
                    remap[result.id] = MenaiCFGValue(id=next_id, hint=result.hint)
                    next_id += 1

            new_instrs = tuple(
                self._clone_with_remap(instr, remap) for instr in test_instrs
            )
            new_cond = remap.get(branch.cond.id, branch.cond)

            continue_block = MenaiCFGBlock(
                id=next_block_id,
                label="loop_continue",
                instrs=new_instrs,
                terminator=MenaiCFGBranchTerm(
                    cond=new_cond,
                    true_block=branch.true_block,
                    false_block=body.id,
                ),
            )
            next_block_id += 1

            new_blocks.append(continue_block)
            retarget[self_loop_block.id] = continue_block.id

        # Retarget each self-loop to its rotated test block.
        def _retarget(block: MenaiCFGBlock) -> MenaiCFGBlock:
            if block.id not in retarget:
                return block

            term = block.terminator
            assert isinstance(term, MenaiCFGSelfLoopTerm)
            return replace(
                block,
                terminator=MenaiCFGSelfLoopTerm(
                    args=term.args,
                    param_vals=term.param_vals,
                    target=retarget[block.id],
                ),
            )

        new_blocks = [_retarget(block) for block in new_blocks]

        return replace(func, blocks=tuple(new_blocks)), True

    def _find_loops(
        self, func: MenaiCFGFunction,
    ) -> list[list[MenaiCFGSelfLoopTerm]]:
        """
        Return the function's loops, each as the list of its self-loop
        terminators, ordered innermost first.

        A tail-recursive loop can have more than one back-edge: when both
        arms of a branch in the body tail-call the enclosing function, each
        arm gets its own SelfLoopTerm, and all of them share the loop's
        header.  Self-loops are therefore grouped by the header they target.

        A function may contain several loops (sequential, nested, or both),
        so each group is a distinct loop rather than evidence of an
        unrotatable shape.  Groups are returned innermost first so that a
        nested loop is rotated before the loop that encloses it.
        """
        groups: dict[int, list[MenaiCFGSelfLoopTerm]] = {}
        headers: dict[int, MenaiCFGBlock] = {}
        by_id = blocks_by_id(func)
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSelfLoopTerm):
                header = self._self_loop_header(func, block.terminator, by_id)
                groups.setdefault(header.id, []).append(block.terminator)
                headers[header.id] = header

        # Order innermost first: a loop whose header lies inside another
        # loop's body region is nested within it, so it must be rotated
        # first.  A loop's nesting depth is the number of other loop headers
        # whose body region contains its header.
        #
        # Each header's body region is computed once here, not once per
        # (header, other_header) pair inside `depth`: the region depends only
        # on the header, so recomputing it in the inner loop would make the
        # depth computation quadratic in the number of loops.
        regions: dict[int, set[int]] = {}
        for header_id, header in headers.items():
            if isinstance(header.terminator, MenaiCFGBranchTerm):
                regions[header_id] = self._body_region(
                    by_id, by_id[header.terminator.false_block], header,
                )

        def depth(header_id: int) -> int:
            """Count how many other loops enclose the loop with this header."""
            count = 0
            for other_id, region in regions.items():
                if other_id == header_id:
                    continue

                if header_id in region:
                    count += 1

            return count

        return [groups[h] for h in sorted(groups, key=depth, reverse=True)]

    def _self_loop_header(
        self,
        func: MenaiCFGFunction,
        self_loop: MenaiCFGSelfLoopTerm,
        by_id: dict[int, MenaiCFGBlock],
    ) -> MenaiCFGBlock:
        """
        Return the header a self-loop targets (the entry block when unset).

        `by_id` is the function's block map, passed in so that a caller that
        resolves several self-loops does not rebuild it per call.
        """
        if self_loop.target is None:
            return func.entry()

        return by_id[self_loop.target]

    def _self_loop_block(
        self, func: MenaiCFGFunction, self_loop: MenaiCFGSelfLoopTerm,
    ) -> MenaiCFGBlock | None:
        """Return the block whose terminator is `self_loop`."""
        for block in func.blocks:
            if block.terminator is self_loop:
                return block

        return None

    def _successors(self, block: MenaiCFGBlock) -> list[int]:
        """
        Return the ids of the blocks a block's terminator can transfer control to.

        A self-loop terminator contributes no successor here: it always
        targets the loop header, which the region walk excludes.
        """
        term = block.terminator
        if term is None:
            return []

        if isinstance(term, MenaiCFGJumpTerm):
            return [term.target]

        if isinstance(term, MenaiCFGBranchTerm):
            return [term.true_block, term.false_block]

        if isinstance(term, MenaiCFGSwitchIntegerTerm):
            targets = [t for t in term.targets if t is not None]
            targets.append(term.default_block)
            return targets

        if isinstance(term, MenaiCFGSwitchEnumTerm):
            targets = list(term.targets)
            targets.append(term.default_block)
            return targets

        return []

    def _body_region(
        self,
        by_id: dict[int, MenaiCFGBlock],
        body_entry: MenaiCFGBlock,
        header: MenaiCFGBlock,
    ) -> set[int]:
        """
        Return the ids of the blocks forming the loop body region.

        The region is every block reachable from `body_entry` without passing
        through the loop header.  The header is the rotation point, so it is
        excluded; the self-loop back-edge is not followed (it targets the
        header, which is excluded anyway).  A single-block body yields a
        one-element set.

        `by_id` is the function's block map, passed in so that a caller that
        computes several regions does not rebuild it per call.
        """
        region: set[int] = set()
        stack = [body_entry.id]
        while stack:
            block_id = stack.pop()
            if block_id in region or block_id == header.id:
                continue

            region.add(block_id)
            stack.extend(self._successors(by_id[block_id]))

        return region

    def _is_back_edge_rotated(
        self,
        func: MenaiCFGFunction,
        self_loop: MenaiCFGSelfLoopTerm,
    ) -> bool:
        """
        Return True if this back-edge already falls through into a rotated test.

        Rotation retargets a back-edge to a fresh block holding a copy of the
        header test.  That block is entered only by the back-edge, whereas the
        unrotated header is also entered by the loop's entry path.  A
        back-edge is therefore already rotated when its target cannot be
        reached from the function entry without traversing the back-edge
        itself.

        The decision is per back-edge because a loop may rotate some of its
        back-edges and not others: one whose test cannot change value gets a
        continue block that holds only a jump.  Either kind of continue block
        is unreachable from the entry, so a rotated back-edge is skipped on
        every later round rather than retargeted again.
        """
        if self_loop.target is None:
            return False

        self_loop_block = self._self_loop_block(func, self_loop)
        reachable = self._reachable_without(func, self_loop_block)
        return self_loop.target not in reachable

    def _is_test_constant_on_back_edge(
        self,
        func: MenaiCFGFunction,
        test_instrs: list[MenaiCFGInstr],
        self_loop: MenaiCFGSelfLoopTerm,
    ) -> bool:
        """
        Return True if the header test cannot change value across `self_loop`.

        A rotated test re-executes at the back-edge and must be able to take
        either arm.  Its operands are evaluated there against whatever the
        back-edge leaves in their slots, so the test can only vary when at
        least one operand is rebound by the back-edge itself.  An operand the
        back-edge passes through unchanged is the value the header just read
        and the body cannot have changed, so the test on it is decided: one
        arm is dead and the comparison is repeated work every iteration.

        An operand is considered to vary when the back-edge rebinds it.  A
        function-level self-loop binds its args positionally onto the loop's
        params, so the slot for an arg is the param at that position.  Reading
        the args rather than the target block's phis is deliberate: a
        back-edge that changes a parameter's slot from the second iteration
        onwards passes the parameter itself in that position, and reading the
        phi would report that parameter as rebound when its slot is in fact
        untouched.  A `MenaiIRLoop` back-edge binds `param_vals` instead, so
        the slot for an arg is the loop-carried phi at that position.
        """
        if self_loop.param_vals is not None:
            slots = list(self_loop.param_vals)

        else:
            slots = self._param_slots(func)[:len(self_loop.args)]

        rebound = {
            slot.id
            for slot, arg in zip(slots, self_loop.args)
            if arg.id != slot.id
        }

        return not any(
            operand_id in rebound
            for instr in test_instrs
            for operand_id in value_ids_in_instr(instr)
        )

    def _param_slots(self, func: MenaiCFGFunction) -> list[MenaiCFGValue]:
        """
        Return the function's parameter SSA values, in parameter order.

        A function-level self-loop writes its args positionally into these
        slots, so they are the destinations a rotated test would read.
        """
        param_ids = {
            instr.index: instr.result
            for instr in func.entry().instrs
            if isinstance(instr, MenaiCFGParamInstr)
        }

        return [param_ids[index] for index in sorted(param_ids)]

    def _decided_successor(
        self,
        func: MenaiCFGFunction,
        header: MenaiCFGBlock,
        self_loop_block: MenaiCFGBlock,
    ) -> int:
        """
        Return the block a decided back-edge transfers to.

        The test is decided on this back-edge because none of its operands is
        rebound by it, and the header has already branched on the same
        condition.  Every successor of a branch is dominated by that branch,
        so the back-edge runs on a path that took exactly one of the
        successors, and which one is fixed for as long as control is on that
        path.  The successor that dominates the back-edge is therefore the one
        its taken arm reached.

        Exactly one successor can dominate it: on a path through one arm the
        other arm's successor is not on the path.
        """
        dominators = compute_dominators(func)[self_loop_block.id]
        for successor in self._successors(header):
            if successor in dominators:
                return successor

        raise MenaiInternalError(
            f"loop rotation: no successor of block {header.id} dominates the "
            f"back-edge in block {self_loop_block.id}",
        )

    def _reachable_without(
        self,
        func: MenaiCFGFunction,
        avoid: MenaiCFGBlock | None,
    ) -> set[int]:
        """
        Return the ids of blocks reachable from the function entry without
        entering `avoid`.

        Used to decide whether a self-loop target is entered by the loop's
        entry path (reachable) or only by the back-edge (not reachable).
        """
        by_id = blocks_by_id(func)
        seen: set[int] = set()
        stack = [func.entry().id]
        while stack:
            block_id = stack.pop()
            if block_id in seen or (avoid is not None and block_id == avoid.id):
                continue

            seen.add(block_id)
            stack.extend(self._successors(by_id[block_id]))

        return seen

    def _header_test_instrs(
        self, header: MenaiCFGBlock,
    ) -> list[MenaiCFGInstr]:
        """
        Return the header block's test instructions.

        This is every instruction except the param, free-var, and phi
        definitions.  Param and free-var definitions establish the parameter
        and capture slots once and must not be re-executed on the back-edge.
        Phi definitions are the loop-carried variables; their slots are
        updated in place by the back-edge moves, so copying them into the
        rotated test would produce a second phi with no predecessors.
        """
        return [
            instr for instr in header.instrs
            if not isinstance(
                instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr, MenaiCFGPhiInstr)
            )
        ]

    def _operands_safe_at_back_edge(
        self,
        func: MenaiCFGFunction,
        test_instrs: list[MenaiCFGInstr],
        header: MenaiCFGBlock,
    ) -> bool:
        """
        Return True if the header test can be re-evaluated at the back-edge
        with correct values.

        The rotated test shares the header test's operands.  Those values are
        correct at the back-edge only when they are:

          - params (updated in place by the back-edge moves, so reading them
            after the self-loop observes the new value),
          - phi results (the loop-carried variables, likewise updated in place
            by the back-edge moves),
          - free vars (never reassigned), or
          - defined outside the loop-header block (in the preamble or an
            entry block that dominates the back-edge).

        An operand defined inside the loop-header block by a loop-variant
        instruction would be stale at the back-edge, so such a test is not
        rotated.  Operands defined within the header test itself are remapped
        to the copy's own definitions and are therefore safe.
        """
        header_ids = {id(instr) for instr in header.instrs}
        test_ids = {id(instr) for instr in test_instrs}

        for instr in test_instrs:
            for operand_id in value_ids_in_instr(instr):
                defining = self._defining_instr(func, operand_id)
                if defining is None:
                    # No defining instruction (a param or free var whose value
                    # is established by the entry block).  Safe.
                    continue

                if isinstance(defining, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr)):
                    continue

                if isinstance(defining, MenaiCFGPhiInstr):
                    continue

                if id(defining) in test_ids:
                    # Defined within the copied test; the remap handles it.
                    continue

                if id(defining) in header_ids:
                    return False

        return True

    def _test_results_used_by_body(
        self,
        func: MenaiCFGFunction,
        test_instrs: list[MenaiCFGInstr],
        body_region: set[int],
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> bool:
        """
        Return True if any header test result is consumed by the loop body.

        After rotation the back-edge skips the header test, so a value the
        header computes is only available on the first iteration.  If the
        body reads such a value, the rotated loop would use a stale value on
        later iterations.  A header test whose results flow into the body is
        therefore not rotated.

        Every block in the body region is checked, not just the entry block,
        because the body may span several blocks.  Every self-loop's args are
        checked too: a value passed through them is a loop-carried value and
        must be recomputed each iteration.

        A block's terminator is an input site in its own right — a tail call
        passes its arguments there, not through an instruction — so it is
        scanned as well as the block's instructions.  Missing it means a
        `remaining`-style value computed by the header test and passed on by
        the body's tail call goes unnoticed, and the rotated loop then reads a
        value that only the peeled entry test ever computed.
        """
        test_result_ids = {
            result.id
            for instr in test_instrs
            if (result := getattr(instr, 'result', None)) is not None
        }
        if not test_result_ids:
            return False

        body_uses: set[int] = set()
        for block in func.blocks:
            if block.id not in body_region:
                continue

            for instr in block.instrs:
                body_uses.update(value_ids_in_instr(instr))

            if block.terminator is not None:
                body_uses.update(value_ids_in_term(block.terminator))

        for self_loop in self_loops:
            body_uses.update(arg.id for arg in self_loop.args)

        return bool(test_result_ids & body_uses)

    def _defining_instr(
        self, func: MenaiCFGFunction, value_id: int,
    ) -> MenaiCFGInstr | None:
        """Return the instruction that defines `value_id`, or None."""
        for block in func.blocks:
            for instr in block.instrs:
                result = getattr(instr, 'result', None)
                if result is not None and result.id == value_id:
                    return instr

        return None

    def _clone_with_remap(
        self, instr: MenaiCFGInstr, remap: dict[int, MenaiCFGValue],
    ) -> MenaiCFGInstr:
        """
        Clone an instruction, remapping its result and operands.

        A result or operand value id present in `remap` is replaced by the
        remapped value; ids absent from `remap` are shared with the original
        instruction (they are params, free vars, or values defined outside the
        header test).
        """
        def rv(value: MenaiCFGValue) -> MenaiCFGValue:
            """Remap a single SSA value."""
            return remap.get(value.id, value)

        def rvs(values: tuple[MenaiCFGValue, ...]) -> tuple[MenaiCFGValue, ...]:
            """Remap a sequence of SSA values."""
            return tuple(rv(v) for v in values)

        if isinstance(instr, MenaiCFGConstInstr):
            return MenaiCFGConstInstr(result=rv(instr.result), value=instr.value)

        if isinstance(instr, MenaiCFGBuiltinInstr):
            return MenaiCFGBuiltinInstr(
                result=rv(instr.result), op=instr.op, args=rvs(instr.args),
            )

        if isinstance(instr, MenaiCFGCallInstr):
            return MenaiCFGCallInstr(
                result=rv(instr.result), func=rv(instr.func), args=rvs(instr.args),
            )

        if isinstance(instr, MenaiCFGApplyInstr):
            return MenaiCFGApplyInstr(
                result=rv(instr.result), func=rv(instr.func), arg_list=rv(instr.arg_list),
            )

        if isinstance(instr, MenaiCFGMakeClosureInstr):
            return MenaiCFGMakeClosureInstr(
                result=rv(instr.result),
                function=instr.function,
                captures=rvs(instr.captures),
                needs_patching=instr.needs_patching,
            )

        if isinstance(instr, MenaiCFGMakeStructInstr):
            return MenaiCFGMakeStructInstr(
                result=rv(instr.result), struct_type=instr.struct_type, args=rvs(instr.args),
            )

        if isinstance(instr, MenaiCFGMakeListInstr):
            return MenaiCFGMakeListInstr(result=rv(instr.result), args=rvs(instr.args))

        if isinstance(instr, MenaiCFGMakeVectorInstr):
            return MenaiCFGMakeVectorInstr(result=rv(instr.result), args=rvs(instr.args))

        if isinstance(instr, MenaiCFGMakeSetInstr):
            return MenaiCFGMakeSetInstr(result=rv(instr.result), args=rvs(instr.args))

        if isinstance(instr, MenaiCFGMakeDictInstr):
            return MenaiCFGMakeDictInstr(
                result=rv(instr.result),
                pairs=tuple((rv(k), rv(v)) for k, v in instr.pairs),
            )

        if isinstance(instr, MenaiCFGStructGetIndexedInstr):
            return MenaiCFGStructGetIndexedInstr(
                result=rv(instr.result), struct=rv(instr.struct), index=instr.index,
            )

        if isinstance(instr, MenaiCFGStructWithIndexedInstr):
            return MenaiCFGStructWithIndexedInstr(
                result=rv(instr.result),
                struct=rv(instr.struct),
                index=instr.index,
                value=rv(instr.value),
            )

        if isinstance(instr, MenaiCFGGuardInstr):
            return MenaiCFGGuardInstr(value=rv(instr.value), expected_type=instr.expected_type)

        if isinstance(instr, MenaiCFGPhiInstr):
            return MenaiCFGPhiInstr(
                result=rv(instr.result),
                incoming=tuple((rv(v), block) for v, block in instr.incoming),
            )

        raise TypeError(
            f"MenaiCFGLoopRotation: cannot clone {type(instr).__name__}"
        )

    def _next_block_id(self, func: MenaiCFGFunction) -> int:
        """Return the next available block id in func."""
        return max(b.id for b in func.blocks) + 1

    def _max_value_id(self, func: MenaiCFGFunction) -> int:
        """Return the highest SSA value id present anywhere in func."""
        max_id = -1
        for block in func.blocks:
            for instr in block.instrs:
                result = getattr(instr, 'result', None)
                if result is not None:
                    max_id = max(max_id, result.id)

                for vid in value_ids_in_instr(instr):
                    max_id = max(max_id, vid)

            term = block.terminator
            if isinstance(term, MenaiCFGBranchTerm):
                max_id = max(max_id, term.cond.id)

            elif isinstance(term, MenaiCFGSelfLoopTerm):
                for arg in term.args:
                    max_id = max(max_id, arg.id)

        return max_id
