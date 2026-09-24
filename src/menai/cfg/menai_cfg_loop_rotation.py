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
The pass handles the canonical shape only:

  - one or more `MenaiCFGSelfLoopTerm` terminators, all sharing a single
    loop header (a tail-recursive loop with both arms of a branch calling
    the enclosing function has one self-loop per arm),
  - the loop header (the self-loops' target, or the entry block when the
    target is unset) ends in a `MenaiCFGBranchTerm`,
  - the header test's operands are all params, free vars, or values defined
    outside the header block (so they are valid at the back-edge), and
  - the branch's false target is the entry of the loop body and every
    self-loop block is reachable from it without passing back through the
    header (the body is a region entered at the false target and exited by
    the self-loops; it may span several blocks), and
  - no header test result is consumed anywhere in the body region.

Anything else is left unrotated.  The pass is idempotent: an already-rotated
loop (whose self-loop target blocks are copies of the header test) is
detected and skipped.
"""

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
    MenaiCFGStructSetIndexedInstr,
    MenaiCFGSwitchTerm,
    MenaiCFGValue,
    relink_predecessors,
    value_ids_in_instr,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGPerFunctionPass


class MenaiCFGLoopRotation(MenaiCFGPerFunctionPass):
    """
    CFG optimization pass that rotates a self-loop so its test is at the
    bottom, replacing the unconditional back-edge with a conditional one.

    See the module docstring for the algorithm and applicability conditions.
    """

    def _optimize_function(
        self, func: MenaiCFGFunction,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Rotate a self-loop if the function has the canonical loop shape."""
        self_loops = self._find_self_loops(func)
        if self_loops is None:
            return func, False

        header = self._self_loop_header(func, self_loops[0])

        if not isinstance(header.terminator, MenaiCFGBranchTerm):
            return func, False

        branch = header.terminator
        body = branch.false_block

        # Canonical shape: the header branch's false target is the entry of
        # the loop body, and every self-loop terminates that body.  The body
        # may span several blocks (e.g. when it contains a nested branch);
        # what matters is that each self-loop block is reachable from the
        # body entry without passing back through the header, so the body is
        # a region entered at `body` and exited by the self-loops.
        body_region = self._body_region(body, header)
        for self_loop in self_loops:
            self_loop_block = self._self_loop_block(func, self_loop)
            if self_loop_block is None or self_loop_block.id not in body_region:
                return func, False

        if self._is_rotated(self_loops, header, body):
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

        # Build one rotated test block per self-loop: a copy of the header
        # test with fresh SSA result ids, terminated by a branch with the
        # header's targets.  Each self-loop is retargeted to its own copy so
        # that every back-edge falls through into a test rather than jumping
        # to a shared one.  A single shared test block could only be the
        # fall-through successor of one back-edge, leaving the others with an
        # unconditional jump, which is exactly what rotation removes.
        next_id = self._max_value_id(func) + 1
        next_block_id = self._next_block_id(func)
        for self_loop in self_loops:
            remap: dict[int, MenaiCFGValue] = {}
            for instr in test_instrs:
                result = getattr(instr, 'result', None)
                if result is not None:
                    remap[result.id] = MenaiCFGValue(id=next_id, hint=result.hint)
                    next_id += 1

            new_instrs = [
                self._clone_with_remap(instr, remap) for instr in test_instrs
            ]
            new_cond = remap.get(branch.cond.id, branch.cond)

            continue_block = MenaiCFGBlock(
                id=next_block_id,
                label="loop_continue",
                instrs=new_instrs,
                terminator=MenaiCFGBranchTerm(
                    cond=new_cond,
                    true_block=branch.true_block,
                    false_block=body,
                ),
            )
            next_block_id += 1

            func.blocks.append(continue_block)
            self_loop.target = continue_block

        relink_predecessors(func)
        return func, True

    def _find_self_loops(
        self, func: MenaiCFGFunction,
    ) -> list[MenaiCFGSelfLoopTerm] | None:
        """
        Return the function's self-loop terminators, or None.

        A tail-recursive loop can have more than one back-edge: when both
        arms of a branch in the body tail-call the enclosing function, each
        arm gets its own SelfLoopTerm.  Returns None when there are no
        self-loops, or when they do not all share one loop header (which
        would mean more than one distinct loop, not a single rotatable one).
        """
        found: list[MenaiCFGSelfLoopTerm] = []
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSelfLoopTerm):
                found.append(block.terminator)

        if not found:
            return None

        header = self._self_loop_header(func, found[0])
        for self_loop in found[1:]:
            if self._self_loop_header(func, self_loop) is not header:
                return None

        return found

    def _self_loop_header(
        self, func: MenaiCFGFunction, self_loop: MenaiCFGSelfLoopTerm,
    ) -> MenaiCFGBlock:
        """Return the header a self-loop targets (the entry block when unset)."""
        return self_loop.target if self_loop.target is not None else func.entry()

    def _self_loop_block(
        self, func: MenaiCFGFunction, self_loop: MenaiCFGSelfLoopTerm,
    ) -> MenaiCFGBlock | None:
        """Return the block whose terminator is `self_loop`."""
        for block in func.blocks:
            if block.terminator is self_loop:
                return block

        return None

    def _successors(self, block: MenaiCFGBlock) -> list[MenaiCFGBlock]:
        """
        Return the blocks a block's terminator can transfer control to.

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

        if isinstance(term, MenaiCFGSwitchTerm):
            targets = [t for t in term.targets if t is not None]
            targets.append(term.default_block)
            return targets

        return []

    def _body_region(
        self, body_entry: MenaiCFGBlock, header: MenaiCFGBlock,
    ) -> set[int]:
        """
        Return the ids of the blocks forming the loop body region.

        The region is every block reachable from `body_entry` without passing
        through the loop header.  The header is the rotation point, so it is
        excluded; the self-loop back-edge is not followed (it targets the
        header, which is excluded anyway).  A single-block body yields a
        one-element set.
        """
        region: set[int] = set()
        stack = [body_entry]
        while stack:
            block = stack.pop()
            if block.id in region or block is header:
                continue

            region.add(block.id)
            stack.extend(self._successors(block))

        return region

    def _is_rotated(
        self,
        self_loops: list[MenaiCFGSelfLoopTerm],
        header: MenaiCFGBlock,
        body: MenaiCFGBlock,
    ) -> bool:
        """
        Return True if the loop is already rotated.

        A rotated loop has every self-loop target set to a block (other than
        the header) whose terminator branches back to the body.  The original
        unrotated header also branches to the body, so the self-loop target
        is what distinguishes the two.  A partially rotated loop cannot occur
        (the pass retargets all self-loops or none), so every self-loop is
        checked and any unrotated one means the whole loop is unrotated.
        """
        for self_loop in self_loops:
            if self_loop.target is None or self_loop.target is header:
                return False

            term = self_loop.target.terminator
            if not isinstance(term, MenaiCFGBranchTerm):
                return False

            if term.false_block is not body or term.true_block is body:
                return False

        return True

    def _header_test_instrs(
        self, header: MenaiCFGBlock,
    ) -> list[MenaiCFGInstr]:
        """
        Return the header block's test instructions.

        This is every instruction except the param and free-var definitions,
        which establish the parameter and capture slots once and must not be
        re-executed on the back-edge.
        """
        return [
            instr for instr in header.instrs
            if not isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
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

        def rvs(values: list[MenaiCFGValue]) -> list[MenaiCFGValue]:
            """Remap a list of SSA values."""
            return [rv(v) for v in values]

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
                pairs=[(rv(k), rv(v)) for k, v in instr.pairs],
            )

        if isinstance(instr, MenaiCFGStructGetIndexedInstr):
            return MenaiCFGStructGetIndexedInstr(
                result=rv(instr.result), struct=rv(instr.struct), index=instr.index,
            )

        if isinstance(instr, MenaiCFGStructSetIndexedInstr):
            return MenaiCFGStructSetIndexedInstr(
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
                incoming=[(rv(v), block) for v, block in instr.incoming],
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
