"""
CFG pass: fold struct-is-instance? tests whose result is statically known.

A struct destructuring pattern lowers to

    (and ($struct? tmp) ($struct-is-instance? tmp TypeName))

and the struct-is-instance? test is emitted as the STRUCT_IS_INSTANCE_P
opcode, feeding a branch to the match arm's body on the true edge and to the
next arm (or the "No patterns matched" error) on the false edge.

When the interprocedural type analysis proves that the receiver is a struct of
exactly the tested type, the test is statically #t: the branch always takes its
true edge and the test instruction is redundant.  This pass removes the test
and re-wires the branch to the true edge.

This is the type-identity analogue of MenaiCFGPredicateFold.  That pass folds
the general type predicates (none?, integer?, ...) and deliberately excludes
struct-is-instance?, which is an identity test rather than a kind test and
needs struct-type resolution; this pass supplies that resolution.

Soundness
---------
The pass fires only when the analysis proves the receiver's fact is
Known('struct', T) with T *identical* to the struct type the test names.  It
does not fire when the fact is Known('struct', None) (two struct types joined,
identity lost), ANY, BOTTOM, or a non-struct kind: in every such case the value
could be a struct of another type, so the test is load-bearing.

The receiver's fact is read from the block-local facts, exactly as the
analysis's own struct field-access rewrite reads them: a receiver whose type is
proven only by a struct-is-instance? branch refinement has no definition-site
fact, and the refinement exists only in the block-local facts.  Reading the
global per-value facts alone would miss those receivers; reading them without
the block-local merge would let the pass fire on a fact that is not actually
established at the test.

The tested struct type is read from the context, where the analysis recorded
it.  The analysis already resolves a test's structtype argument through the
enclosing lexical scope to refine the receiver on the true edge; this pass
consumes that resolution rather than re-deriving it, so the two cannot drift.

The pass is a pure optimisation: where the type is not proven the test and its
branch are left untouched, so behaviour is never changed.

Shapes handled
--------------
The test instruction and the branch that consumes it need not be in the same
block: the (and ...) desugaring and the block simplifier can leave the test's
result defined in a dominating block and consumed by a later block's branch.
The test is therefore located through the function's value-definition map, not
by scanning the branch's own block.

Both condition shapes the analysis recognises are handled:

  - a branch whose condition is directly the struct-is-instance? result;
  - a branch whose condition is a phi joining a struct-is-instance? result with
    constant #f values, which is the shape an (and ...) guard lowers to.  On
    the true edge only the struct-is-instance? branch can have been taken, so
    the test is known #t there too.

The false edge of a folded branch becomes unreachable.  The now-dead block is
left in place for MenaiCFGSimplifyBlocks (which runs earlier in the pipeline
and is re-run by the pass manager's fixed-point loop on the next sweep) to
remove.  The test instruction and any instruction that only fed it are dead
once the branch is re-wired; prune_dead_definitions sweeps them so the fold
leaves no orphaned instructions behind.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGPhiInstr,
    predecessors,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    prune_dead_definitions,
)
from menai.cfg.menai_cfg_type_fact import TypeFact, join


class MenaiCFGStructInstanceFold(MenaiCFGPerFunctionPass):
    """
    Fold struct-is-instance? tests whose receiver's proven struct type is the
    tested type, re-wiring the branch to its true edge.

    See the module docstring for the algorithm and the soundness argument.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Fold every foldable struct-is-instance? test in the function."""
        facts = context.facts_for(func)
        if not facts:
            return func, False

        value_defs = _value_defs(func)
        # Map each block id to the block-local facts known at its entry, so a
        # test's receiver is read at the point the test executes even when the
        # test and the branch that consumes it are in different blocks.
        entry_facts = {
            block.id: self._block_local_facts(func, block, facts, context)
            for block in func.blocks
        }

        changed = False
        new_terminators: dict[int, MenaiCFGJumpTerm] = {}

        for block in func.blocks:
            term = block.terminator
            if not isinstance(term, MenaiCFGBranchTerm):
                continue

            test_instr = _branch_instance_test(term, value_defs)
            if test_instr is None:
                continue

            test_block = _defining_block(func, value_defs, test_instr.result.id)
            if test_block is None:
                continue

            if not self._proves_test(func, test_instr, entry_facts[test_block.id], context):
                continue

            new_terminators[block.id] = MenaiCFGJumpTerm(target=term.true_block)
            changed = True

        if not changed:
            return func, False

        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            if block.id in new_terminators:
                block = replace(block, terminator=new_terminators[block.id])

            new_blocks.append(block)

        func = replace(func, blocks=tuple(new_blocks))

        # The folded test's result is no longer read by the branch, and the
        # instructions that only fed the test are now dead too.  Sweep them so
        # the fold does not leave an orphaned operand chain for the bytecode to
        # carry.
        func, _ = prune_dead_definitions(func)

        return func, True

    def _block_local_facts(
        self,
        func: MenaiCFGFunction,
        block: MenaiCFGBlock,
        facts: dict[int, TypeFact],
        context: MenaiCFGContext,
    ) -> dict[int, TypeFact]:
        """
        Compute the facts known at the entry of `block`, block-local facts
        taking precedence over the global per-value facts.

        This mirrors the analysis's own struct field-access rewrite: the global
        facts carry definition-site facts, while a value refined by a
        struct-is-instance? branch has a refined fact that exists only in the
        block-local facts.  Merging with block-local winning is what makes a
        refined receiver visible here.
        """
        block_facts = dict(facts)
        block_facts.update(self._incoming_facts(func, block, facts, context))
        return block_facts

    def _incoming_facts(
        self,
        func: MenaiCFGFunction,
        block: MenaiCFGBlock,
        facts: dict[int, TypeFact],
        context: MenaiCFGContext,
    ) -> dict[int, TypeFact]:
        """
        Compute the facts at a block's entry by joining its predecessors'
        outgoing facts, refined on a struct-is-instance? true edge.

        The entry block has no predecessors and contributes nothing here: its
        parameters' facts are already in the global facts.  A block with a
        single predecessor inherits that predecessor's outgoing facts.  A block
        with multiple predecessors joins them, so a receiver proven on every
        incoming path is still recognised.
        """
        preds = predecessors(func, block)
        if not preds:
            return {}

        result: dict[int, TypeFact] = {}
        for pred in preds:
            pred_facts = _outgoing_facts(pred, facts)
            refinement = _true_edge_refinement(func, pred, block, context)
            if refinement is not None:
                val_id, refined = refinement
                pred_facts = dict(pred_facts)
                pred_facts[val_id] = refined

            for val_id, fact in pred_facts.items():
                if val_id in result:
                    result[val_id] = join(result[val_id], fact)

                else:
                    result[val_id] = fact

        return result

    @staticmethod
    def _proves_test(
        func: MenaiCFGFunction,
        test_instr: MenaiCFGBuiltinInstr,
        block_facts: dict[int, TypeFact],
        context: MenaiCFGContext,
    ) -> bool:
        """
        Return True if the receiver's proven fact is a struct of exactly the
        struct type the test names.
        """
        if len(test_instr.args) != 2:
            return False

        fact = block_facts.get(test_instr.args[0].id)
        if fact is None or not fact.is_known() or fact.kind != 'struct':
            return False

        if fact.struct_type is None:
            return False

        tested_type = context.struct_type_for_test(func, test_instr.args[1].id)
        if tested_type is None:
            return False

        # MenaiStructType equality is tag-based, so compare by value: the
        # receiver's struct type and the tested type may be distinct objects
        # that denote the same type.
        return fact.struct_type == tested_type


def _value_defs(func: MenaiCFGFunction) -> dict[int, object]:
    """Map each defined SSA value id in a function to its defining instruction."""
    result: dict[int, object] = {}
    for block in func.blocks:
        for instr in block.instrs:
            result_value = getattr(instr, 'result', None)
            if result_value is not None:
                result[result_value.id] = instr

    return result


def _defining_block(
    func: MenaiCFGFunction,
    value_defs: dict[int, object],
    value_id: int,
) -> MenaiCFGBlock | None:
    """Return the block that defines `value_id`, or None if it is not defined."""
    instr = value_defs.get(value_id)
    if instr is None:
        return None

    for block in func.blocks:
        for candidate in block.instrs:
            if candidate is instr:
                return block

    return None


def _branch_instance_test(
    term: MenaiCFGBranchTerm,
    value_defs: dict[int, object],
) -> MenaiCFGBuiltinInstr | None:
    """
    Return the struct-is-instance? builtin whose result determines the branch,
    or None if the branch is not determined by such a test.

    Handles both shapes: the condition is directly the test's result, or the
    condition is a phi joining the test's result with constant #f values (the
    (and ...) guard shape).  The test instruction may be defined in a different
    block from the branch, so it is located through `value_defs`.
    """
    direct = _instance_test_of_value(value_defs, term.cond.id)
    if direct is not None:
        return direct

    cond_instr = value_defs.get(term.cond.id)
    if isinstance(cond_instr, MenaiCFGPhiInstr):
        return _instance_test_through_phi(cond_instr, value_defs)

    return None


def _instance_test_of_value(
    value_defs: dict[int, object],
    value_id: int,
) -> MenaiCFGBuiltinInstr | None:
    """Return the struct-is-instance? builtin defining `value_id`, or None."""
    instr = value_defs.get(value_id)
    if (
        isinstance(instr, MenaiCFGBuiltinInstr)
        and instr.op == 'struct-is-instance?'
    ):
        return instr

    return None


def _instance_test_through_phi(
    phi: MenaiCFGPhiInstr,
    value_defs: dict[int, object],
) -> MenaiCFGBuiltinInstr | None:
    """
    Return the single struct-is-instance? builtin a phi joins with constant #f
    values, or None if the phi is not that shape.

    Requires exactly one incoming value to be a struct-is-instance? result and
    every other incoming value to be a constant false.  This is the shape an
    (and (struct? v) (struct-is-instance? v T)) guard lowers to.
    """
    result: MenaiCFGBuiltinInstr | None = None
    for incoming_val, _ in phi.incoming:
        test = _instance_test_of_value(value_defs, incoming_val.id)
        if test is not None:
            if result is not None:
                return None

            result = test
            continue

        if not _is_constant_false(value_defs, incoming_val.id):
            return None

    return result


def _is_constant_false(value_defs: dict[int, object], value_id: int) -> bool:
    """Return True if `value_id` is defined as the constant #f."""
    instr = value_defs.get(value_id)
    if isinstance(instr, MenaiCFGConstInstr):
        return getattr(instr.value, 'value', None) is False

    return False


def _true_edge_refinement(
    func: MenaiCFGFunction,
    pred: MenaiCFGBlock,
    succ: MenaiCFGBlock,
    context: MenaiCFGContext,
) -> tuple[int, TypeFact] | None:
    """
    If pred branches to succ on the true edge on a struct-is-instance? test,
    return (receiver_id, Known('struct', type)).

    This mirrors the analysis's own refinement so the pass reads the same
    refined fact the analysis used to resolve field access on that edge.  The
    tested struct type is read from the context, where the analysis recorded
    it.
    """
    term = pred.terminator
    if not isinstance(term, MenaiCFGBranchTerm) or term.true_block != succ.id:
        return None

    test_instr = _branch_instance_test(term, _value_defs(func))
    if test_instr is None:
        return None

    tested_type = context.struct_type_for_test(func, test_instr.args[1].id)
    if tested_type is None:
        return None

    return test_instr.args[0].id, TypeFact(kind='struct', struct_type=tested_type)


def _outgoing_facts(
    block: MenaiCFGBlock,
    facts: dict[int, TypeFact],
) -> dict[int, TypeFact]:
    """Return the facts established by a block's instructions."""
    result: dict[int, TypeFact] = {}
    for instr in block.instrs:
        result_value = getattr(instr, 'result', None)
        if result_value is None:
            continue

        fact = facts.get(result_value.id)
        if fact is not None:
            result[result_value.id] = fact

    return result
