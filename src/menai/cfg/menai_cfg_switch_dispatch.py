"""
CFG pass: integer switch dispatch.

Detects chains of branches that all test the same value against integer
literals with `integer=?` and replaces them with a single dense
MenaiCFGSwitchTerm, lowered to the SWITCH_INT opcode (a jump table) by the
VM backend.

Detection pattern (per test block):

    [const scrutinee]?  const <integer literal>
    builtin integer=? [scrutinee, literal]
    branch %eq → then_block / next_test_block

The scrutinee must be the same SSA value in every test, or an equal integer
constant re-materialised per block (the common shape after inlining a call
with a constant argument).  Each literal must be a distinct compile-time
integer.  The chain is followed along the false edges; the first block that
does not match the pattern terminates the chain and its block becomes the
switch default.

Safety: every test block after the first must have exactly one predecessor
(the preceding test block), no other block may reference the SSA values the
transformation deletes (the eq results and literal consts), and no arm target
may itself be a test block.  The integer guard for the scrutinee is inserted
by MenaiCFGTypePropagation (which runs after this pass), preserving the type
error behaviour of the original `integer=?` chain — `integer=?` raises on
non-integer operands, and the guard raises before any arm is tested.

Density: a table is only built when the literals span a dense range:
span <= max(8, 4 * arm_count) and span <= 4095.  Sparse chains are left as
branches.

This pass runs after MenaiCFGSimplifyBlocks (so empty indirection blocks are
already gone and the chain is in its canonical shape) and before
MenaiCFGTypePropagation (so the eq builtins it removes have not yet had
guards inserted for them).  The integer guard for the switch scrutinee is
also handled by type propagation.
"""


from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGSwitchTerm,
    MenaiCFGValue,
    value_ids_in_instr,
    value_ids_in_term,
    relink_predecessors,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGOptimizationPass
from menai.menai_value import MenaiInteger


_MIN_ARMS = 2

_ChainMatch = tuple[  # pylint: disable=invalid-name
    MenaiCFGValue,
    'MenaiCFGConstInstr | None',
    list[tuple[int, MenaiCFGBlock]],
    MenaiCFGBlock,
    list[MenaiCFGBlock],
]


class MenaiCFGSwitchDispatch(MenaiCFGOptimizationPass):
    """
    Rewrite integer-equality branch chains into dense switch terminators.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        changed = False

        for entry in list(func.blocks):
            if not any(b.id == entry.id for b in func.blocks):
                continue

            chain = _match_chain(entry)
            if chain is None:
                continue

            scrutinee, scrut_const_instr, arms, default_block, test_blocks = chain

            if len(arms) < _MIN_ARMS or not _is_dense(arms):
                continue

            if not _values_unreferenced_elsewhere(func, test_blocks):
                continue

            _rewrite(entry, scrut_const_instr, scrutinee, arms, default_block)

            test_ids = {b.id for b in test_blocks}
            entry_pos = next(i for i, b in enumerate(func.blocks) if b.id == entry.id)
            func.blocks = [b for b in func.blocks if b.id not in test_ids]
            entry_pos = min(entry_pos, len(func.blocks))
            func.blocks.insert(entry_pos, entry)

            relink_predecessors(func)
            changed = True

        return func, changed


def _match_chain(entry: MenaiCFGBlock) -> _ChainMatch | None:
    """
    Follow the false edges from `entry`, collecting (literal, then_block)
    arms while each block matches the integer-equality test pattern.

    Returns (scrutinee, scrut_const_instr, arms, default_block, test_blocks)
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
    arms: list[tuple[int, MenaiCFGBlock]] = []
    seen_literals: set[int] = set()
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

        literal, then_block, scrut, const_val, const_instr = arm

        # Interior test blocks must be reachable only through the chain —
        # an outside predecessor would lose its path when the block is removed.
        if block is not entry:
            if len(block.predecessors) != 1 or block.predecessors[0] is not test_blocks[-1]:
                break

        if any(then_block.id == t.id for t in test_blocks) or then_block is block:
            break

        if literal in seen_literals:
            break

        if scrutinee is None:
            scrutinee = scrut
            scrut_const_val = const_val
            scrut_const_instr = const_instr

        arms.append((literal, then_block))
        test_blocks.append(block)
        seen_literals.add(literal)

        nxt = block.terminator
        assert isinstance(nxt, MenaiCFGBranchTerm)
        block = nxt.false_block

    if scrutinee is None or not test_blocks:
        return None

    # The chain must terminate somewhere that is not a removed test block.
    last_term = test_blocks[-1].terminator
    assert isinstance(last_term, MenaiCFGBranchTerm)
    default_block = last_term.false_block
    if any(default_block.id == t.id for t in test_blocks):
        return None

    return scrutinee, scrut_const_instr, arms, default_block, test_blocks


def _match_test_block(
    block: MenaiCFGBlock,
    expected_scrutinee: MenaiCFGValue | None,
    expected_const_val: int | None,
) -> tuple[int, MenaiCFGBlock, MenaiCFGValue, 'int | None', 'MenaiCFGConstInstr | None'] | None:
    """
    Match a single block against the test pattern:

        [const scrutinee]? const <literal>
        builtin integer=? [scrutinee, literal]
        branch %eq → then / next

    Returns (literal, then_block, scrutinee, scrut_const_val, scrut_const_instr)
    or None.  The scrutinee const, when present, must head the block.
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
    if not isinstance(eq, MenaiCFGBuiltinInstr) or eq.op != 'integer=?' or len(eq.args) != 2:
        return None

    if term.cond.id != eq.result.id:
        return None

    if len(instrs) < 2:
        return None

    lit_instr = instrs[-2]
    if not isinstance(lit_instr, MenaiCFGConstInstr) or not isinstance(lit_instr.value, MenaiInteger):
        return None

    literal = lit_instr.value.value

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
            if not isinstance(sc.value, MenaiInteger):
                return None

            scrut_const_instr = sc
            scrut_const_val = sc.value.value
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

    return literal, term.true_block, scrut, scrut_const_val, scrut_const_instr


def _is_dense(arms: list[tuple[int, MenaiCFGBlock]]) -> bool:
    """Return True when the literals span a dense enough range to tabulate."""
    lo = min(k for k, _ in arms)
    hi = max(k for k, _ in arms)
    span = hi - lo + 1
    return span <= max(8, 4 * len(arms)) and span <= 0xFFF


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
    arms: list[tuple[int, MenaiCFGBlock]],
    default_block: MenaiCFGBlock,
) -> None:
    """
    Rewrite `entry` in place: keep the scrutinee const (when the scrutinee is
    defined here) and replace the terminator with the switch.  All existing
    references to `entry` remain valid.
    """
    lo = min(k for k, _ in arms)
    hi = max(k for k, _ in arms)

    targets: list[MenaiCFGBlock | None] = [None] * (hi - lo + 1)
    for k, then_block in arms:
        targets[k - lo] = then_block

    new_instrs: list[MenaiCFGInstr] = []
    if scrut_const_instr is not None:
        new_instrs.append(scrut_const_instr)

    entry.instrs = new_instrs
    entry.terminator = MenaiCFGSwitchTerm(
        value=scrutinee,
        min=lo,
        targets=targets,
        default_block=default_block,
    )
