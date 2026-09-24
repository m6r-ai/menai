"""
CFG pass: order exception blocks last.

Blocks that end in a raise (a MenaiCFGRaiseTerm, lowered to RAISE_ERROR)
are exception paths: they are taken only when the program is about to
abort.  Leaving them interleaved with the normal-path blocks puts cold
instructions in the middle of the hot region, which costs instruction
cache locality on the path that actually runs.

This pass stably partitions a function's block list so that raise-terminated
blocks come last:

    [entry] ++ [other normal blocks in original order] ++ [raise blocks in original order]

The partition is stable, so the relative order of normal blocks is unchanged
and the relative order of raise blocks is unchanged.  The entry block is
pinned first and is never treated as an exception block, even in the
degenerate case where it is itself terminated by a raise.

The block list order is not itself the emitted layout — the VM backend
derives its emission order from the CFG (see MenaiVCodeBuilder).  This pass
records the intent in the block list; the backend honours it by emitting
raise-terminated blocks last.  Running this pass last in the CFG pipeline
makes it the final authority on that ordering.
"""

from menai.cfg.menai_cfg import MenaiCFGFunction, MenaiCFGRaiseTerm
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGPerFunctionPass


class MenaiCFGOrderExceptionBlocks(MenaiCFGPerFunctionPass):
    """
    Move raise-terminated blocks to the end of the function's block list.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Stably partition `func.blocks` into the entry block, then other normal
        blocks, then raise-terminated blocks.

        Returns the function unchanged with changed=False when no raise block
        is present or when every raise block is already at the end.
        """
        entry = func.entry()
        raise_blocks = [
            block for block in func.blocks
            if block is not entry and isinstance(block.terminator, MenaiCFGRaiseTerm)
        ]
        if not raise_blocks:
            return func, False

        normal_blocks = [
            block for block in func.blocks
            if block is not entry and not isinstance(block.terminator, MenaiCFGRaiseTerm)
        ]

        reordered = [entry] + normal_blocks + raise_blocks
        if all(new is old for new, old in zip(reordered, func.blocks)):
            return func, False

        func.blocks = reordered
        return func, True
