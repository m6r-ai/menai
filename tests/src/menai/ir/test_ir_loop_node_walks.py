"""
Tests that the IR inliner, optimizer, and use counter walk MenaiIRLoop and
MenaiIRRecur nodes.

These three passes run after MenaiIRLetrecToLoop in the pipeline, so they must
handle the two loop nodes or every program containing a converted loop fails.
The tests build a small loop IR by hand and check each pass traverses it
without error and preserves the loop structure.
"""

from __future__ import annotations

from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLetrec,
    MenaiIRLoop,
    MenaiIRRecur,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.ir.menai_ir_use_counter import MenaiIRUseCounter
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


def _self_call(name: str, args: list[MenaiIRVariable]) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=_local(name),
        arg_plans=args,
        is_tail_call=True,
        is_builtin=False,
        builtin_name=None,
    )


def _make_loop() -> MenaiIRLoop:
    """Build the loop that the canonical convertible letrec lowers to."""
    name = "loop"
    recur = _self_call(name, [_builtin_call('integer-', [_local("n"), _const(1)])])
    body = MenaiIRIf(
        condition_plan=_builtin_call('integer>?', [_local("n"), _const(0)]),
        then_plan=recur,
        else_plan=_const(0),
        in_tail_position=True,
    )
    lam = MenaiIRLambda(
        params=["n"],
        body_plan=body,
        sibling_free_vars=[name],
        sibling_free_var_plans=[_local(name)],
        outer_free_vars=[],
        outer_free_var_plans=[],
        param_count=1,
        is_variadic=False,
        binding_name=name,
    )
    letrec = MenaiIRLetrec(
        bindings=[(name, lam)],
        body_plan=_self_call(name, [_const(5)]),
        in_tail_position=True,
    )
    loop, changed = MenaiIRLetrecToLoop().optimize(letrec)
    assert changed is True
    assert isinstance(loop, MenaiIRLoop)
    return loop


class TestLoopNodeWalks:
    """The inliner, optimizer, and use counter traverse loop nodes."""

    def test_inliner_walks_loop(self):
        """The inliner traverses a loop without raising and preserves it."""
        loop = _make_loop()
        result, _ = MenaiIRInliner().optimize(loop)
        assert isinstance(result, MenaiIRLoop)
        assert result.params == ["n"]

    def test_optimizer_walks_loop(self):
        """The optimizer traverses a loop without raising and preserves it."""
        loop = _make_loop()
        result, _ = MenaiIROptimizer().optimize(loop)
        assert isinstance(result, MenaiIRLoop)
        assert result.params == ["n"]

    def test_use_counter_walks_loop(self):
        """The use counter traverses a loop and counts its param uses."""
        loop = _make_loop()
        counts = MenaiIRUseCounter().count(loop)
        # The loop body references `n` in the condition and the recur arg.
        # The count is attributed to the loop's synthetic binding id.
        assert len(counts.frames) >= 1
        total = sum(frame.counts.get(id(loop), 0) for frame in counts.frames)
        assert total == 2

    def test_inliner_walks_recur_args(self):
        """A Recur nested in a loop has its argument plans walked."""
        loop = _make_loop()
        result, _ = MenaiIRInliner().optimize(loop)
        assert isinstance(result, MenaiIRLoop)
        body = result.body_plan
        assert isinstance(body, MenaiIRIf)
        assert isinstance(body.then_plan, MenaiIRRecur)
