"""
CFG pass: loop-invariant code motion (LICM).

When a function contains a SelfLoopTerm (a tail-recursive loop), instructions
whose operands are all loop-invariant are hoisted into a preamble block that
executes once on function entry.  Every SelfLoopTerm's target is set to the
loop-entry block (the original entry minus the hoisted instructions), so the
self-loop skips the preamble on every iteration after the first.  A loop may
have more than one back-edge (both arms of a branch tail-calling the enclosing
function); all of them are retargeted.

Multiple loops per function
---------------------------
A function may contain more than one loop.  This happens when the IR inliner
splices a letrec-to-loop body (an inner loop) into a function that is itself
a loop, producing a loop nested inside another loop; it also happens for
sequential loops.  Self-loops are grouped by the header they target, and each
group is a distinct loop.  Every loop is optimised independently, innermost
first: hoisting out of an inner loop only moves instructions into its own
pre-header, which lies inside the enclosing loop, so it cannot invalidate the
region an enclosing loop's analysis was derived from.  Processing the
innermost loop first means a value that is invariant for both an inner and an
enclosing loop is first hoisted to the inner pre-header and then, on a later
fixed-point iteration, hoisted further out to the enclosing pre-header.

Loop region
-----------
The blocks that make up a loop are the header plus every block that can reach
one of the loop's back-edge blocks without passing through the header, walking
predecessors.  The back-edge blocks are exactly the blocks terminated by the
loop's SelfLoopTerms, so no dominator computation is needed.

This is deliberately not a forward reachability walk from the header, and not
a "blocks dominated by the header" set.  Both over-include.  For a loop
nested inside another, the inner header is reachable from the outer loop's
later blocks (via the outer back-edge and the next outer iteration), so a
forward walk would pull the whole enclosing loop into the inner region.  And a
loop that sits at the top of a function (e.g. an inlined `skip-ws` loop) has
its header on the path from the entry to everything after it, so the header
dominates the rest of the function even though that code is not in the loop.
The back-edge walk stops at the header, so it reaches neither an enclosing
loop nor the code that follows the loop.

Menai is pure (no side effects, no mutation), so hoisting cannot change the
*result* an instruction computes: there are no side effects, memory aliasing,
or ordering hazards to check.  Purity does not, however, make hoisting safe in
general, because an instruction can also *raise*.

A raise is observable — the language has no handlers, so a raise terminates
the program — and hoisting moves an instruction to the preamble, which runs
once before the first iteration.  Hoisting a raising instruction out of a
block that does not execute on every iteration would make it execute on
iterations that previously skipped it, which can make a program raise where it
previously completed.  A raising instruction is therefore hoisted only from a
block that dominates every back-edge block, i.e. one that executes on every
iteration (ADR-0049).  An instruction that cannot raise is hoisted whenever it
is loop-invariant.

Loop-invariant values
---------------------
An SSA value is loop-invariant if it is defined by an instruction whose
operands are all loop-invariant.  The base cases are:

  - Constants (MenaiCFGConstInstr) — always invariant.
  - Free vars (MenaiCFGFreeVarInstr) — always invariant (captures don't change).
  - Params (MenaiCFGParamInstr) — invariant when the param is not reassigned
    by the self-loop: either its index is beyond the length of
    SelfLoopTerm.args, or every back-edge passes the param itself.

Guards on loop-carried values
-----------------------------
A guard on a value that the self-loop reassigns is never treated as
loop-invariant.  The back-edge type describes the value from the second
iteration onwards; the first iteration's value is whatever the caller passed,
which the back-edge says nothing about.  Hoisting or dropping such a guard on
the strength of the back-edge type would rely on a fact that only holds after
the first iteration.

Such a guard is rewritten instead: a guard on the value's initial incoming
value is placed in the preamble, which checks the first iteration once, and
the original guard is dropped.  The back-edge preserves the type on every
later iteration, so nothing is left unchecked.

Block splitting
---------------
When at least one instruction is hoisted, the hoisting target depends on
whether the loop header is the entry block:

  - Function-level loop (header is the entry block): the entry block is split
    into a preamble and a new loop-entry block.
      - Preamble: ParamInstr/FreeVarInstr definitions, then hoisted
        instructions in dependency order, terminated by a JumpTerm to the
        loop-entry block.
      - Loop-entry: remaining instructions (non-hoisted), with the original
        terminator.
    Each SelfLoopTerm's target is set to the loop-entry block.  The VCode
    builder emits the self-loop jump targeting that block's own label, so the
    back-edge resolves to the loop-entry rather than the preamble.

  - MenaiIRLoop (header is a distinct loop-entry block): the header already
    has a pre-header that dominates it and the back-edge already skips it, so
    the hoisted instructions are appended to that pre-header and the
    SelfLoopTerms' targets are left unchanged.  Splitting the entry block in
    this case would orphan the header's phi nodes.

Hoisting from non-entry blocks
------------------------------
Loop-invariant instructions in non-entry blocks (e.g. inside a conditional
branch within the loop body) are also hoisted into the preamble.  They are
removed from their original block and appended to the preamble in the order
they are discovered.

Dependency ordering
-------------------
Hoisted instructions are appended to the preamble in the order they are
encountered during a forward walk of all blocks.  Because SSA values are
single-assignment and an instruction's operands must be defined before it,
a forward walk naturally places producers before consumers.  When an
instruction in block B depends on a value defined in block A (where A
precedes B in the block list), the producer is hoisted first.

The pass returns a new MenaiCFGFunction; it does not mutate its input.  It
moves instructions between block.instrs lists and may create a new block.
"""

from dataclasses import replace

from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.menai_builtin_registry import MenaiBuiltinRegistry
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
    MenaiCFGSwitchIntegerTerm,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGValue,
    blocks_by_id,
    predecessors,
    value_ids_in_instr,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    compute_dominators,
    compute_dominators_in_subgraph,
)
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
    MenaiVector,
)

# Instruction types that can be hoisted (produce a result and are pure).
# MenaiCFGPhiInstr is excluded — phi nodes are join points tied to their block.
# MenaiCFGPatchClosureInstr is excluded — it has no result and is a side-effecting
# mutation (closure initialisation), stored in patch_instrs not instrs.
_HOISTABLE_TYPES = (
    MenaiCFGConstInstr,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGApplyInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGGuardInstr,
)

_VALUE_TYPE_MAP = {
    MenaiNone: 'none',
    MenaiBoolean: 'boolean',
    MenaiInteger: 'integer',
    MenaiFloat: 'float',
    MenaiComplex: 'complex',
    MenaiString: 'string',
    MenaiSymbol: 'symbol',
    MenaiList: 'list',
    MenaiDict: 'dict',
    MenaiSet: 'set',
    MenaiVector: 'vector',
    MenaiFunction: 'function',
    MenaiBytes: 'bytes',
    MenaiStruct: 'struct',
    MenaiStructType: 'structtype',
}


def _value_type(value: object) -> str | None:
    """Return the Menai type name for a MenaiValue instance, or None."""
    for cls, name in _VALUE_TYPE_MAP.items():
        if isinstance(value, cls):
            return name

    return None


class MenaiCFGLICM(MenaiCFGPerFunctionPass):
    """
    CFG optimization pass that hoists loop-invariant instructions out of
    self-loops into a preamble block.

    See module docstring for the algorithm description.
    """

    def _optimize_function(
        self, func: MenaiCFGFunction, context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Hoist loop-invariant instructions from every loop in the function.

        A function may contain more than one loop (nested or sequential), so
        each loop is optimised independently.  Loops are processed innermost
        first so that hoisting out of an inner loop (which only moves
        instructions into its own pre-header, inside the enclosing loop)
        cannot invalidate the region an enclosing loop's analysis was derived
        from.
        """
        changed = False
        for loop in self._find_loops(func):
            func, loop_changed = self._optimize_loop(func, loop)
            if loop_changed:
                changed = True

        return func, changed

    def _optimize_loop(
        self,
        func: MenaiCFGFunction,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Hoist loop-invariant instructions from a single loop into a preamble.

        `self_loops` are the loop's back-edges: all the SelfLoopTerms that
        share one header.  The header, region, and pre-header are re-derived
        from the current CFG, so this is safe to call after other loops in the
        same function have been optimised.
        """
        entry = func.entry()
        header = self._self_loop_header(func, self_loops[0])

        # When the loop header is not the entry block (a MenaiIRLoop lowered to
        # a distinct loop-entry block), the header already has a pre-header that
        # dominates it and the back-edge already skips it.  Hoisting goes into
        # that pre-header and the self-loop target is left unchanged.  Splitting
        # the entry block in this case would orphan the header's phi nodes.
        #
        # When no such block exists, the loop cannot be hoisted out of: the
        # preamble is the only place an invariant instruction may go, and there
        # is no block that dominates the header to put it in.
        pre_header: MenaiCFGBlock | None = None
        if header is not entry:
            pre_header = self._pre_header(func, header, self_loops)
            if pre_header is None:
                return func, False

        param_ids = self._param_ids_by_index(entry)
        unchanged_param_ids = self._unchanged_param_ids(self_loops, param_ids)

        # Build a map from SSA value id to defining instruction for type lookup.
        def_map = self._build_def_map(func)

        # Compute back-edge types for guard invariance checks.
        back_edge_types = self._back_edge_types(self_loops, param_ids, def_map)

        # Compute the set of loop-invariant SSA value ids.
        invariant_ids = self._compute_invariant_values(
            func, unchanged_param_ids,
        )

        # Only instructions inside the loop may be hoisted.  The region is the
        # header plus the blocks that reach a back-edge without passing through
        # the header; a pre-header, an enclosing-loop block, or code after the
        # loop is excluded, so an instruction is never moved across a use or
        # out of a loop it is not invariant for.
        loop_block_ids = self._loop_region(func, header, self_loops)

        # Blocks that execute on every iteration.  A raising instruction may be
        # hoisted only from one of these: hoisting it moves it to the preamble,
        # which runs once before the first iteration, so an instruction that
        # does not execute on every iteration would be made to execute on
        # iterations that did not previously execute it (ADR-0049).
        every_iteration_ids = self._blocks_on_every_iteration(
            func, header, self_loops, loop_block_ids,
        )

        # The block each hoisted instruction will be moved into.  For a
        # MenaiIRLoop that is the pre-header; for a function-level loop the
        # entry block is split, so the target is the entry block itself (the
        # hoisted instructions go into the new preamble, which is that block).
        hoist_target_id = pre_header.id if pre_header is not None else entry.id

        # Guards on loop-carried phis are rewritten rather than moved: see
        # _rewrite_phi_guards.  The rewritten guards go in the same preamble.
        rewritten_guards = self._rewrite_phi_guards(
            func, self_loops, back_edge_types, def_map, loop_block_ids,
        )

        # Dominators of the whole function, and the block that defines each SSA
        # value.  A guard may be hoisted only to a point its operand's defining
        # block dominates.
        dom = compute_dominators(func)
        def_block_ids = self._defining_block_ids(func)

        # Collect hoistable instructions from blocks inside the loop.
        preamble_instrs: list[MenaiCFGInstr] = []

        # Guards already in the preamble.  A rewritten guard that duplicates
        # one of them is redundant: the preamble already checks that type on
        # that value before the loop is entered.
        #
        # A function-level self-loop's hoist target is the entry block, whose
        # guards are about to be split into the preamble and the loop entry.
        # Those guards are not "already in the preamble" in the sense that
        # matters here, so they are not seeded: seeding them would suppress
        # the hoisted copy while the original is moved out, dropping the guard
        # entirely.
        seen_guards: set[tuple[int, str]] = (
            self._guards_in_block(func, hoist_target_id)
            if pre_header is not None
            else set()
        )

        for guard in rewritten_guards:
            key = (guard.value.id, guard.expected_type)
            if key in seen_guards:
                continue

            seen_guards.add(key)
            preamble_instrs.append(guard)

        # The loop-carried values whose guard the preamble now covers.  The
        # guard on such a value is dropped from its block: the preamble guard
        # checks the value's first-iteration value, and the back-edge
        # preserves the type on every later one.
        #
        # A guard that already sits in the hoist target is left alone.  That
        # is the function-level self-loop case, where the param is defined in
        # the entry block that becomes the preamble: the guard is already
        # where it needs to be.
        covered_value_ids = self._rewritten_value_ids(
            func, self_loops, back_edge_types, def_map, loop_block_ids,
        )

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            if block.id not in loop_block_ids:
                new_blocks.append(block)
                continue

            remaining: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if (
                    isinstance(instr, MenaiCFGGuardInstr)
                    and instr.value.id in covered_value_ids
                    and block.id != hoist_target_id
                ):
                    continue

                if self._is_hoistable(
                    instr, invariant_ids, block.id, every_iteration_ids,
                    hoist_target_id, dom, def_block_ids,
                ):
                    if isinstance(instr, MenaiCFGGuardInstr):
                        key = (instr.value.id, instr.expected_type)
                        if key in seen_guards:
                            continue

                        seen_guards.add(key)

                    preamble_instrs.append(instr)

                else:
                    remaining.append(instr)

            new_blocks.append(replace(block, instrs=tuple(remaining)))

        if not preamble_instrs:
            return func, False

        if pre_header is not None:
            # MenaiIRLoop case: append the hoisted instructions to the
            # pre-header, before its terminator.  The self-loop already targets
            # the header, which the pre-header dominates, so the hoisted
            # instructions run once and the back-edge skips them.
            new_blocks = [
                replace(b, instrs=b.instrs + tuple(preamble_instrs))
                if b.id == pre_header.id
                else b
                for b in new_blocks
            ]
            return replace(func, blocks=tuple(new_blocks)), True

        # Function-level self-loop: the header is the entry block, so split it
        # into a preamble (param/free-var definitions plus hoisted instructions)
        # and a new loop-entry block that the self-loop targets.
        # The entry block in `new_blocks` already has the hoisted instructions
        # removed; use it as the source for the split.
        entry = next(b for b in new_blocks if b.id == entry.id)
        def_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        loop_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if not isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        # Preamble: param/free-var definitions, then hoisted instructions.
        loop_entry_id = self._next_block_id(func)
        new_entry = replace(
            entry,
            instrs=tuple(def_instrs + preamble_instrs),
            terminator=MenaiCFGJumpTerm(target=loop_entry_id),
        )
        loop_entry = MenaiCFGBlock(
            id=loop_entry_id,
            label="loop_entry",
            instrs=tuple(loop_instrs),
            terminator=entry.terminator,
        )

        # Retarget this loop's self-loops to the new loop-entry block.  Other
        # loops' self-loops are left alone: a function may contain more than
        # one loop, and only this loop's back-edges should skip this preamble.
        self_loop_block_ids = self._self_loop_block_ids(func, self_loops)

        def _retarget(b: MenaiCFGBlock) -> MenaiCFGBlock:
            if b.id not in self_loop_block_ids:
                return b

            term = b.terminator
            assert isinstance(term, MenaiCFGSelfLoopTerm)
            return replace(
                b,
                terminator=MenaiCFGSelfLoopTerm(
                    args=term.args,
                    param_vals=term.param_vals,
                    target=loop_entry.id,
                ),
            )

        rebuilt = [_retarget(b) for b in new_blocks]
        rebuilt = [
            new_entry if b.id == entry.id else b for b in rebuilt
        ]

        # The entry block's outgoing edges moved to loop_entry (it inherited
        # the original terminator), so a successor block's phi that named the
        # entry block as an incoming predecessor must now name loop_entry.
        # The VCode builder emits a phi-elimination move in a predecessor block
        # only when the phi's incoming predecessor is that block; a stale
        # reference would drop the move and leave the phi's register
        # uninitialised.
        successor_ids = set(self._successor_ids(loop_entry))
        rebuilt = [
            self._retarget_phi_incoming(b, entry.id, loop_entry.id)
            if b.id in successor_ids
            else b
            for b in rebuilt
        ]

        rebuilt.append(loop_entry)

        return replace(func, blocks=tuple(rebuilt)), True

    def _compute_invariant_values(
        self,
        func: MenaiCFGFunction,
        unchanged_param_ids: set[int],
    ) -> set[int]:
        """
        Compute the set of SSA value ids that are loop-invariant.

        An SSA value is loop-invariant if its defining instruction's operands
        are all loop-invariant.  Base cases:
          - Constants, globals, free vars — always invariant.
          - Params not reassigned by the self-loop — invariant.

        Iterates to a fixed point: a value produced by a hoistable
        instruction becomes invariant once all its operands are invariant,
        which may in turn make downstream instructions invariant.
        """
        invariant: set[int] = set()

        # Seed: base-case invariant values.
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGConstInstr):
                    invariant.add(instr.result.id)

                elif isinstance(instr, MenaiCFGFreeVarInstr):
                    invariant.add(instr.result.id)

                elif isinstance(instr, MenaiCFGParamInstr):
                    if instr.result.id in unchanged_param_ids:
                        invariant.add(instr.result.id)

        # Iterate to fixed point: propagate invariance through hoistable
        # instructions.  A hoistable instruction is invariant when all its
        # input value ids are in the invariant set.
        while True:
            changed = False
            for block in func.blocks:
                for instr in block.instrs:
                    if not isinstance(instr, _HOISTABLE_TYPES):
                        continue

                    if isinstance(instr, MenaiCFGGuardInstr):
                        continue

                    result = getattr(instr, 'result', None)
                    if result is None or result.id in invariant:
                        continue

                    operand_ids = value_ids_in_instr(instr)
                    if all(oid in invariant for oid in operand_ids):
                        invariant.add(result.id)
                        changed = True

            if not changed:
                break

        # A guard on a reassigned loop-carried value is deliberately not added
        # to the invariant set, even when the back-edge type matches.  The
        # back-edge type describes the value from the second iteration
        # onwards; the first iteration's value is whatever the caller passed,
        # which the back-edge says nothing about.  Treating such a guard as
        # invariant would let it be hoisted or dropped on the strength of a
        # fact that only holds after the first iteration.
        #
        # `_rewrite_phi_guards` handles these guards instead: it replaces the
        # guard with one on the value's initial incoming value, placed in the
        # preamble, which checks the first iteration and is skipped by the
        # back-edge.

        return invariant

    def _is_hoistable(
        self,
        instr: MenaiCFGInstr,
        invariant_ids: set[int],
        block_id: int,
        every_iteration_ids: set[int],
        hoist_target_id: int,
        dom: dict[int, set[int]],
        def_block_ids: dict[int, int],
    ) -> bool:
        """
        Return True if an instruction should be hoisted into the preamble.

        A hoistable instruction type whose result is loop-invariant is
        hoisted.  Guards are hoisted when their operand is invariant.

        A guard is additionally hoisted only when its operand is already
        available at the hoist target, which requires the operand's defining
        block to dominate the target.  The defining block then executes on
        every path into the target, so the value has been assigned before the
        guard runs.

        A loop-carried phi is defined in the loop header, and the header does
        not dominate the pre-header the guard would be hoisted into.  A guard
        on such a phi is therefore never hoisted, even though the phi's type
        is preserved across the back-edge: hoisting it would read the phi's
        register before the first iteration has assigned it.  The guard stays
        in the loop body, where the phi has already been assigned by the time
        it runs.

        An instruction that can raise is hoisted only from a block that
        executes on every iteration of the loop.  Hoisting moves it to the
        preamble, which runs once before the first iteration; hoisting a
        raising instruction from a block that does not execute on every
        iteration would make it execute on iterations that did not previously
        execute it, which can make a program raise where it previously
        completed (ADR-0049).
        """
        if not isinstance(instr, _HOISTABLE_TYPES):
            return False

        if self._instruction_may_raise(instr) and block_id not in every_iteration_ids:
            return False

        if isinstance(instr, MenaiCFGGuardInstr):
            if instr.value.id not in invariant_ids:
                return False

            def_block_id = def_block_ids.get(instr.value.id)
            if def_block_id is None:
                return False

            return def_block_id in dom[hoist_target_id]

        result = getattr(instr, 'result', None)
        if result is None:
            return False

        return result.id in invariant_ids

    def _instruction_may_raise(self, instr: MenaiCFGInstr) -> bool:
        """
        Return True if an instruction can raise a runtime error.

        A guard raises on a type mismatch.  A call or apply can raise
        unconditionally: it can reach `error`, or recurse into a function that
        raises.  A builtin raises when the registry classifies it as raising.
        The remaining hoistable instructions (constants, and the container and
        closure constructors) are total.
        """
        if isinstance(instr, MenaiCFGGuardInstr):
            return True

        if isinstance(instr, (MenaiCFGCallInstr, MenaiCFGApplyInstr)):
            return True

        if isinstance(instr, MenaiCFGBuiltinInstr):
            return MenaiBuiltinRegistry.builtin_may_raise(instr.op)

        return False

    def _build_def_map(
        self, func: MenaiCFGFunction,
    ) -> dict[int, MenaiCFGInstr]:
        """Map SSA value id -> defining instruction for all instructions in func."""
        result: dict[int, MenaiCFGInstr] = {}
        for block in func.blocks:
            for instr in block.instrs:
                r = getattr(instr, 'result', None)
                if r is not None:
                    result[r.id] = instr

        return result

    @staticmethod
    def _guards_in_block(
        func: MenaiCFGFunction, block_id: int,
    ) -> set[tuple[int, str]]:
        """Return the (value id, expected type) pairs of a block's guards."""
        result: set[tuple[int, str]] = set()
        for block in func.blocks:
            if block.id != block_id:
                continue

            for instr in block.instrs:
                if isinstance(instr, MenaiCFGGuardInstr):
                    result.add((instr.value.id, instr.expected_type))

        return result

    def _rewritable_phi_initial(
        self,
        value_id: int,
        expected_type: str,
        back_edge_types: dict[int, str | None],
        def_map: dict[int, MenaiCFGInstr],
        back_edge_block_ids: set[int],
    ) -> MenaiCFGValue | None:
        """
        Return the value a guard on a loop-carried value should be rewritten
        to check, or None when the guard cannot be rewritten.

        A loop-carried value's first-iteration value is its initial incoming
        value; every later iteration receives the back-edge value.  A guard is
        rewritable when the back-edge value already has the guarded type, so
        only the first iteration needs checking, and when the initial value is
        available in the preamble.

        Two shapes are handled:

          - A phi in the loop header.  Its initial value is the incoming value
            from a non-back-edge predecessor, and there must be exactly one so
            the preamble dominates it.
          - A function param reassigned by a function-level self-loop.  The
            param is defined in the entry block, which becomes the preamble,
            so the guard can simply stay on the param.

        A phi whose back-edge incoming value is the phi itself never changes,
        so its initial value is its value on every iteration.
        """
        phi = def_map.get(value_id)
        if isinstance(phi, MenaiCFGParamInstr):
            if back_edge_types.get(value_id) != expected_type:
                return None

            # A param's initial value is the param itself, so rewriting the
            # guard would produce the same guard on the same value.  That is a
            # no-op, not a rewrite: report it as unrewritable so the guard is
            # left where it is.
            return None

        if not isinstance(phi, MenaiCFGPhiInstr):
            return None

        initial = [
            value for value, pred_id in phi.incoming
            if pred_id not in back_edge_block_ids
        ]
        if len(initial) != 1:
            return None

        back_edge_incoming = [
            value for value, pred_id in phi.incoming
            if pred_id in back_edge_block_ids
        ]

        # A phi that passes itself across the back-edge never changes, so its
        # initial value is its value on every iteration.
        if back_edge_incoming and all(
            value.id == value_id for value in back_edge_incoming
        ):
            return initial[0]

        if back_edge_types.get(value_id) != expected_type:
            return None

        return initial[0]

    def _rewritten_value_ids(
        self,
        func: MenaiCFGFunction,
        self_loops: list[MenaiCFGSelfLoopTerm],
        back_edge_types: dict[int, str | None],
        def_map: dict[int, MenaiCFGInstr],
        loop_block_ids: set[int],
    ) -> set[int]:
        """Return the ids of loop-carried values whose guard the preamble covers."""
        back_edge_block_ids = self._self_loop_block_ids(func, self_loops)
        result: set[int] = set()
        for block in func.blocks:
            if block.id not in loop_block_ids:
                continue

            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGGuardInstr):
                    continue

                if self._rewritable_phi_initial(
                    instr.value.id, instr.expected_type,
                    back_edge_types, def_map, back_edge_block_ids,
                ) is not None:
                    result.add(instr.value.id)

        return result

    def _rewrite_phi_guards(
        self,
        func: MenaiCFGFunction,
        self_loops: list[MenaiCFGSelfLoopTerm],
        back_edge_types: dict[int, str | None],
        def_map: dict[int, MenaiCFGInstr],
        loop_block_ids: set[int],
    ) -> list[MenaiCFGGuardInstr]:
        """
        Return guards to place in the preamble in place of guards on
        loop-carried phis.

        A guard on a loop-carried phi cannot be hoisted: the phi is assigned
        in the loop header, so any block the guard could move to precedes the
        assignment and would read the phi's register before it is written.

        The guard is redundant instead, and can be eliminated.  A phi's value
        is the initial incoming value on the first iteration and the back-edge
        value on every later one.  When the back-edge value's type matches the
        guard's expected type, every iteration after the first satisfies the
        guard by construction, so only the first iteration needs checking.
        Guarding the phi's initial incoming value in the preamble checks
        exactly that, once, and the guard on the phi itself is dropped.

        Returns the guards to append to the preamble, in no particular order.
        The caller removes the original guards from their blocks.
        """
        back_edge_block_ids = self._self_loop_block_ids(func, self_loops)
        result: list[MenaiCFGGuardInstr] = []
        seen: set[tuple[int, str]] = set()

        for block in func.blocks:
            if block.id not in loop_block_ids:
                continue

            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGGuardInstr):
                    continue

                initial = self._rewritable_phi_initial(
                    instr.value.id, instr.expected_type,
                    back_edge_types, def_map, back_edge_block_ids,
                )
                if initial is None:
                    continue

                key = (initial.id, instr.expected_type)
                if key in seen:
                    continue

                seen.add(key)
                result.append(MenaiCFGGuardInstr(
                    value=initial,
                    expected_type=instr.expected_type,
                ))

        return result

    def _defining_block_ids(
        self, func: MenaiCFGFunction,
    ) -> dict[int, int]:
        """
        Map SSA value id -> the id of the block that defines it.

        Used to check that a value is available at a hoist point: a guard may
        be hoisted only to a block dominated by its operand's defining block.
        """
        result: dict[int, int] = {}
        for block in func.blocks:
            for instr in block.instrs:
                r = getattr(instr, 'result', None)
                if r is not None:
                    result[r.id] = block.id

        return result

    def _back_edge_types(
        self,
        self_loops: list[MenaiCFGSelfLoopTerm],
        param_ids: dict[int, int],
        def_map: dict[int, MenaiCFGInstr],
    ) -> dict[int, str | None]:
        """
        Map loop-carried SSA value id -> type at the self-loop back-edge.

        For each loop-carried value, the back-edge type is the type of the
        corresponding SelfLoopTerm arg (the new value assigned to it on the
        back-edge).  When a loop has several back-edges, the value's type is
        the join of the args from all of them: the common type when every
        back-edge agrees, otherwise unknown (None).  The arg's type is
        determined by examining the instruction that defines its SSA value.

        Which values a back-edge carries depends on how the loop was lowered.
        A function-level self-loop (a tail-recursive function) reassigns the
        function's params, so the carried values are the params.  A
        MenaiIRLoop (`param_vals` is not None) reassigns the loop's phi
        values instead, so the carried values are those phis and the function's
        params are never touched.  Keying the result by the carried value in
        both cases is what lets the guard-invariance check in
        `_compute_invariant_values` find the back-edge type of a loop-carried
        phi: keying by param would leave every phi unlisted, and the guard on
        it would be treated as loop-variant and re-executed on every iteration.
        """
        result: dict[int, str | None] = {}
        for index, carried_id in self._carried_values(self_loops, param_ids).items():
            types: list[str | None] = []
            arity_ok = True
            for self_loop in self_loops:
                if index >= len(self_loop.args):
                    arity_ok = False
                    break

                types.append(self._type_of_value(self_loop.args[index], def_map))

            if not arity_ok or not types:
                result[carried_id] = None
                continue

            first = types[0]
            agrees = first is not None and all(t == first for t in types[1:])
            result[carried_id] = first if agrees else None

        return result

    def _carried_values(
        self,
        self_loops: list[MenaiCFGSelfLoopTerm],
        param_ids: dict[int, int],
    ) -> dict[int, int]:
        """
        Map back-edge arg index -> the SSA value id that index updates.

        A function-level self-loop updates the function's params, so the
        carried values are the params.  A MenaiIRLoop back-edge carries
        `param_vals` instead: `args[i]` is the new value of `param_vals[i]`,
        and the function's params are not reassigned by it.

        All self-loops of one loop pass the same number of args, so the first
        self-loop's shape is representative.
        """
        if self_loops[0].param_vals is not None:
            return {
                index: value.id
                for index, value in enumerate(self_loops[0].param_vals)
            }

        return dict(param_ids)

    def _type_of_value(
        self, value: MenaiCFGValue, def_map: dict[int, MenaiCFGInstr],
    ) -> str | None:
        """
        Determine the Menai type name of an SSA value by examining its
        defining instruction.  Returns None if the type is not statically known.
        """
        instr = def_map.get(value.id)
        if instr is None:
            return None

        if isinstance(instr, MenaiCFGConstInstr):
            return _value_type(instr.value)

        if isinstance(instr, MenaiCFGGuardInstr):
            return instr.expected_type

        if isinstance(instr, MenaiCFGBuiltinInstr):
            sig = BUILTIN_TYPE_SIGNATURES.get(instr.op)
            if sig is not None:
                return sig[1]

            return None

        if isinstance(instr, MenaiCFGMakeListInstr):
            return 'list'

        if isinstance(instr, MenaiCFGMakeVectorInstr):
            return 'vector'

        if isinstance(instr, MenaiCFGMakeSetInstr):
            return 'set'

        if isinstance(instr, MenaiCFGMakeDictInstr):
            return 'dict'

        if isinstance(instr, MenaiCFGMakeStructInstr):
            return 'struct'

        # Parameters, free vars, globals, calls, apply, closures: unknown.
        return None

    def _next_block_id(self, func: MenaiCFGFunction) -> int:
        """Return the next available block id in func."""
        return max(b.id for b in func.blocks) + 1

    def _find_loops(
        self, func: MenaiCFGFunction,
    ) -> list[list[MenaiCFGSelfLoopTerm]]:
        """
        Return the function's loops, each as the list of its back-edges.

        A tail-recursive loop can have more than one back-edge: when both
        arms of a branch in the body tail-call the enclosing function, each
        arm gets its own SelfLoopTerm, and all of them share the loop's
        header.  Self-loops are therefore grouped by the header they target.

        A function may contain several loops (nested, sequential, or both),
        so each group is a distinct loop rather than evidence of an
        unhoistable shape.  Groups are returned innermost first so that a
        nested loop is optimised before the loop that encloses it.
        """
        groups: dict[int, list[MenaiCFGSelfLoopTerm]] = {}
        headers: dict[int, MenaiCFGBlock] = {}
        by_id = blocks_by_id(func)
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSelfLoopTerm):
                header = self._self_loop_header(func, block.terminator, by_id)
                groups.setdefault(header.id, []).append(block.terminator)
                headers[header.id] = header

        # Order innermost first: a loop's nesting depth is the number of other
        # loop regions that contain its header.  Optimising a nested loop only
        # moves instructions into its own pre-header, which lies inside the
        # enclosing loop, so it cannot invalidate an enclosing loop's region.
        #
        # Each header's region is computed once here, not once per
        # (header, other_header) pair inside `depth`: the region depends only
        # on the loop, so recomputing it in the inner loop would make the
        # depth computation quadratic in the number of loops.
        regions: dict[int, set[int]] = {
            header_id: self._loop_region(func, header, groups[header_id])
            for header_id, header in headers.items()
        }

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
        by_id: dict[int, MenaiCFGBlock] | None = None,
    ) -> MenaiCFGBlock:
        """
        Return the header a self-loop targets (the entry block when unset).

        `by_id` is the function's block map, passed in so that a caller that
        resolves several self-loops does not rebuild it per call.
        """
        if self_loop.target is None:
            return func.entry()

        if by_id is None:
            by_id = blocks_by_id(func)

        return by_id[self_loop.target]

    def _loop_region(
        self,
        func: MenaiCFGFunction,
        header: MenaiCFGBlock,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> set[int]:
        """
        Return the ids of the blocks that make up the loop.

        The region is the header plus every block that can reach a back-edge
        block without passing through the header, walking predecessors.  The
        back-edge blocks are the blocks terminated by the loop's SelfLoopTerms.

        This is deliberately not a forward reachability walk from the header,
        and not a "blocks dominated by the header" set: both over-include.  A
        loop nested inside another has its header reachable from the outer
        loop's later blocks (via the outer back-edge and the next outer
        iteration), so a forward walk pulls the whole enclosing loop in.  A
        loop at the top of a function (e.g. an inlined `skip-ws` loop) has its
        header on the path from the entry to everything after it, so the
        header dominates the rest of the function even though that code is not
        in the loop.  The back-edge walk stops at the header, so it reaches
        neither an enclosing loop nor the code that follows the loop.
        """
        by_id = blocks_by_id(func)
        region: set[int] = {header.id}
        stack = list(self._self_loop_block_ids(func, self_loops))
        while stack:
            block_id = stack.pop()
            if block_id in region:
                continue

            region.add(block_id)
            for pred in self._predecessor_ids(by_id, block_id):
                if pred not in region:
                    stack.append(pred)

        return region

    def _blocks_on_every_iteration(
        self,
        func: MenaiCFGFunction,
        header: MenaiCFGBlock,
        self_loops: list[MenaiCFGSelfLoopTerm],
        region: set[int],
    ) -> set[int]:
        """
        Return the ids of the blocks that execute on every iteration of the loop.

        A block executes on every iteration exactly when it dominates every
        back-edge block: every path from the loop header to any back-edge then
        passes through it, so no iteration can reach the back-edge without
        executing it.

        This is the condition under which a *raising* instruction may be
        hoisted.  Hoisting moves the instruction to the preamble, which runs
        once before the first iteration; if the instruction executes on every
        iteration it already ran on the first, so hoisting cannot introduce a
        raise on an iteration that did not previously execute it (ADR-0049).

        Dominators are computed over the loop region only, with the header as
        the root.  Edges entering the region from outside (the header's
        pre-header, or a block in an enclosing loop) are ignored: the question
        is which blocks every *iteration* passes through, not which blocks
        every execution of the enclosing function passes through.
        """
        back_edge_ids = self._self_loop_block_ids(func, self_loops)
        if not back_edge_ids:
            return set()

        by_id = blocks_by_id(func)
        region_preds: dict[int, list[int]] = {}
        for block_id in region:
            region_preds[block_id] = [
                pred for pred in self._predecessor_ids(by_id, block_id)
                if pred in region
            ]

        dom = compute_dominators_in_subgraph(region, header.id, region_preds)

        # A block executes on every iteration when it dominates every back-edge
        # block.  A back-edge block that is not in the region cannot be
        # dominated by anything in it, so the whole set is then empty.
        if not back_edge_ids <= region:
            return set()

        result = set(region)
        for back_edge_id in back_edge_ids:
            result &= dom[back_edge_id]

        return result

    def _predecessor_ids(
        self,
        by_id: dict[int, MenaiCFGBlock],
        block_id: int,
    ) -> list[int]:
        """
        Return the ids of the blocks that can transfer control to `block_id`.

        The reverse of `_successor_ids`: a block is a predecessor when one of
        its successors is `block_id`.  A SelfLoopTerm with a target contributes
        that target as a successor (the back-edge), so the loop header is seen
        as a predecessor of its own back-edge block.
        """
        return [
            other.id for other in by_id.values()
            if block_id in self._successor_ids(other)
        ]

    def _self_loop_block_ids(
        self,
        func: MenaiCFGFunction,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> set[int]:
        """Return the ids of the blocks whose terminator is one of `self_loops`."""
        wanted = {id(self_loop) for self_loop in self_loops}
        return {
            block.id for block in func.blocks
            if id(block.terminator) in wanted
        }

    def _retarget_phi_incoming(
        self,
        block: MenaiCFGBlock,
        old_pred_id: int,
        new_pred_id: int,
    ) -> MenaiCFGBlock:
        """
        Rewrite phi incoming edges that name `old_pred_id` to `new_pred_id`.

        Used when a block is split: the split block's outgoing edges move to
        the new block, so a successor's phi must name the new block as the
        incoming predecessor.
        """
        changed = False
        new_instrs: list[MenaiCFGInstr] = []
        for instr in block.instrs:
            if not isinstance(instr, MenaiCFGPhiInstr):
                new_instrs.append(instr)
                continue

            if not any(pred == old_pred_id for _, pred in instr.incoming):
                new_instrs.append(instr)
                continue

            new_incoming = tuple(
                (value, new_pred_id if pred == old_pred_id else pred)
                for value, pred in instr.incoming
            )
            new_instrs.append(
                MenaiCFGPhiInstr(result=instr.result, incoming=new_incoming)
            )
            changed = True

        if not changed:
            return block

        return replace(block, instrs=tuple(new_instrs))

    def _successor_ids(self, block: MenaiCFGBlock) -> list[int]:
        """Return the ids of the blocks a block's terminator can transfer to."""
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

        if isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
            return [term.target]

        return []

    def _pre_header(
        self,
        func: MenaiCFGFunction,
        header: MenaiCFGBlock,
        self_loops: list[MenaiCFGSelfLoopTerm],
    ) -> MenaiCFGBlock | None:
        """
        Return the loop header's pre-header block, or None if there is not
        exactly one that dominates the header.

        The pre-header is the unique predecessor of the header that is not a
        back-edge block (a block whose terminator is one of the loop's
        self-loops).  For a MenaiIRLoop the header has exactly one such
        predecessor: the block that computes the loop's initial values.

        Being the only non-back-edge predecessor is not on its own enough to
        make a block a valid hoisting target.  Hoisting an instruction into a
        block moves it to a point where it executes on every path through that
        block, so the block must dominate the header: otherwise the hoisted
        instruction executes on paths that never reached the loop, and a guard
        hoisted there can read a value the loop header defines (its phis) on a
        path where that value has not been assigned yet.  A header reached from
        only one non-back-edge predecessor can still fail this: when the
        predecessor is one arm of a branch whose other arm raises or returns,
        it does not dominate the header, and hoisting into it would run the
        instruction on a path that never entered the loop.

        A block with several successors also cannot host a hoisted instruction:
        the instruction would execute on every path leaving that block,
        including paths that branch away from the loop.
        """
        back_edge_ids = {
            block.id for block in func.blocks
            if any(block.terminator is self_loop for self_loop in self_loops)
        }

        candidates = [
            pred for pred in predecessors(func, header)
            if pred.id not in back_edge_ids
        ]

        if len(candidates) != 1:
            return None

        candidate = candidates[0]
        if len(self._successor_ids(candidate)) != 1:
            return None

        # The candidate must dominate the header: every path from the function
        # entry to the header must pass through it.
        dom = compute_dominators(func)
        if candidate.id not in dom[header.id]:
            return None

        return candidate

    def _param_ids_by_index(
        self, entry: MenaiCFGBlock,
    ) -> dict[int, int]:
        """Map param index -> SSA value id, from ParamInstr in the entry block."""
        result: dict[int, int] = {}
        for instr in entry.instrs:
            if isinstance(instr, MenaiCFGParamInstr):
                result[instr.index] = instr.result.id

        return result

    def _unchanged_param_ids(
        self,
        self_loops: list[MenaiCFGSelfLoopTerm],
        param_ids: dict[int, int],
    ) -> set[int]:
        """
        Return the set of SSA value ids for params that are not reassigned
        by any self-loop.  These params are loop-invariant, just like free
        vars.  A param is unchanged in either of two ways:

          - Its index is beyond the length of the self-loop's args, so the
            back-edge does not mention it.
          - Every back-edge passes the param itself as that arg, so the
            back-edge assigns it its own value.

        All self-loops of one loop pass the same number of args, so the first
        self-loop's arity is representative.

        A MenaiIRLoop back-edge (`param_vals` is not None) updates the
        loop-carried phi values rather than the function's params, so none of
        the function's params are reassigned by it and all of them are
        unchanged.
        """
        if self_loops[0].param_vals is not None:
            return set(param_ids.values())

        n_args = len(self_loops[0].args)
        result: set[int] = set()
        for index, param_id in param_ids.items():
            if index >= n_args:
                result.add(param_id)
                continue

            # The param is unchanged when every back-edge hands it back to
            # itself, which leaves its value the same on every iteration.
            if all(
                self_loop.args[index].id == param_id
                for self_loop in self_loops
            ):
                result.add(param_id)

        return result
