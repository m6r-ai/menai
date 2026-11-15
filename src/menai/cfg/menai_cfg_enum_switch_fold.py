"""
CFG pass: fold an enum switch whose scrutinee is a known enum.

A match over an enum lowers to a chain of enum=? tests, and
MenaiCFGEnumSwitchDispatch fuses that chain into a MenaiCFGSwitchEnumTerm — a
dense jump table over the variant index.  When the scrutinee is defined by a
MenaiCFGMakeEnumInstr, the enum type and the variant index are both known at
compile time, so the table's answer for that value is known too: the switch is
an unconditional jump to the arm for that variant.

This is what deletes a mode dispatch that inlining has made static.  A module
that takes an optional mode argument picks its default with

    (if (list-null? rest) (mode 'auto) (list-first rest))

and when it is called with one argument and inlined into its caller the rest
list is empty, so the default is the only incoming value of the phi and the
variant is a compile-time constant.  Every other arm of the match then becomes
unreachable and is never emitted.

The fold needs no type fact: a MenaiCFGMakeEnumInstr names its enum type and
variant index directly, and in SSA form its definition dominates every use.  The
jump is therefore the arm the jump table would select, with no table.

The fold matches on the variant index alone, without checking that the scrutinee's
enum type is the type the switch's arms test.  It cannot check:
MenaiCFGSwitchEnumTerm carries a dense variant table and no enum type, and the
fold exists to need no type fact.  The fold is nevertheless correct because it
reaches a switch whose scrutinee's type is *already* established, and this is the
property to preserve:

  - A match's desugaring puts an `enum-is-instance?` test in front of the
    `enum=?` chain, and MenaiCFGEnumSwitchDispatch fuses only the `enum=?` tests,
    so the type test never becomes part of the switch.  It stays a branch that
    dominates the switch.
  - A variant index is therefore read only on the edge where the scrutinee is an
    instance of the arms' enum type.  Given the type, the index identifies the
    arm exactly, and a MenaiCFGMakeEnumInstr's index is known.

So the fold does not need to know the type: on every path that reaches the switch,
the type is the one the table indexes.  A mismatched enum is not a problem — it is
routed away by the type test before the switch is reached, and because the fold
replaces the terminator without adding a path, it cannot make an arm reachable
that was not.  It would only be unsafe to hoist the switch above its type test, or
to fold a switch reached without one.

The unselected arms become unreachable, and the VCode builder emits only
reachable blocks, so they cost nothing.  The enum construction, and anything
that only fed it, is dead once the switch is gone; prune_dead_definitions sweeps
it.

Position in the pipeline
------------------------
This pass runs last, after every other CFG pass.  That is not a convenience: it
is where the fold costs nothing.  Run earlier — before
MenaiCFGInterprocTypeAnalysis, or before MenaiCFGSchedule — deleting the switch
perturbs the passes that follow, and they lose sharing exactly where it is
dearest.  In the encoders it stops the type analysis sharing one guarded
`bytes-append-u8` between two arms of the inlined compressor and stops it
proving a call result's type, and the loop that follows is then duplicated into
its own continuation blocks.  The result measured on the corpus was a 0.005%
*increase* in executed instructions while the code shrank: a fold is not free
just because it deletes blocks.  Running last leaves the rest of the pipeline
compiling exactly what it compiled before, so the only change is the switch that
is gone.

The deleted dispatch is one SWITCH_ENUM executed once per compress call, so its
absence is worth two instructions per call and no more.  What the fold is really
for is the arms it makes unreachable: those stop being emitted at all.

Only an enum switch is folded.  An integer=? chain is fused into a
MenaiCFGSwitchIntegerTerm by MenaiCFGSwitchDispatch, and a constant scrutinee
would fold the same way, but that shape is not the dispatch this pass exists for
and no corpus cost currently calls for it.
"""

from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGMakeEnumInstr,
    MenaiCFGSwitchEnumTerm,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    prune_dead_definitions,
)


class MenaiCFGEnumSwitchFold(MenaiCFGPerFunctionPass):
    """
    Fold an enum switch whose scrutinee is a statically-known enum, replacing it
    with a jump to the selected arm.

    See the module docstring for the algorithm and why it needs no type fact.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Replace every switch on a constructed enum with the arm it selects."""
        variants = _known_variants(func)
        if not variants:
            return func, False

        changed = False
        new_blocks: list[MenaiCFGBlock] = []

        for block in func.blocks:
            term = block.terminator
            if isinstance(term, MenaiCFGSwitchEnumTerm):
                target = _selected_arm(term, variants)
                if target is not None:
                    block = replace(block, terminator=MenaiCFGJumpTerm(target=target))
                    changed = True

            new_blocks.append(block)

        if not changed:
            return func, False

        func = replace(func, blocks=tuple(new_blocks))

        # The enum construction is dead once its only reader, the switch, is
        # gone, and the instructions that only fed it are dead with it.  Sweep
        # them so the fold does not leave an orphaned operand chain for the
        # bytecode to carry.
        func, _ = prune_dead_definitions(func)

        return func, True


def _known_variants(func: MenaiCFGFunction) -> dict[int, int]:
    """Map each SSA value that is a constructed enum to its variant index."""
    variants: dict[int, int] = {}

    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGMakeEnumInstr):
                variants[instr.result.id] = instr.variant_index

    return variants


def _selected_arm(
    term: MenaiCFGSwitchEnumTerm,
    variants: dict[int, int],
) -> int | None:
    """
    Return the block the switch jumps to for a known scrutinee, or None when the
    scrutinee is not a constructed enum.

    The variant index is bounds-checked against the table: a table shorter than
    the scrutinee's variant index would mean the two disagree about the enum
    type, and the switch is then left for the runtime to dispatch.
    """
    variant_index = variants.get(term.value.id)
    if variant_index is None or not 0 <= variant_index < len(term.targets):
        return None

    return term.targets[variant_index]
