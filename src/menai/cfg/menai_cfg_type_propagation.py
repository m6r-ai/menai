"""
CFG pass: type propagation and guard insertion.

Determines the static type of each MenaiCFGValue within a function and inserts
MenaiCFGGuardInstr instructions where a type-specific operation receives an
operand whose type is not statically known.  The operational opcodes in the C VM
rely on guards having already verified types and omit their own runtime type
checks for performance.

Type knowledge sources
----------------------
1. Constants — a MenaiCFGConstInstr's value has a directly readable type.
2. Builtin results — each builtin has a known result type (from
   BUILTIN_TYPE_SIGNATURES in menai_type_signatures.py).
3. Guards — a MenaiCFGGuardInstr asserts a type, so the value is known to
   have that type after the guard executes.
4. Phi joins — if all incoming values share the same known type, the phi
   result has that type; otherwise it is unknown.

Values whose type cannot be determined (parameters, free variables, call
results, apply results) are "unknown".  When an unknown-typed value is used
as an argument to a builtin that expects a specific type, a guard is inserted
before the builtin to check that type at runtime.
Similarly, when a branch terminator's condition has an unknown type, a
boolean guard is inserted before the branch.

Algorithm
---------
The pass works in two phases:

Phase 1 — Forward type propagation.  Walk each block in reverse-post-order.
For each instruction, compute the result type from the instruction's operands
and type signature.  At phi nodes, the type is the meet (join) of all incoming
value types.  Because phi nodes can reference values from later blocks
(back-edges in loops), we iterate to a fixed point.

Phase 2 — Guard insertion.  Walk each block in order.  For each builtin call,
check each argument: if its type is known and matches the expected type, no
guard is needed.  If its type is unknown but the builtin expects a specific
type, insert a guard before the builtin call.  For branch terminators, guard
the condition as a boolean.  After a guard, the value's type becomes known for
subsequent uses, so multiple builtins (or branches) using the same
unknown-typed value only need one guard.

The pass mutates the CFG in place — it inserts MenaiCFGGuardInstr instructions
into block.instrs lists and returns the same MenaiCFGFunction.

Phase 3 — Loop-invariant guard hoisting.  When a function contains a
SelfLoopTerm, loop-invariant guards from any block in the function are
hoisted into a preamble block.  A guard is loop-invariant if it guards a
free var (which never changes) or a param whose type is preserved through
the loop body (the back-edge type matches the guard's expected type).
The SelfLoopTerm's target is set to the loop-entry block (the original entry
minus the hoisted guards), so the self-loop skips the preamble on every
iteration after the first.
"""

from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.cfg.menai_cfg import (
    MenaiCFGApplyInstr,
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGlobalInstr,
    MenaiCFGGuardInstr,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGParamInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGPhiInstr,
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
)

# Map from Python value class to Menai type name string.
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


class MenaiCFGTypePropagation(MenaiCFGOptimizationPass):
    """
    CFG optimization pass that propagates types and inserts guards.

    See module docstring for the algorithm description.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """Run type propagation and guard insertion on a single function."""
        # Phase 1: forward type propagation to fixed point.
        types = self._propagate_types(func)

        # Phase 2: insert guards where needed.
        changed = self._insert_guards(func, types)

        # Phase 3: hoist loop-invariant guards out of self-loops.
        changed = self._hoist_loop_invariant_guards(func, types) or changed

        return func, changed

    def _propagate_types(self, func: MenaiCFGFunction) -> dict[int, str | None]:
        """
        Compute the known type of every MenaiCFGValue in the function.

        Returns a dict mapping value id → type name (or None for unknown).
        Iterates to a fixed point to handle phi nodes with back-edge inputs.
        """
        types: dict[int, str | None] = {}

        # Iterate to fixed point.
        while True:
            changed = False
            for block in func.blocks:
                for instr in block.instrs:
                    if isinstance(instr, (MenaiCFGConstInstr, MenaiCFGGlobalInstr,
                                          MenaiCFGParamInstr, MenaiCFGFreeVarInstr,
                                          MenaiCFGBuiltinInstr, MenaiCFGCallInstr,
                                          MenaiCFGApplyInstr, MenaiCFGMakeClosureInstr,
                                          MenaiCFGMakeStructInstr, MenaiCFGMakeListInstr,
                                          MenaiCFGMakeSetInstr, MenaiCFGMakeDictInstr,
                                          MenaiCFGPhiInstr)):
                        new_type = self._instr_type(instr, types)
                        old_type = types.get(instr.result.id)
                        if new_type != old_type:
                            types[instr.result.id] = new_type
                            changed = True

            if not changed:
                break

        return types

    def _instr_type(self, instr: object, types: dict[int, str | None]) -> str | None:
        """Determine the result type of an instruction given current type knowledge."""
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

        if isinstance(instr, MenaiCFGMakeSetInstr):
            return 'set'

        if isinstance(instr, MenaiCFGMakeDictInstr):
            return 'dict'

        if isinstance(instr, MenaiCFGMakeStructInstr):
            return 'struct'

        if isinstance(instr, MenaiCFGPhiInstr):
            return self._phi_type(instr, types)

        # Parameters, free vars, globals, calls, apply, closures: unknown.
        return None

    def _phi_type(self, instr: MenaiCFGPhiInstr, types: dict[int, str | None]) -> str | None:
        """Compute the type of a phi node from its incoming values."""
        result_type: str | None = None
        for incoming_val, _ in instr.incoming:
            incoming_type = types.get(incoming_val.id)
            if incoming_type is None:
                return None

            if result_type is None:
                result_type = incoming_type

            elif result_type != incoming_type:
                return None

        return result_type

    def _insert_guards(
        self,
        func: MenaiCFGFunction,
        types: dict[int, str | None],
    ) -> bool:
        """
        Insert guard instructions where type-specific builtins receive
        operands of unknown type.  Guards are scoped by dominance: a guard
        inserted in a block suppresses redundant guards in blocks it
        dominates (those with a single chain of single-predecessor blocks
        from it), but not in sibling blocks reached via alternative
        branches.

        For each block, the incoming type knowledge is determined by its
        predecessors:
          - A block with exactly one predecessor inherits that
            predecessor's outgoing types (which include guards inserted
            there).  This is sound because the single predecessor is the
            only way to reach the block, so any guard in it must have
            executed.
          - A block with zero predecessors starts from the Phase 1
            propagated types.
          - A block with multiple predecessors starts from the
            intersection of all predecessors' outgoing types: a value
            is known to have type T at the join point only if every
            predecessor's outgoing types agree on T.  This is sound
            because regardless of which predecessor delivered control,
            the type was established.  Values not present in any
            predecessor's outgoing types fall back to the Phase 1
            propagated types.

        Within a single block, guards still suppress redundant guards for
        later instructions in that block.

        Returns True if any guards were inserted.
        """
        changed = False

        outgoing_types: dict[int, dict[int, str | None]] = {}

        for block in func.blocks:
            preds = block.predecessors
            if len(preds) == 1:
                block_types = dict(outgoing_types.get(preds[0].id, types))

            elif len(preds) > 1:
                block_types = self._meet_outgoing_types(preds, outgoing_types, types)

            else:
                block_types = dict(types)

            new_instrs: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGBuiltinInstr):
                    self._guard_builtin_args(instr, block_types, new_instrs)

                new_instrs.append(instr)

            self._guard_branch(block, block_types, new_instrs)

            outgoing_types[block.id] = block_types

            if len(new_instrs) != len(block.instrs):
                block.instrs = new_instrs
                changed = True

        return changed

    def _meet_outgoing_types(
        self,
        preds: list[MenaiCFGBlock],
        outgoing_types: dict[int, dict[int, str | None]],
        types: dict[int, str | None],
    ) -> dict[int, str | None]:
        """
        Compute the incoming type knowledge at a join point (block with
        multiple predecessors) by intersecting all predecessors' outgoing
        types.

        A value is known to have type T at the join point only if every
        predecessor's outgoing types agree on T.  If any predecessor has
        a different type, or has no outgoing type for that value (meaning
        the value is unknown on that path), the type is unknown at the
        join point.

        Values not present in any predecessor's outgoing types fall back
        to the Phase 1 propagated types.
        """
        result: dict[int, str | None] = {}

        # Collect all value ids that appear in any predecessor's outgoing types.
        all_ids: set[int] = set()
        for pred in preds:
            all_ids.update(outgoing_types.get(pred.id, {}).keys())

        for val_id in all_ids:
            agreed_type: str | None = None
            for pred in preds:
                pred_type = outgoing_types.get(pred.id, {}).get(val_id)
                if pred_type is None:
                    agreed_type = None
                    break

                if agreed_type is None:
                    agreed_type = pred_type

                elif agreed_type != pred_type:
                    agreed_type = None
                    break

            result[val_id] = agreed_type

        # Values not in any predecessor's outgoing types fall back to Phase 1 types.
        for val_id, phase1_type in types.items():
            if val_id not in result:
                result[val_id] = phase1_type

        return result

    def _guard_builtin_args(
        self,
        instr: MenaiCFGBuiltinInstr,
        types: dict[int, str | None],
        new_instrs: list[MenaiCFGInstr],
    ) -> None:
        """
        Check the arguments of a builtin call and insert guards for any
        argument whose type is unknown but whose expected type is specific.

        Appends guard instructions to new_instrs as needed and updates the
        types dict so that later uses see the guarded type.
        """
        sig = BUILTIN_TYPE_SIGNATURES.get(instr.op)
        if sig is None:
            return

        arg_types, _ = sig
        for i, arg in enumerate(instr.args):
            if i >= len(arg_types):
                break

            expected = arg_types[i]
            if expected is None or expected == 'any':
                continue

            known = types.get(arg.id)
            if known == expected:
                continue

            new_instrs.append(MenaiCFGGuardInstr(
                value=arg,
                expected_type=expected,
            ))
            types[arg.id] = expected

    def _guard_branch(
        self,
        block: MenaiCFGBlock,
        types: dict[int, str | None],
        new_instrs: list[MenaiCFGInstr],
    ) -> None:
        """
        Insert a boolean guard on a branch terminator's condition if its
        type is not statically known to be boolean.

        Appends a guard to new_instrs if needed and updates the types dict.
        """
        term = block.terminator
        if not isinstance(term, MenaiCFGBranchTerm):
            return

        cond_type = types.get(term.cond.id)
        if cond_type == 'boolean':
            return

        new_instrs.append(MenaiCFGGuardInstr(
            value=term.cond,
            expected_type='boolean',
        ))
        types[term.cond.id] = 'boolean'

    def _hoist_loop_invariant_guards(
        self,
        func: MenaiCFGFunction,
        types: dict[int, str | None],
    ) -> bool:
        """
        Hoist loop-invariant guards from all blocks into a preamble
        block so they execute once on function entry rather than on every
        self-loop iteration.

        A guard in any block is loop-invariant if it guards a value
        whose type is known to be preserved across the self-loop back-edge:

          - Free var guards: always loop-invariant (free vars are never
            reassigned).
          - Param guards: loop-invariant when the type of the corresponding
            SelfLoopTerm arg (the new value assigned to that param) matches
            the guard's expected type.  The arg's type comes from the Phase 1
            types dict.

        When loop-invariant guards are found, the entry block is split:
          - Preamble: ParamInstr/FreeVarInstr instructions plus the
            loop-invariant guards, terminated by a JumpTerm to the loop-entry.
          - Loop-entry: remaining instructions (including non-loop-invariant
            guards), with the original terminator.

        The SelfLoopTerm's target is set to the loop-entry block.
        Guards hoisted from non-entry blocks are removed from those blocks.

        Returns True if the entry block was split.
        """
        self_loop = self._find_self_loop(func)
        if self_loop is None:
            return False

        entry = func.entry()
        param_ids = self._param_ids_by_index(entry)
        free_var_ids = self._free_var_ids(entry)

        # Params not reassigned by the self-loop (their index is beyond
        # the self-loop args) are loop-invariant, just like free vars.
        unchanged_param_ids = self._unchanged_param_ids(self_loop, param_ids)

        back_edge_types = self._back_edge_types(self_loop, param_ids, types)

        preamble_instrs: list[MenaiCFGInstr] = []

        for block in func.blocks:
            remaining: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGGuardInstr):
                    if self._is_loop_invariant_guard(
                        instr, free_var_ids, unchanged_param_ids, back_edge_types,
                    ):
                        preamble_instrs.append(instr)
                        continue

                remaining.append(instr)

            block.instrs = remaining

        if not preamble_instrs:
            return False

        # The preamble needs the ParamInstr/FreeVarInstr that
        # define the SSA values the guards reference.  These are at
        # the top of the original entry block, before any guards.
        def_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        loop_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if not isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        # Preamble: param/free-var definitions, then hoisted guards.
        entry.instrs = def_instrs + preamble_instrs
        loop_entry = MenaiCFGBlock(
            id=self._next_block_id(func),
            label="loop_entry",
            instrs=loop_instrs,
            terminator=entry.terminator,
        )

        entry.terminator = MenaiCFGJumpTerm(target=loop_entry)

        func.blocks.append(loop_entry)

        self_loop.target = loop_entry

        return True

    def _next_block_id(self, func: MenaiCFGFunction) -> int:
        """Return the next available block id in func."""
        return max(b.id for b in func.blocks) + 1

    def _find_self_loop(
        self, func: MenaiCFGFunction,
    ) -> MenaiCFGSelfLoopTerm | None:
        """Return the SelfLoopTerm in func, or None if there is none."""
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSelfLoopTerm):
                return block.terminator

        return None

    def _param_ids_by_index(
        self, entry: MenaiCFGBlock,
    ) -> dict[int, int]:
        """Map param index → SSA value id, from ParamInstr in the entry block."""
        result: dict[int, int] = {}
        for instr in entry.instrs:
            if isinstance(instr, MenaiCFGParamInstr):
                result[instr.index] = instr.result.id

        return result

    def _free_var_ids(
        self, entry: MenaiCFGBlock,
    ) -> set[int]:
        """Return the set of SSA value ids for free vars in the entry block."""
        result: set[int] = set()
        for instr in entry.instrs:
            if isinstance(instr, MenaiCFGFreeVarInstr):
                result.add(instr.result.id)

        return result

    def _back_edge_types(
        self,
        self_loop: MenaiCFGSelfLoopTerm,
        param_ids: dict[int, int],
        types: dict[int, str | None],
    ) -> dict[int, str | None]:
        """
        Map param SSA value id → type at the self-loop back-edge.

        For each param, the back-edge type is the type of the corresponding
        SelfLoopTerm arg (the new value assigned to that param on the
        back-edge).  The arg's type comes from the Phase 1 types dict.
        """
        result: dict[int, str | None] = {}
        for param_index, arg_val in enumerate(self_loop.args):
            param_id = param_ids.get(param_index)
            if param_id is not None:
                result[param_id] = types.get(arg_val.id)

        return result

    def _unchanged_param_ids(
        self,
        self_loop: MenaiCFGSelfLoopTerm,
        param_ids: dict[int, int],
    ) -> set[int]:
        """
        Return the set of SSA value ids for params that are not reassigned
        by the self-loop (their index is beyond the length of self_loop.args).
        These params are loop-invariant, just like free vars.
        """
        n_args = len(self_loop.args)
        return {
            param_id for index, param_id in param_ids.items()
            if index >= n_args
        }

    def _is_loop_invariant_guard(
        self,
        guard: MenaiCFGGuardInstr,
        free_var_ids: set[int],
        unchanged_param_ids: set[int],
        back_edge_types: dict[int, str | None],
    ) -> bool:
        """
        Return True if a guard is loop-invariant (redundant on all
        self-loop iterations after the first).

        A guard on a free var or an unchanged param is always loop-invariant
        — these values are never reassigned across the self-loop back-edge.

        A guard on a param is loop-invariant when the back-edge type (the
        type of the value assigned to that param by the SelfLoopTerm)
        matches the guard's expected type.
        """
        val_id = guard.value.id

        if val_id in free_var_ids or val_id in unchanged_param_ids:
            return True

        if val_id in back_edge_types:
            return back_edge_types[val_id] == guard.expected_type

        return False
