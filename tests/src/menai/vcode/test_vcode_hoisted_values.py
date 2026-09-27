"""
Tests for the VCode builder's hoisted-value classification.

A value is "hoisted" for a loop when it is defined outside the loop's region
but used inside it.  Such values must survive the loop's back-edge, so the
slot allocator pins them to permanently-live slots.  Misclassifying a value as
hoisted is a missed optimisation (it can no longer be coalesced into the slot
of the value it is moved into on the back-edge); misclassifying one as *not*
hoisted would be a correctness bug (its slot could be freed too early).

The subtle case is a nested loop whose exit block also holds the *enclosing*
loop's back-edge.  That back-edge's args belong to the enclosing loop and are
not used by the inner loop, even though the terminator lives inside the inner
loop's region.

These tests build CFGs by hand and call the builder's classifier directly, so
they assert the classification precisely without depending on the front end.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGParamInstr,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    relink_predecessors,
)
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder


def _v(value_id: int, hint: str = "") -> MenaiCFGValue:
    """Return an SSA value with the given id."""
    return MenaiCFGValue(id=value_id, hint=hint)


def _nested_loop_function() -> MenaiCFGFunction:
    """
    Build a nested-loop CFG:

      block 0 (entry):      %lst param; %x = list-first %lst; jump block 1
      block 1 (outer_hdr):  branch %c1 -> block 4 / block 2
      block 2 (outer_body): %y = list-rest %lst; jump block 3
      block 3 (inner_hdr):  branch %c2 -> block 5 / block 6
      block 6 (inner_body): self-loop -> block 3
      block 5 (inner_exit): self-loop[%y] -> block 1   (the outer back-edge)
      block 4 (exit):       return

    The outer back-edge lives in block 5, which is inside the inner loop's
    region (it is reachable from the inner header).  Its arg %y is defined in
    block 2, outside the inner loop's region.
    """
    lst = _v(0, "lst")
    x = _v(1, "x")
    c1 = _v(2, "c1")
    y = _v(3, "y")
    c2 = _v(4, "c2")

    entry = MenaiCFGBlock(id=0, label="entry")
    entry.instrs = [
        MenaiCFGParamInstr(result=lst, index=0, param_name="lst"),
        MenaiCFGBuiltinInstr(result=x, op="list-first", args=[lst]),
    ]

    outer_hdr = MenaiCFGBlock(id=1, label="outer_hdr")

    outer_body = MenaiCFGBlock(id=2, label="outer_body")
    outer_body.instrs = [
        MenaiCFGBuiltinInstr(result=y, op="list-rest", args=[lst]),
    ]

    inner_hdr = MenaiCFGBlock(id=3, label="inner_hdr")
    inner_body = MenaiCFGBlock(id=6, label="inner_body")
    inner_exit = MenaiCFGBlock(id=5, label="inner_exit")
    exit_block = MenaiCFGBlock(id=4, label="exit")

    entry.terminator = MenaiCFGJumpTerm(target=outer_hdr)
    outer_hdr.terminator = MenaiCFGBranchTerm(
        cond=c1, true_block=exit_block, false_block=outer_body,
    )
    outer_body.terminator = MenaiCFGJumpTerm(target=inner_hdr)
    inner_hdr.terminator = MenaiCFGBranchTerm(
        cond=c2, true_block=inner_exit, false_block=inner_body,
    )
    inner_body.terminator = MenaiCFGSelfLoopTerm(args=[], target=inner_hdr)
    inner_exit.terminator = MenaiCFGSelfLoopTerm(args=[y], target=outer_hdr)
    exit_block.terminator = MenaiCFGReturnTerm(value=x)

    func = MenaiCFGFunction(params=["lst"], binding_name="outer")
    func.blocks = [
        entry, outer_hdr, outer_body, inner_hdr, inner_body, inner_exit, exit_block,
    ]
    relink_predecessors(func)
    return func


class TestHoistedValueClassification:
    """The hoisted-value classifier must not attribute an enclosing loop's
    back-edge args to a nested loop."""

    def test_inner_loop_does_not_hoist_enclosing_back_edge_arg(self):
        """
        %y is defined in the outer loop's body (block 2) and used only by the
        outer loop's back-edge (block 5), which lives in the inner loop's exit
        block.  It must not be classified as hoisted for the inner loop: the
        inner loop never reads it.
        """
        func = _nested_loop_function()
        inner_hdr = next(b for b in func.blocks if b.id == 3)
        assert MenaiVCodeBuilder()._hoisted_value_ids(func, inner_hdr) == []

    def test_outer_loop_hoists_value_used_in_its_region(self):
        """
        %x is defined in the entry block and used in the outer loop's region,
        so it is hoisted for the outer loop.
        """
        func = _nested_loop_function()
        outer_hdr = next(b for b in func.blocks if b.id == 1)
        assert MenaiVCodeBuilder()._hoisted_value_ids(func, outer_hdr) == [1]

    def test_inner_loop_region_includes_its_exit_block(self):
        """
        The inner loop's region includes the exit block holding the outer
        back-edge, which is what makes the exclusion necessary.
        """
        func = _nested_loop_function()
        inner_hdr = next(b for b in func.blocks if b.id == 3)
        region = MenaiVCodeBuilder()._loop_region_ids(inner_hdr)
        assert 5 in region
