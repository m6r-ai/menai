"""
CFG pass: loop-invariant code motion (LICM).

When a function contains a SelfLoopTerm (a tail-recursive loop), instructions
whose operands are all loop-invariant are hoisted into a preamble block that
executes once on function entry.  The SelfLoopTerm's target is set to the
loop-entry block (the original entry minus the hoisted instructions), so the
self-loop skips the preamble on every iteration after the first.

Because Menai is pure — no side effects, no mutation — any instruction whose
operands are all loop-invariant can be hoisted unconditionally.  There is no
need to check for side effects, memory aliasing, or ordering hazards.

Loop-invariant values
---------------------
An SSA value is loop-invariant if it is defined by an instruction whose
operands are all loop-invariant.  The base cases are:

  - Constants (MenaiCFGConstInstr) — always invariant.
  - Globals (MenaiCFGGlobalInstr) — always invariant (globals don't change).
  - Free vars (MenaiCFGFreeVarInstr) — always invariant (captures don't change).
  - Params (MenaiCFGParamInstr) — invariant when the param is not reassigned
    by the self-loop (its index is beyond the length of SelfLoopTerm.args).

Guard-specific invariance
-------------------------
A MenaiCFGGuardInstr on a param that *is* reassigned by the self-loop is still
loop-invariant when the back-edge type (the type of the value assigned to
that param by the SelfLoopTerm) matches the guard's expected type.  In that
case the guard will succeed on every iteration just as it did on the first.

This type-aware guard invariance check requires type information from the
type propagation pass, which is why LICM runs after type propagation.

Block splitting
---------------
When at least one instruction is hoisted, the entry block is split:

  - Preamble: ParamInstr/FreeVarInstr definitions, then hoisted instructions
    in dependency order, terminated by a JumpTerm to the loop-entry block.
  - Loop-entry: remaining instructions (non-hoisted), with the original
    terminator.

The SelfLoopTerm's target is set to the loop-entry block.  The VCode builder
recognises this and emits a label named "__entry__" for the loop-entry block,
so the self-loop jump resolves to the loop-entry rather than the preamble.

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

The pass mutates the CFG in place — it moves instructions between block.instrs
lists and may create a new block — and returns the same MenaiCFGFunction.
"""

from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.cfg.menai_cfg import (
    MenaiCFGApplyInstr,
    MenaiCFGBlock,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGlobalInstr,
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
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    value_ids_in_instr,
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
    MenaiVector,
)

# Instruction types that can be hoisted (produce a result and are pure).
# MenaiCFGPhiInstr is excluded — phi nodes are join points tied to their block.
# MenaiCFGPatchClosureInstr is excluded — it has no result and is a side-effecting
# mutation (closure initialisation), stored in patch_instrs not instrs.
_HOISTABLE_TYPES = (
    MenaiCFGConstInstr,
    MenaiCFGGlobalInstr,
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


class MenaiCFGLICM(MenaiCFGOptimizationPass):
    """
    CFG optimization pass that hoists loop-invariant instructions out of
    self-loops into a preamble block.

    See module docstring for the algorithm description.
    """

    def _optimize_function(
        self, func: MenaiCFGFunction,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Hoist loop-invariant instructions from a self-loop into a preamble."""
        self_loop = self._find_self_loop(func)
        if self_loop is None:
            return func, False

        entry = func.entry()
        param_ids = self._param_ids_by_index(entry)
        unchanged_param_ids = self._unchanged_param_ids(self_loop, param_ids)

        # Build a map from SSA value id to defining instruction for type lookup.
        def_map = self._build_def_map(func)

        # Compute back-edge types for guard invariance checks.
        back_edge_types = self._back_edge_types(self_loop, param_ids, def_map)

        # Compute the set of loop-invariant SSA value ids.
        invariant_ids = self._compute_invariant_values(
            func, unchanged_param_ids, back_edge_types,
        )

        # Collect hoistable instructions from all blocks.
        preamble_instrs: list[MenaiCFGInstr] = []
        seen_guards: set[tuple[int, str]] = set()

        for block in func.blocks:
            remaining: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                if self._is_hoistable(instr, invariant_ids):
                    if isinstance(instr, MenaiCFGGuardInstr):
                        key = (instr.value.id, instr.expected_type)
                        if key in seen_guards:
                            continue

                        seen_guards.add(key)

                    preamble_instrs.append(instr)

                else:
                    remaining.append(instr)

            block.instrs = remaining

        if not preamble_instrs:
            return func, False

        # Split the entry block into preamble + loop-entry.
        def_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        loop_instrs: list[MenaiCFGInstr] = [
            instr for instr in entry.instrs
            if not isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
        ]

        # Preamble: param/free-var definitions, then hoisted instructions.
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

        return func, True

    def _compute_invariant_values(
        self,
        func: MenaiCFGFunction,
        unchanged_param_ids: set[int],
        back_edge_types: dict[int, str | None],
    ) -> set[int]:
        """
        Compute the set of SSA value ids that are loop-invariant.

        An SSA value is loop-invariant if its defining instruction's operands
        are all loop-invariant.  Base cases:
          - Constants, globals, free vars — always invariant.
          - Params not reassigned by the self-loop — invariant.

        Guards on reassigned params are invariant when the back-edge type
        matches the guard's expected type.

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

                elif isinstance(instr, MenaiCFGGlobalInstr):
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

        # Handle guards: a guard is invariant if its operand is invariant
        # (by the general rule above) or if it guards a reassigned param
        # whose back-edge type matches the guard's expected type.
        for block in func.blocks:
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGGuardInstr):
                    continue

                if instr.value.id in invariant:
                    continue

                val_id = instr.value.id
                if val_id in back_edge_types:
                    if back_edge_types[val_id] == instr.expected_type:
                        invariant.add(val_id)

        return invariant

    def _is_hoistable(
        self,
        instr: MenaiCFGInstr,
        invariant_ids: set[int],
    ) -> bool:
        """
        Return True if an instruction should be hoisted into the preamble.

        A hoistable instruction type whose result is loop-invariant is
        hoisted.  Guards are hoisted when their operand is invariant.
        """
        if not isinstance(instr, _HOISTABLE_TYPES):
            return False

        if isinstance(instr, MenaiCFGGuardInstr):
            return instr.value.id in invariant_ids

        result = getattr(instr, 'result', None)
        if result is None:
            return False

        return result.id in invariant_ids

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

    def _back_edge_types(
        self,
        self_loop: MenaiCFGSelfLoopTerm,
        param_ids: dict[int, int],
        def_map: dict[int, MenaiCFGInstr],
    ) -> dict[int, str | None]:
        """
        Map param SSA value id -> type at the self-loop back-edge.

        For each param, the back-edge type is the type of the corresponding
        SelfLoopTerm arg (the new value assigned to that param on the
        back-edge).  The arg's type is determined by examining the
        instruction that defines the arg's SSA value.
        """
        result: dict[int, str | None] = {}
        for param_index, arg_val in enumerate(self_loop.args):
            param_id = param_ids.get(param_index)
            if param_id is not None:
                result[param_id] = self._type_of_value(arg_val, def_map)

        return result

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
        """Map param index -> SSA value id, from ParamInstr in the entry block."""
        result: dict[int, int] = {}
        for instr in entry.instrs:
            if isinstance(instr, MenaiCFGParamInstr):
                result[instr.index] = instr.result.id

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
