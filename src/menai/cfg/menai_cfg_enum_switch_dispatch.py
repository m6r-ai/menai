"""
CFG pass: enum switch dispatch.

Detects chains of branches that all test the same value against enum variants
with `enum=?` and replaces them with a single dense MenaiCFGSwitchEnumTerm,
lowered to the SWITCH_ENUM opcode (a jump table) by the VM backend.

Detection pattern (per test block):

    [const scrutinee]?  const <variant symbol>
    builtin enum=? [scrutinee, variant]
    branch %eq → then_block / next_test_block

The scrutinee must be the same SSA value in every test, or an equal enum
constant re-materialised per block.  Each variant must be distinct.  The chain
is followed along the false edges; the first block that does not match the
pattern terminates the chain and its block becomes the switch default.

Safety: every test block after the first must have exactly one predecessor
(the preceding test block), no other block may reference the SSA values the
transformation deletes (the eq results and variant consts), and no arm target
may itself be a test block.  The enum guard for the scrutinee is inserted by
MenaiCFGGuardInsertion (which runs after this pass), preserving the type error
behaviour of the original `enum=?` chain.

Density: unlike the integer switch there is no density check.  Enum variant
indices are dense (0..nvariants-1) by construction, so the table always covers
the whole range and every arm is a real entry.

This pass runs after MenaiCFGSimplifyBlocks (so empty indirection blocks are
already gone and the chain is in its canonical shape) and before
MenaiCFGGuardInsertion (so the enum=? builtins it removes have not yet had
guards inserted for them).  The enum guard for the switch scrutinee is also
handled by guard insertion.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGValue,
    blocks_by_id,
    predecessors_by_block,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    replace_blocks,
)
from menai.menai_value import MenaiEnum


_MIN_ARMS = 2

_ChainMatch = tuple[  # pylint: disable=invalid-name
    MenaiCFGValue,
    'MenaiCFGConstInstr | None',
    list[tuple[int, int]],
    int,
    list[MenaiCFGBlock],
]


class MenaiCFGEnumSwitchDispatch(MenaiCFGPerFunctionPass):
    """
    Rewrite enum-equality branch chains into dense switch terminators.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        changed = False

        by_id = blocks_by_id(func)
        preds_by_block = predecessors_by_block(func)
        current_ids = set(by_id)

        for entry in list(func.blocks):
            if entry.id not in current_ids:
                continue

            chain = _match_chain(entry, by_id, preds_by_block)
            if chain is None:
                continue

            scrutinee, scrut_const_instr, arms, default_block_id, test_blocks = chain

            if len(arms) < _MIN_ARMS:
                continue

            if not _values_unreferenced_elsewhere(func, test_blocks):
                continue

            new_entry = _rewrite(
                entry, scrut_const_instr, scrutinee, arms, default_block_id,
            )

            test_ids = {b.id for b in test_blocks}
            entry_pos = next(i for i, b in enumerate(func.blocks) if b.id == entry.id)
            blocks = [b for b in func.blocks if b.id not in test_ids]
            entry_pos = min(entry_pos, len(blocks))
            blocks.insert(entry_pos, new_entry)
            func = replace_blocks(func, tuple(blocks))

            by_id = blocks_by_id(func)
            preds_by_block = predecessors_by_block(func)
            current_ids = set(by_id)
            changed = True

        return func, changed


def _match_chain(
    entry: MenaiCFGBlock,
    by_id: dict[int, MenaiCFGBlock],
    preds_by_block: dict[int, list[MenaiCFGBlock]],
) -> _ChainMatch | None:
    """
    Follow the false edges from `entry`, collecting (variant index, then_block)
    arms while each block matches the enum-equality test pattern.

    Returns (scrutinee, scrut_const_instr, arms, default_block_id, test_blocks)
    or None.  `scrut_const_instr` is the entry block's const instruction
    defining the scrutinee when the scrutinee is a per-block constant
    (None when it is a value defined elsewhere).
    """
    term = entry.terminator
    if not isinstance(term, MenaiCFGBranchTerm):
        return None

    scrutinee: MenaiCFGValue | None = None
    scrut_const_val: int | None = None
    scrut_const_instr: MenaiCFGConstInstr | None = None
    arms: list[tuple[int, int]] = []
    seen_variants: set[int] = set()
    test_blocks: list[MenaiCFGBlock] = []
    visited: set[int] = set()

    block = entry
    while True:
        if block.id in visited:
            break

        visited.add(block.id)
        arm = _match_test_block(block, scrutinee, scrut_const_val)
        if arm is None:
            break

        variant_index, then_block_id, scrut, const_val, const_instr = arm

        if block is not entry:
            preds = preds_by_block[block.id]
            if len(preds) != 1 or preds[0] is not test_blocks[-1]:
                break

        if any(then_block_id == t.id for t in test_blocks) or then_block_id == block.id:
            break

        if variant_index in seen_variants:
            break

        if scrutinee is None:
            scrutinee = scrut
            scrut_const_val = const_val
            scrut_const_instr = const_instr

        arms.append((variant_index, then_block_id))
        test_blocks.append(block)
        seen_variants.add(variant_index)

        nxt = block.terminator
        assert isinstance(nxt, MenaiCFGBranchTerm)
        block = by_id[nxt.false_block]

    if scrutinee is None or not test_blocks:
        return None

    last_term = test_blocks[-1].terminator
    assert isinstance(last_term, MenaiCFGBranchTerm)
    default_block_id = last_term.false_block
    if any(default_block_id == t.id for t in test_blocks):
        return None

    return scrutinee, scrut_const_instr, arms, default_block_id, test_blocks


def _match_test_block(
    block: MenaiCFGBlock,
    expected_scrutinee: MenaiCFGValue | None,
    expected_const_val: int | None,
) -> tuple[int, int, MenaiCFGValue, 'int | None', 'MenaiCFGConstInstr | None'] | None:
    """
    Match a single block against the test pattern:

        [const scrutinee]? const <variant>
        builtin enum=? [scrutinee, variant]
        branch %eq → then / next

    Returns (variant_index, then_block_id, scrutinee, scrut_const_val,
    scrut_const_instr) or None.  The scrutinee const, when present, must head
    the block.
    """
    if block.patch_instrs:
        return None

    term = block.terminator
    if not isinstance(term, MenaiCFGBranchTerm):
        return None

    instrs = list(block.instrs)

    if not instrs:
        return None

    eq = instrs[-1]
    if not isinstance(eq, MenaiCFGBuiltinInstr) or eq.op != 'enum=?' or len(eq.args) != 2:
        return None

    if term.cond.id != eq.result.id:
        return None

    if len(instrs) < 2:
        return None

    lit_instr = instrs[-2]
    if not isinstance(lit_instr, MenaiCFGConstInstr) or not isinstance(lit_instr.value, MenaiEnum):
        return None

    variant_index = lit_instr.value.variant_index

    a0, a1 = eq.args
    if a0.id == lit_instr.result.id:
        scrut = a1

    elif a1.id == lit_instr.result.id:
        scrut = a0

    else:
        return None

    scrut_const_instr: MenaiCFGConstInstr | None = None
    scrut_const_val: int | None = None
    if len(instrs) >= 3:
        sc = instrs[-3]
        if isinstance(sc, MenaiCFGConstInstr) and sc.result.id == scrut.id:
            if not isinstance(sc.value, MenaiEnum):
                return None

            scrut_const_instr = sc
            scrut_const_val = sc.value.variant_index
            prefix = instrs[:-3]

        else:
            prefix = instrs[:-2]

    else:
        prefix = instrs[:-2]

    if prefix:
        return None

    if expected_scrutinee is not None:
        if scrut_const_val is not None:
            if expected_const_val is None or scrut_const_val != expected_const_val:
                return None

        elif scrut.id != expected_scrutinee.id:
            return None

    return variant_index, term.true_block, scrut, scrut_const_val, scrut_const_instr


def _values_unreferenced_elsewhere(
    func: MenaiCFGFunction,
    test_blocks: list[MenaiCFGBlock],
) -> bool:
    """
    Verify the SSA values defined by the per-block consts and eq results being
    deleted are not referenced outside their own test blocks.
    """
    deleted_ids: set[int] = set()
    for block in test_blocks:
        assert isinstance(block.instrs[-1], MenaiCFGBuiltinInstr)
        assert isinstance(block.instrs[-2], MenaiCFGConstInstr)
        deleted_ids.add(block.instrs[-1].result.id)
        deleted_ids.add(block.instrs[-2].result.id)

        if len(block.instrs) >= 3 and isinstance(block.instrs[-3], MenaiCFGConstInstr):
            deleted_ids.add(block.instrs[-3].result.id)

    test_ids = {b.id for b in test_blocks}

    for block in func.blocks:
        if block.id in test_ids:
            continue

        for instr in block.instrs:
            if any(v in deleted_ids for v in value_ids_in_instr(instr)):
                return False

        if block.terminator is not None:
            if any(v in deleted_ids for v in value_ids_in_term(block.terminator)):
                return False

    return True


def _rewrite(
    entry: MenaiCFGBlock,
    scrut_const_instr: MenaiCFGConstInstr | None,
    scrutinee: MenaiCFGValue,
    arms: list[tuple[int, int]],
    default_block_id: int,
) -> MenaiCFGBlock:
    """
    Return a copy of `entry` keeping the scrutinee const (when the scrutinee is
    defined here) and with the terminator replaced by the switch.
    """
    n_variants = max(k for k, _ in arms) + 1

    targets: list[int] = [default_block_id] * n_variants
    for k, then_block_id in arms:
        targets[k] = then_block_id

    new_instrs: list[MenaiCFGInstr] = []
    if scrut_const_instr is not None:
        new_instrs.append(scrut_const_instr)

    return replace(
        entry,
        instrs=tuple(new_instrs),
        terminator=MenaiCFGSwitchEnumTerm(
            value=scrutinee,
            targets=tuple(targets),
            default_block=default_block_id,
        ),
    )
