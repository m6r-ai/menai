"""
CFG pass: common subexpression elimination.

Two instructions compute the same value when they are the same operation
applied to the same operands, and the operation is a pure function of its
operands.  Within a function, the later of two such instructions can be
replaced by the earlier one wherever the earlier one's result is already
available.

Availability is dominance.  The pass walks the dominator tree from the entry
block, maintaining a map from an expression's value-numbering key to the SSA
value that holds it.  A block's map is the map of its dominator, extended by
the expressions the block itself defines.  Because a block is visited only
after the block that dominates it, an entry in the map at the point a block is
visited is a value that is available on every path to that block: the
definition dominates the use.

When an instruction's key is already in the map, its result is redundant.  The
result is recorded as a substitution to the available value, and the
instruction is dropped.  After the walk, every reference to a substituted value
is rewritten to the available value, and prune_dead_definitions sweeps any
instruction left with no uses.

This is a merge, not a hoist: the redundant instruction is removed, but no
instruction is moved to a point where it did not execute before.  The
dominator condition is what makes the merge sound, including for an expression
that can raise: a raising expression is merged only where it already executed
on every path to the merge point, so merging cannot introduce a raise on a path
that did not have one (ADR-0049).

Mergeable instructions
----------------------
An instruction is mergeable when its result is a pure function of its operand
values:

  - builtins (MenaiCFGBuiltinInstr)
  - the container constructors (make_list, make_vector, make_set, make_dict,
    make_struct)
  - struct_get_indexed

Calls, applies, and make_closure are not mergeable: two calls of the same
function are not the same value, and a call is not value-numbered here.  Guards
are not mergeable: a guard is a check, not a computation, and merging two
guards would not remove work.  Phis, params, free vars, and constants define
values that are not expressions over other values.

Menai is pure, so no instruction has a side effect that merging could duplicate
or reorder; the only observable effect an instruction can have beyond its
result is raising, which the dominance condition covers.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGValue,
    blocks_by_id,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    compute_dominators,
    prune_dead_definitions,
    substitute_value_in_function,
)


def _expression_key(instr: MenaiCFGInstr) -> tuple[tuple, MenaiCFGValue] | None:
    """
    Return a (key, result) pair for a mergeable instruction, or None.

    Two instructions with equal keys compute the same value.  The key includes
    the operation and the identity of every operand value, so it is only equal
    for the same operation on the same operands.

    A non-mergeable instruction returns None and is left alone.
    """
    if isinstance(instr, MenaiCFGBuiltinInstr):
        return ("builtin", instr.op, tuple(a.id for a in instr.args)), instr.result

    if isinstance(instr, MenaiCFGMakeListInstr):
        return ("make_list", tuple(a.id for a in instr.args)), instr.result

    if isinstance(instr, MenaiCFGMakeVectorInstr):
        return ("make_vector", tuple(a.id for a in instr.args)), instr.result

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return ("make_set", tuple(a.id for a in instr.args)), instr.result

    if isinstance(instr, MenaiCFGMakeDictInstr):
        return ("make_dict", tuple((k.id, v.id) for k, v in instr.pairs)), instr.result

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return (
            "make_struct",
            instr.struct_type.name,
            tuple(a.id for a in instr.args),
        ), instr.result

    if isinstance(instr, MenaiCFGStructGetIndexedInstr):
        return ("struct_get_indexed", instr.struct.id, instr.index), instr.result

    return None


class MenaiCFGCSE(MenaiCFGPerFunctionPass):
    """
    CFG optimization pass that eliminates redundant computations.

    See the module docstring for the algorithm and its soundness argument.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Eliminate redundant computations in a single function.

        Walks the dominator tree, recording a substitution for each redundant
        instruction, then rewrites the function and prunes the instructions the
        substitutions made dead.
        """
        replacements = self._find_replacements(func)
        if not replacements:
            return func, False

        func = substitute_value_in_function(func, replacements)
        func, _ = prune_dead_definitions(func)
        return func, True

    def _find_replacements(
        self,
        func: MenaiCFGFunction,
    ) -> dict[int, MenaiCFGValue]:
        """
        Return a map from a redundant value id to the value that replaces it.

        The map is built by a dominator-tree walk.  Each block is visited once,
        after the block that dominates it, so an expression already in the map
        at that point is available on every path to the block.
        """
        by_id = blocks_by_id(func)
        dom = compute_dominators(func)

        # The immediate dominator of a block is its dominator (other than
        # itself) with the largest dominator set: every other dominator of the
        # block dominates it, so it is the deepest one.  The entry block has no
        # immediate dominator.
        idom: dict[int, int] = {}
        for block_id in by_id:
            dominators = [d for d in dom[block_id] if d != block_id]
            if not dominators:
                continue

            idom[block_id] = max(dominators, key=lambda d: len(dom[d]))

        children: dict[int, list[int]] = {block_id: [] for block_id in by_id}
        for block_id, parent_id in idom.items():
            children[parent_id].append(block_id)

        replacements: dict[int, MenaiCFGValue] = {}

        # Iterative dominator-tree walk.  Each stack entry carries the block and
        # the available-expression map in scope on entry to it.  A child's map
        # is the parent's map extended by the parent's own definitions, so the
        # map is never mutated in place across siblings.
        stack: list[tuple[int, dict[tuple, MenaiCFGValue]]] = [
            (func.entry().id, {})
        ]
        while stack:
            block_id, available = stack.pop()
            block = by_id[block_id]

            for instr in block.instrs:
                entry = _expression_key(instr)
                if entry is None:
                    continue

                key, result = entry
                existing = available.get(key)
                if existing is not None:
                    replacements[result.id] = existing

                else:
                    available[key] = result

            for child_id in children[block_id]:
                stack.append((child_id, dict(available)))

        return replacements
