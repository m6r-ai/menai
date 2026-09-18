"""
CFG pass: guard insertion.

Inserts MenaiCFGGuardInstr instructions where a type-specific operation
receives an operand whose type is not statically known.  The operational
opcodes in the C VM rely on guards having already verified types and omit
their own runtime type checks for performance.

This pass consumes the per-value type facts computed by the interprocedural
type analysis pass (stored on MenaiCFGFunction.type_facts).  It does not
compute facts itself.

Algorithm
---------
Guards are scoped by dominance: a guard inserted in a block suppresses
redundant guards in blocks it dominates (those with a single chain of
single-predecessor blocks from it), but not in sibling blocks reached via
alternative branches.

For each block, the incoming type knowledge is determined by its predecessors:

  - A block with exactly one predecessor inherits that predecessor's outgoing
    types (which include guards inserted there).  This is sound because the
    single predecessor is the only way to reach the block, so any guard in it
    must have executed.
  - A block with zero predecessors starts from the analysis facts.
  - A block with multiple predecessors starts from the intersection of all
    predecessors' outgoing types: a value is known to have type T at the join
    point only if every predecessor's outgoing types agree on T.  This is sound
    because regardless of which predecessor delivered control, the type was
    established.  Values not present in any predecessor's outgoing types fall
    back to the analysis facts.

Within a single block, guards still suppress redundant guards for later
instructions in that block.

Type refinement through branch conditions: when a branch tests the result of a
type predicate (e.g. `integer?`), the true-edge successor inherits the refined
type of the predicate's argument.  This allows guards to be skipped when a
prior type predicate has already established the type at runtime.

The pass mutates the CFG in place — it inserts MenaiCFGGuardInstr instructions
into block.instrs lists and returns the same MenaiCFGFunction.
"""

from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGInstr,
    MenaiCFGFunction,
    MenaiCFGGuardInstr,
    MenaiCFGSwitchTerm,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGPerFunctionPass

# Map from type-predicate builtin name to the Menai type name it tests for.
# When a branch condition is the result of one of these predicates, the
# true-edge successor inherits the refined type of the predicate's argument.
_TYPE_PREDICATES: dict[str, str] = {
    'none?': 'none',
    'boolean?': 'boolean',
    'integer?': 'integer',
    'float?': 'float',
    'complex?': 'complex',
    'string?': 'string',
    'bytes?': 'bytes',
    'list?': 'list',
    'dict?': 'dict',
    'set?': 'set',
    'vector?': 'vector',
    'symbol?': 'symbol',
    'function?': 'function',
    'struct?': 'struct',
    'structtype?': 'structtype',
}


class MenaiCFGGuardInsertion(MenaiCFGPerFunctionPass):
    """
    CFG optimization pass that inserts runtime type guards.

    See module docstring for the algorithm description.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """Insert guards where type-specific operations receive unknown-typed operands."""
        return func, self._insert_guards(func)

    def _insert_guards(self, func: MenaiCFGFunction) -> bool:
        """
        Insert guard instructions where type-specific builtins receive
        operands of unknown type.  See the module docstring for the
        dominance scoping rules.

        Returns True if any guards were inserted.
        """
        changed = False
        types: dict[int, str | None] = {
            val_id: fact.kind for val_id, fact in func.type_facts.items()
        }

        outgoing_types: dict[int, dict[int, str | None]] = {}
        branch_true_types: dict[int, dict[int, str | None]] = {}

        for block in func.blocks:
            preds = block.predecessors
            if len(preds) == 1:
                pred = preds[0]
                term = pred.terminator
                if (
                    pred.id in branch_true_types
                    and isinstance(term, MenaiCFGBranchTerm)
                    and block.id == term.true_block.id
                ):
                    block_types = dict(branch_true_types[pred.id])

                else:
                    block_types = dict(outgoing_types.get(pred.id, types))

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
            self._guard_switch(block, block_types, new_instrs)

            self._refine_branch_types(block, block_types, branch_true_types)

            outgoing_types[block.id] = block_types

            if len(new_instrs) != len(block.instrs):
                block.instrs = new_instrs
                changed = True

        return changed

    @staticmethod
    def _meet_outgoing_types(
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
        to the analysis facts.
        """
        result: dict[int, str | None] = {}

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

        for val_id, fact_type in types.items():
            if val_id not in result:
                result[val_id] = fact_type

        return result

    @staticmethod
    def _guard_builtin_args(
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

    @staticmethod
    def _guard_branch(
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

    @staticmethod
    def _guard_switch(
        block: MenaiCFGBlock,
        types: dict[int, str | None],
        new_instrs: list[MenaiCFGInstr],
    ) -> None:
        """
        Insert an integer guard on a switch terminator's scrutinee if its
        type is not statically known to be integer.

        Appends a guard to new_instrs if needed and updates the types dict.
        """
        term = block.terminator
        if not isinstance(term, MenaiCFGSwitchTerm):
            return

        val_type = types.get(term.value.id)
        if val_type == 'integer':
            return

        new_instrs.append(MenaiCFGGuardInstr(
            value=term.value,
            expected_type='integer',
        ))
        types[term.value.id] = 'integer'

    def _refine_branch_types(
        self,
        block: MenaiCFGBlock,
        types: dict[int, str | None],
        branch_true_types: dict[int, dict[int, str | None]],
    ) -> None:
        """
        If the block's terminator is a branch whose condition is the result
        of a type-predicate builtin, record the refined type for the true
        edge in branch_true_types.

        The true-edge successor inherits the predicate's argument type.
        The false edge does not refine the argument's type (the predicate
        returning #f only tells us the value is *not* that type, which is
        not useful for guard suppression).
        """
        term = block.terminator
        if not isinstance(term, MenaiCFGBranchTerm):
            return

        refinement = self._branch_type_refinement(block, term)
        if refinement is None:
            return

        val_id, refined_type = refinement
        true_types = dict(types)
        true_types[val_id] = refined_type
        branch_true_types[block.id] = true_types

    @staticmethod
    def _branch_type_refinement(
        block: MenaiCFGBlock,
        term: MenaiCFGBranchTerm,
    ) -> tuple[int, str] | None:
        """
        Check whether a branch condition is the result of a type-predicate
        builtin call in the same block.  If so, return (arg_value_id, type_name)
        for the predicate's argument.  Otherwise return None.
        """
        for instr in block.instrs:
            if (
                isinstance(instr, MenaiCFGBuiltinInstr)
                and instr.result.id == term.cond.id
                and instr.op in _TYPE_PREDICATES
                and len(instr.args) == 1
            ):
                return instr.args[0].id, _TYPE_PREDICATES[instr.op]

        return None
