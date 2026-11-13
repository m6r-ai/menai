"""
CFG pass: merge duplicate exit blocks.

A match lowers one copy of its else arm per edge that can fall through to it.  A
run of same-type literal arms emits

    (if (type? tmp) <equality chain, whose fallthrough is the else arm> else arm)

so the else arm is built twice: once for the type guard's false edge, and once as
the chain's fallthrough, which MenaiCFGSwitchDispatch later makes the switch's
default target.  The two copies are identical whenever the arm reads nothing that
the guard has proved, and only one of them can ever execute.

This pass merges a block into an earlier block whose instructions and terminator
are equivalent, provided the block has no successors and no value it defines is
referenced outside it.

Having no successors is what keeps the rewrite local.  A block with successors
would require rewriting the phi incoming entries of those successors, because a
phi records which predecessor each incoming value arrived from; restricting the
pass to exits means a merge only rewrites the terminators of the predecessors that
pointed at the block, through `remap_term`.  A block whose values are referenced
outside itself cannot be dropped whatever its shape, since the survivor never
computes them.

Equivalence is structural over SSA values.  An instruction pair matches when the
instruction types are the same and every non-result operand is identical, with the
second block's locally defined values mapped to the corresponding values of the
first; the mapping is built instruction by instruction, so a duplicated block that
threads an intermediate value through several instructions is recognised.  Two
blocks are therefore equivalent when they compute the same thing from the same
inputs, not merely when they compute the same thing.

Blocks holding a closure construction or a closure patch are never merged.  A
closure is a unique object, so two constructions that look alike are different
values, and a patch writes into a capture slot, so dropping one would drop the
assignment.  The entry block is never a candidate, because it establishes the
function's parameters and captures.

Two blocks that are both successors of one terminator are never merged.  Merging
them would turn that terminator into a branch whose arms coincide, which is sound
but changes a shape the passes that reason about arm structure — branch folding,
guard placement and common-subexpression elimination — are written against.  It is
also not the duplication this pass exists for: a match's else arm is duplicated
between a type guard's false edge and the arm chain's fallthrough, which are
different terminators.  The restriction keeps every such decision where it is.

Removing a block preserves the order of the blocks that remain, so the
exception-block partition MenaiCFGOrderExceptionBlocks establishes still holds
after this pass has run.
"""

from dataclasses import fields, replace
from typing import Any

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGMakeClosureInstr,
    MenaiCFGTerminator,
    MenaiCFGValue,
    remap_term,
    result_id_in_instr,
    successor_ids,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    replace_blocks,
    value_reference_counts,
)


class MenaiCFGMergeExitBlocks(MenaiCFGPerFunctionPass):
    """
    Merge a block into an earlier block with equivalent contents and no successors.

    See the module docstring for the equivalence rule and the soundness argument.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Merge every mergeable exit block in the function."""
        changed = False

        while True:
            merged = self._merge_one(func)
            if merged is None:
                return func, changed

            func = merged
            changed = True

    @staticmethod
    def _merge_one(func: MenaiCFGFunction) -> MenaiCFGFunction | None:
        """
        Merge one exit block into an equivalent earlier block, or return None.

        One merge per call keeps the predecessor relation and the reference counts
        consistent with the current block list; the caller repeats until nothing
        merges.
        """
        counts = value_reference_counts(func)
        candidates = [block for block in func.blocks[1:] if _is_exit_block(block)]

        for loser in candidates:
            internal = _internal_reference_counts(loser)
            if any(counts.get(val_id, 0) != count for val_id, count in internal.items()):
                continue

            for keeper in candidates:
                if keeper.id == loser.id or keeper.id > loser.id:
                    continue

                if _share_a_terminator(func, keeper.id, loser.id):
                    continue

                if not _blocks_equivalent(keeper, loser):
                    continue

                return _retarget(func, keeper.id, loser.id)

        return None


def _share_a_terminator(func: MenaiCFGFunction, first_id: int, second_id: int) -> bool:
    """Return True if one block's terminator has edges to both blocks."""
    return any(
        first_id in successors and second_id in successors
        for successors in (successor_ids(block.terminator) for block in func.blocks)
    )


def _is_exit_block(block: MenaiCFGBlock) -> bool:
    """
    Return True if `block` may be merged: it is an exit, with no closures or patches.
    """
    if block.terminator is None or successor_ids(block.terminator) != []:
        return False

    if block.patch_instrs:
        return False

    return not any(isinstance(instr, MenaiCFGMakeClosureInstr) for instr in block.instrs)


def _internal_reference_counts(block: MenaiCFGBlock) -> dict[int, int]:
    """Return how many times each value `block` defines is referenced by `block`."""
    counts: dict[int, int] = {}

    def add(val_id: int) -> None:
        counts[val_id] = counts.get(val_id, 0) + 1

    defined: set[int] = set()
    for instr in block.instrs:
        result_id = result_id_in_instr(instr)
        if result_id is not None:
            defined.add(result_id)

        for val_id in value_ids_in_instr(instr):
            add(val_id)

    if block.terminator is not None:
        for val_id in value_ids_in_term(block.terminator):
            add(val_id)

    return {val_id: counts[val_id] for val_id in defined if val_id in counts}


def _blocks_equivalent(first: MenaiCFGBlock, second: MenaiCFGBlock) -> bool:
    """
    Return True if `second` computes exactly what `first` computes from the same inputs.

    `second` is compared with its locally defined values mapped onto `first`'s, so
    the two blocks may use different SSA value ids for the same intermediate result.
    """
    if len(first.instrs) != len(second.instrs):
        return False

    mapping: dict[int, int] = {}
    for first_instr, second_instr in zip(first.instrs, second.instrs):
        if not _instruction_matches(first_instr, second_instr, mapping):
            return False

    return _terminator_matches(first.terminator, second.terminator, mapping)


def _instruction_matches(first: Any, second: Any, mapping: dict[int, int]) -> bool:
    """
    Compare one instruction pair, extending `mapping` with the pair's results.

    The result field is skipped when comparing operands and recorded afterwards,
    so an instruction can refer to a value defined earlier in the same block
    through the mapping.
    """
    if type(first) is not type(second):
        return False

    for field in fields(first):
        if field.name == 'result':
            continue

        if not _operand_matches(
            getattr(first, field.name), getattr(second, field.name), mapping
        ):
            return False

    first_result = result_id_in_instr(first)
    second_result = result_id_in_instr(second)
    if (first_result is None) != (second_result is None):
        return False

    if second_result is not None:
        assert first_result is not None
        mapping[second_result] = first_result

    return True


def _terminator_matches(
    first: MenaiCFGTerminator | None,
    second: MenaiCFGTerminator | None,
    mapping: dict[int, int],
) -> bool:
    """Compare two exit terminators operand by operand under `mapping`."""
    if first is None or second is None:
        return first is None and second is None

    if type(first) is not type(second):
        return False

    return all(
        _operand_matches(getattr(first, field.name), getattr(second, field.name), mapping)
        for field in fields(first)
    )


def _operand_matches(first: Any, second: Any, mapping: dict[int, int]) -> bool:
    """
    Compare two operand values, resolving `second`'s block-local SSA ids.

    A value defined outside either block is the same value in both, so ids must be
    equal; a value `second` defines locally is the corresponding value of `first`
    when the mapping says so.
    """
    if isinstance(first, MenaiCFGValue) or isinstance(second, MenaiCFGValue):
        if not (isinstance(first, MenaiCFGValue) and isinstance(second, MenaiCFGValue)):
            return False

        return mapping.get(second.id, second.id) == first.id

    if isinstance(first, tuple) or isinstance(second, tuple):
        if not (isinstance(first, tuple) and isinstance(second, tuple)):
            return False

        if len(first) != len(second):
            return False

        return all(
            _operand_matches(first_item, second_item, mapping)
            for first_item, second_item in zip(first, second)
        )

    return first == second


def _retarget(func: MenaiCFGFunction, keeper_id: int, loser_id: int) -> MenaiCFGFunction:
    """
    Drop the block `loser_id` and point its predecessors at `keeper_id`.

    Only the terminators of the loser's predecessors change, and only in the block
    ids they name; no instruction is rewritten.
    """
    new_blocks: list[MenaiCFGBlock] = []

    for block in func.blocks:
        if block.id == loser_id:
            continue

        terminator = block.terminator
        if terminator is not None and loser_id in successor_ids(terminator):
            terminator = remap_term(
                terminator,
                lambda target: keeper_id if target == loser_id else target,
            )
            block = replace(block, terminator=terminator)

        new_blocks.append(block)

    return replace_blocks(func, tuple(new_blocks))
