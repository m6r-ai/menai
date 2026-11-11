"""
CFG pass: propagate branch facts to dominated branches.

A conditional branch establishes a fact on each of its outgoing edges: its
condition is true on the true edge and false on the false edge.  When a later
branch tests the same SSA value, and every path reaching that branch carries
the same fact, the branch has only one possible direction and is replaced by
a jump.

Facts are propagated by a forward dataflow.  A block's entry facts are the
intersection of its reachable predecessors' outgoing facts, so a fact survives
only when every path agrees.  Values are in SSA form, so a fact about a value
stays true wherever that value is read; no kill step is needed.

The pass runs after MenaiCFGCSE.  CSE is what makes two syntactically identical
tests refer to the same SSA value -- for example a caller's `vector-empty?`
test and the copy inlined from a callee -- and that shared value is the shape
this pass folds.  The transformation is a pure optimisation: it removes a
branch that could only have gone one way, and a branch has no other effect.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBranchTerm,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGSwitchIntegerTerm,
    MenaiCFGTerminator,
    blocks_by_id,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    prune_dead_definitions,
)


class MenaiCFGBranchFactProp(MenaiCFGPerFunctionPass):
    """
    Fold a conditional branch whose condition's truth is already established.

    See the module docstring for the fact lattice, the dataflow, and the
    reason the pass runs after CSE.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Fold every branch whose condition is known on entry to its block."""
        changed_overall = False

        while True:
            in_facts = _propagate_facts(func)
            new_blocks = []
            changed = False

            for block in func.blocks:
                facts = in_facts.get(block.id)
                term = block.terminator
                if facts is not None and isinstance(term, MenaiCFGBranchTerm):
                    known = facts.get(term.cond.id)
                    if known is not None:
                        target = term.true_block if known else term.false_block
                        block = replace(
                            block,
                            terminator=MenaiCFGJumpTerm(target=target),
                        )
                        changed = True

                new_blocks.append(block)

            if not changed:
                break

            func = replace(func, blocks=tuple(new_blocks))
            func, _ = prune_dead_definitions(func)
            changed_overall = True

        return func, changed_overall


def _propagate_facts(
    func: MenaiCFGFunction,
) -> dict[int, dict[int, bool] | None]:
    """
    Return the branch facts known at each block's entry.

    The value is None for an unreachable block.  A reachable block maps each
    SSA value id to the truth value every path to the block agrees on.
    """
    by_id = blocks_by_id(func)
    entry_id = func.entry().id
    in_facts: dict[int, dict[int, bool] | None] = {
        block.id: None for block in func.blocks
    }
    in_facts[entry_id] = {}
    worklist = [entry_id]

    while worklist:
        block_id = worklist.pop()
        block = by_id[block_id]
        facts = in_facts[block_id]
        assert facts is not None

        for successor_id, successor_facts in _successor_facts(
            block.terminator, facts, entry_id
        ):
            if successor_id not in in_facts:
                continue

            existing = in_facts[successor_id]
            if existing is None:
                in_facts[successor_id] = successor_facts
                worklist.append(successor_id)

            else:
                joined = {
                    value_id: truth
                    for value_id, truth in existing.items()
                    if successor_facts.get(value_id) == truth
                }
                if len(joined) != len(existing):
                    in_facts[successor_id] = joined
                    worklist.append(successor_id)

    return in_facts


def _successor_facts(
    term: MenaiCFGTerminator | None,
    facts: dict[int, bool],
    entry_id: int,
) -> list[tuple[int, dict[int, bool]]]:
    """
    Return the facts carried on each edge leaving a block.

    A branch on a value whose truth is already known carries only the edge
    that is still possible; the other edge's target is unreachable.
    """
    if isinstance(term, MenaiCFGBranchTerm):
        known = facts.get(term.cond.id)
        if known is True:
            return [(term.true_block, dict(facts))]

        if known is False:
            return [(term.false_block, dict(facts))]

        true_facts = dict(facts)
        true_facts[term.cond.id] = True
        false_facts = dict(facts)
        false_facts[term.cond.id] = False
        return [
            (term.true_block, true_facts),
            (term.false_block, false_facts),
        ]

    if isinstance(term, MenaiCFGJumpTerm):
        return [(term.target, dict(facts))]

    if isinstance(term, MenaiCFGSelfLoopTerm):
        target = term.target if term.target is not None else entry_id
        return [(target, dict(facts))]

    if isinstance(term, MenaiCFGSwitchIntegerTerm):
        targets = [target for target in term.targets if target is not None]
        targets.append(term.default_block)
        return [(target, dict(facts)) for target in targets]

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        targets = [*term.targets, term.default_block]
        return [(target, dict(facts)) for target in targets]

    return []
