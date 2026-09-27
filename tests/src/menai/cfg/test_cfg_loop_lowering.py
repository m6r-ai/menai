"""
Tests for the CFG builder's MenaiIRLoop / MenaiIRRecur lowering.

A MenaiIRLoop becomes an inline loop in the enclosing function: the loop params
become phi nodes in a new loop-entry block, and each MenaiIRRecur becomes a
MenaiCFGSelfLoopTerm back-edge whose param_vals are the phi results and whose
target is the loop-entry block.

The tests build the loop IR by hand (the shape MenaiIRLetrecToLoop produces)
and run MenaiCFGBuilder directly, so they assert the CFG structure precisely
without depending on the backend.
"""

from __future__ import annotations

from menai.cfg.menai_cfg import (
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
)
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRIf,
    MenaiIRLoop,
    MenaiIRRecur,
    MenaiIRVariable,
)
from menai.menai_value import MenaiInteger


def _const(n: int) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiInteger(n))


def _local(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name)


def _builtin_call(name: str, args: list[MenaiIRVariable]) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=_local(name),
        arg_plans=args,
        is_tail_call=False,
        is_builtin=True,
        builtin_name=name,
    )


def _make_loop() -> MenaiIRLoop:
    """
    Build the loop that the canonical convertible letrec lowers to:

    (letrec ((loop (lambda (n) (if (integer>? n 0) (loop (integer- n 1)) 0))))
      (loop 5))
    """
    recur = MenaiIRRecur(
        arg_plans=[_builtin_call('integer-', [_local("n"), _const(1)])],
        is_tail_call=True,
    )
    body = MenaiIRIf(
        condition_plan=_builtin_call('integer>?', [_local("n"), _const(0)]),
        then_plan=recur,
        else_plan=_const(0),
        in_tail_position=True,
    )
    return MenaiIRLoop(
        params=["n"],
        init_plans=[_const(5)],
        body_plan=body,
        in_tail_position=True,
    )


class TestLoopLowering:
    """A MenaiIRLoop lowers to a loop-entry block with phis and a back-edge."""

    def test_loop_entry_block_has_param_phi(self):
        """The loop param becomes a phi in a loop-entry block."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        loop_entries = [b for b in cfg.blocks if b.label == "loop_entry"]
        assert len(loop_entries) == 1
        phis = [i for i in loop_entries[0].instrs if isinstance(i, MenaiCFGPhiInstr)]
        assert len(phis) == 1

    def test_back_edge_is_self_loop_with_param_vals(self):
        """The recur becomes a SelfLoopTerm with param_vals and a target."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        self_loops = [
            b.terminator for b in cfg.blocks
            if isinstance(b.terminator, MenaiCFGSelfLoopTerm)
        ]
        assert len(self_loops) == 1
        term = self_loops[0]
        assert term.param_vals is not None
        assert len(term.param_vals) == 1
        assert term.target is not None
        assert term.target.label == "loop_entry"

    def test_back_edge_targets_loop_entry(self):
        """The self-loop's target is the loop-entry block."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        loop_entry = next(b for b in cfg.blocks if b.label == "loop_entry")
        term = next(
            b.terminator for b in cfg.blocks
            if isinstance(b.terminator, MenaiCFGSelfLoopTerm)
        )
        assert term.target is loop_entry

    def test_phi_incoming_has_init_and_back_edge(self):
        """The param phi merges the init value and the back-edge value."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        loop_entry = next(b for b in cfg.blocks if b.label == "loop_entry")
        phi = next(i for i in loop_entry.instrs if isinstance(i, MenaiCFGPhiInstr))
        assert len(phi.incoming) == 2

    def test_loop_entry_is_predecessor_linked(self):
        """The loop-entry block records the back-edge as a predecessor."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        loop_entry = next(b for b in cfg.blocks if b.label == "loop_entry")
        # Predecessors: the pre-loop block (jump in) and the recur block (back-edge).
        assert len(loop_entry.predecessors) == 2

    def test_param_vals_match_phi_results(self):
        """The back-edge param_vals are the loop-entry phi results."""
        cfg = MenaiCFGBuilder().build(_make_loop())
        loop_entry = next(b for b in cfg.blocks if b.label == "loop_entry")
        phi = next(i for i in loop_entry.instrs if isinstance(i, MenaiCFGPhiInstr))
        term = next(
            b.terminator for b in cfg.blocks
            if isinstance(b.terminator, MenaiCFGSelfLoopTerm)
        )
        assert term.param_vals == [phi.result]
