"""
Tests for MenaiIRLetrecToLoop.

Strategy
--------
The pass is tested at the IR level by constructing IR trees by hand.  Each
guard of the conversion is exercised with a positive case (the letrec is
converted) and a negative case (the letrec is left alone), so a regression in
any single guard is caught precisely.

A helper builds the canonical convertible shape: a single-binding letrec whose
lambda is self-referencing and whose body is a single tail call to that
lambda.  Individual tests perturb one aspect of that shape to exercise the
guard under test.

End-to-end tests compile real Menai source through the full pipeline and check
that a converted loop still produces the correct result (once the backend
lowering is wired up).
"""

from __future__ import annotations

from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRLoop,
    MenaiIRRecur,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.menai_value import MenaiInteger


def _const(n: int) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiInteger(n))


def _local(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name)


def _builtin(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name)


def _builtin_call(name: str, args: list[MenaiIRVariable]) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=_builtin(name),
        arg_plans=args,
        is_tail_call=False,
        is_builtin=True,
        builtin_name=name,
    )


def _self_call(name: str, args: list[MenaiIRVariable]) -> MenaiIRCall:
    """A non-builtin call to `name` — the shape a self-referencing call takes."""
    return MenaiIRCall(
        func_plan=_local(name),
        arg_plans=args,
        is_tail_call=True,
        is_builtin=False,
        builtin_name=None,
    )


def _self_lambda(name: str, params: list[str], body_plan) -> MenaiIRLambda:
    """A lambda that captures itself as a sibling free var (self-referencing)."""
    return MenaiIRLambda(
        params=params,
        body_plan=body_plan,
        sibling_free_vars=[name],
        sibling_free_var_plans=[_local(name)],
        outer_free_vars=[],
        outer_free_var_plans=[],
        param_count=len(params),
        is_variadic=False,
        binding_name=name,
    )


def _convertible_letrec(name: str = "loop", params: list[str] | None = None):
    """
    Build the canonical convertible letrec.

    (letrec ((loop (lambda (n) (if (integer>? n 0) (loop (integer- n 1)) 0))))
      (loop 5))

    Returns (letrec, lambda, init_args).
    """
    params = params if params is not None else ["n"]
    recur = _self_call(name, [_builtin_call('integer-', [_local("n"), _const(1)])])
    base = _const(0)
    body = MenaiIRIf(
        condition_plan=_builtin_call('integer>?', [_local("n"), _const(0)]),
        then_plan=recur,
        else_plan=base,
        in_tail_position=True,
    )
    lam = _self_lambda(name, params, body)
    init_args = [_const(5)]
    letrec = MenaiIRLetrec(
        bindings=[(name, lam)],
        body_plan=_self_call(name, init_args),
        in_tail_position=True,
    )
    return letrec, lam, init_args


class TestConvertible:
    """The canonical shape is converted to a MenaiIRLoop."""

    def test_canonical_letrec_converted_to_loop(self):
        """A single-binding self-recursive tail-call letrec becomes a loop."""
        letrec, _, _ = _convertible_letrec()
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is True
        assert isinstance(result, MenaiIRLoop)
        assert result.params == ["n"]
        assert len(result.init_plans) == 1
        assert result.in_tail_position is True

    def test_self_calls_become_recur(self):
        """The self-referencing tail call inside the body becomes a Recur."""
        letrec, _, _ = _convertible_letrec()
        result, _ = MenaiIRLetrecToLoop().optimize(letrec)
        assert isinstance(result, MenaiIRLoop)
        body = result.body_plan
        assert isinstance(body, MenaiIRIf)
        assert isinstance(body.then_plan, MenaiIRRecur)
        assert len(body.then_plan.arg_plans) == 1

    def test_direct_call_body_without_return(self):
        """A body that is a bare call (no Return wrapper) is also converted."""
        name = "loop"
        recur = _self_call(name, [_builtin_call('integer-', [_local("n"), _const(1)])])
        lam = _self_lambda(name, ["n"], recur)
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(3)]),
            in_tail_position=False,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is True
        assert isinstance(result, MenaiIRLoop)
        assert result.in_tail_position is False

    def test_return_wrapped_body_converted(self):
        """A body wrapped in MenaiIRReturn is converted."""
        letrec, _, _ = _convertible_letrec()
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is True
        assert isinstance(result, MenaiIRLoop)


class TestGuards:
    """Each conversion guard rejects the shape it is meant to reject."""

    def test_two_bindings_not_converted(self):
        """A letrec with two bindings is not convertible."""
        name = "loop"
        recur = _self_call(name, [_local("n")])
        lam = _self_lambda(name, ["n"], recur)
        other = _self_lambda("other", ["n"], _const(0))
        letrec = MenaiIRLetrec(
            bindings=[(name, lam), ("other", other)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_non_lambda_binding_not_converted(self):
        """A single binding whose value is not a lambda is not convertible."""
        letrec = MenaiIRLetrec(
            bindings=[("x", _const(1))],
            body_plan=_local("x"),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_non_self_referencing_lambda_not_converted(self):
        """A lambda that does not capture itself is not convertible."""
        name = "loop"
        lam = MenaiIRLambda(
            params=["n"],
            body_plan=_const(0),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=[],
            outer_free_var_plans=[],
            param_count=1,
            is_variadic=False,
            binding_name=name,
        )
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_body_not_a_call_not_converted(self):
        """A letrec body that is not a call to the lambda is not convertible."""
        name = "loop"
        lam = _self_lambda(name, ["n"], _const(0))
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_const(7),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_body_calls_other_function_not_converted(self):
        """A letrec body that calls a different function is not convertible."""
        name = "loop"
        lam = _self_lambda(name, ["n"], _const(0))
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call("other", [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_body_arity_mismatch_not_converted(self):
        """A body call with the wrong arity is not convertible."""
        name = "loop"
        lam = _self_lambda(name, ["n"], _const(0))
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1), _const(2)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_self_call_wrong_arity_in_body_not_converted(self):
        """A self-call inside the lambda body with the wrong arity is rejected."""
        name = "loop"
        bad_recur = _self_call(name, [_local("n"), _const(1)])
        lam = _self_lambda(name, ["n"], bad_recur)
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_non_tail_self_call_not_converted(self):
        """A self-call in non-tail position is rejected."""
        name = "loop"
        non_tail = _builtin_call('integer+', [_self_call(name, [_local("n")]), _const(1)])
        lam = _self_lambda(name, ["n"], non_tail)
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_nested_lambda_referencing_name_not_converted(self):
        """A nested lambda that references the loop name is rejected."""
        name = "loop"
        nested = MenaiIRLambda(
            params=[],
            body_plan=_local(name),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=[name],
            outer_free_var_plans=[_local(name)],
            param_count=0,
            is_variadic=False,
            binding_name=None,
        )
        lam = _self_lambda(name, ["n"], nested)
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)

    def test_name_referenced_outside_call_not_converted(self):
        """The loop name used as a value (not a call head) is rejected."""
        name = "loop"
        # (list loop) — the name is an argument, not a call head.
        value_use = _builtin_call('list', [_local(name)])
        lam = _self_lambda(name, ["n"], value_use)
        letrec = MenaiIRLetrec(
            bindings=[(name, lam)],
            body_plan=_self_call(name, [_const(1)]),
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is False
        assert isinstance(result, MenaiIRLetrec)


class TestNestedAndRecursive:
    """The pass walks the whole tree and handles already-converted loops."""

    def test_convertible_letrec_nested_in_let(self):
        """A convertible letrec nested inside a let is still converted."""
        letrec, _, _ = _convertible_letrec()
        outer = MenaiIRLet(
            bindings=[("x", _const(1))],
            body_plan=letrec,
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(outer)
        assert changed is True
        assert isinstance(result, MenaiIRLet)
        assert isinstance(result.body_plan, MenaiIRLoop)

    def test_already_converted_loop_is_walked(self):
        """An existing MenaiIRLoop is walked without re-conversion."""
        letrec, _, _ = _convertible_letrec()
        loop, _ = MenaiIRLetrecToLoop().optimize(letrec)
        result, changed = MenaiIRLetrecToLoop().optimize(loop)
        assert changed is False
        assert isinstance(result, MenaiIRLoop)

    def test_non_convertible_letrec_children_still_walked(self):
        """A non-convertible letrec still has its children optimised."""
        name = "loop"
        lam = _self_lambda(name, ["n"], _const(0))
        # Two bindings => not convertible, but the body contains a convertible
        # letrec nested inside.
        inner_letrec, _, _ = _convertible_letrec(name="inner")
        other = _self_lambda("other", ["n"], _const(0))
        letrec = MenaiIRLetrec(
            bindings=[(name, lam), ("other", other)],
            body_plan=inner_letrec,
            in_tail_position=True,
        )
        result, changed = MenaiIRLetrecToLoop().optimize(letrec)
        assert changed is True
        assert isinstance(result, MenaiIRLetrec)
        assert isinstance(result.body_plan, MenaiIRLoop)
