"""
Tests for MenaiIRReachability and its use by MenaiIROptimizer.

Strategy
--------
Two layers, as elsewhere in the IR tests:

1. Unit tests that construct IR trees by hand and assert which bindings the
   reachability analysis marks live.  These are precise and fast.

2. Integration tests that compile real Menai source and assert that an
   unreachable mutually-recursive group is removed while reachable recursion
   is preserved.

The motivating case is a group of mutually-recursive bindings that nothing
reachable references.  Every member of such a group references another member,
so a reference count is never zero and a count-based dead-binding test can
never remove any of them.  Reachability marks the whole group dead as a unit.
"""

from __future__ import annotations

from menai import Menai
from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.ir.menai_ir_reachability import MenaiIRReachability
from menai.ir.menai_ir_use_counter import MenaiIRUseCounter
from menai.menai_value import MenaiInteger


def _const(n: int) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiInteger(n))


def _local(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name)


def _builtin_call(name: str, args: list, tail: bool = False) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=MenaiIRVariable(name='$' + name),
        arg_plans=tuple(args),
        is_tail_call=tail,
        is_builtin=True,
        builtin_name=name,
    )


def _user_call(func: str, args: list, tail: bool = False) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=_local(func),
        arg_plans=tuple(args),
        is_tail_call=tail,
        is_builtin=False,
        builtin_name=None,
    )


def _analyze(ir):
    """Run the use counter then reachability, returning the reachability result."""
    counts = MenaiIRUseCounter().count(ir)
    return MenaiIRReachability().analyze(ir, counts.lambda_frame_ids)


# ---------------------------------------------------------------------------
# Unit tests: reachability of let/letrec bindings
# ---------------------------------------------------------------------------

class TestReachabilityBasic:
    """Reachability of simple let bindings."""

    def test_used_binding_is_live(self):
        """A binding referenced from the body is live."""
        b_x = ("x", _const(1))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_x],
            body_plan=_local("x"),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert reach.is_live(0, id(b_x))

    def test_unused_binding_is_dead(self):
        """A binding never referenced is dead."""
        b_x = ("x", _const(1))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_x],
            body_plan=_const(99),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert not reach.is_live(0, id(b_x))

    def test_binding_referenced_only_by_dead_binding_is_dead(self):
        """
        A binding referenced only from another dead binding's value is dead.

        (let ((x 1)) (let ((y x)) 42))  — y is dead, so x is dead too.
        """
        b_x = ("x", _const(1))
        b_y = ("y", _local("x"))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_x],
            body_plan=MenaiIRLet(
                bindings=[b_y],
                body_plan=_const(42),
                in_tail_position=True,
            ),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert not reach.is_live(0, id(b_y))
        assert not reach.is_live(0, id(b_x))


class TestReachabilityLetrec:
    """Reachability of letrec bindings, including mutually-recursive groups."""

    def test_reachable_recursive_binding_is_live(self):
        """A self-recursive binding called from the body is live."""
        lam = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        b_f = ("f", lam)
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[b_f],
            body_plan=_user_call("f", [_const(1)], tail=True),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert reach.is_live(0, id(b_f))

    def test_unreachable_mutually_recursive_group_is_dead(self):
        """
        A mutually-recursive group that nothing reachable references is dead.

        f calls g and g calls f, so each has a non-zero reference count, but
        neither is reachable from the body.
        """
        lam_f = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("g", [_local("n")], tail=True)),
            sibling_free_vars=("g",),
            sibling_free_var_plans=(_local("g"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        lam_g = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="g",
        )
        b_f = ("f", lam_f)
        b_g = ("g", lam_g)
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[b_f, b_g],
            body_plan=_const(42),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert not reach.is_live(0, id(b_f))
        assert not reach.is_live(0, id(b_g))

    def test_reachable_mutually_recursive_group_is_live(self):
        """A mutually-recursive group called from the body is live in full."""
        lam_f = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("g", [_local("n")], tail=True)),
            sibling_free_vars=("g",),
            sibling_free_var_plans=(_local("g"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        lam_g = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="g",
        )
        b_f = ("f", lam_f)
        b_g = ("g", lam_g)
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[b_f, b_g],
            body_plan=_user_call("f", [_const(1)], tail=True),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert reach.is_live(0, id(b_f))
        assert reach.is_live(0, id(b_g))

    def test_dead_group_does_not_keep_referenced_binding_live(self):
        """
        A dead group's outward reference does not make its target live.

        The group (f, g) is unreachable; f references a helper h.  h must be
        dead because the only reference to it comes from dead code.
        """
        lam_h = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_local("n")),
            sibling_free_vars=(),
            sibling_free_var_plans=(),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="h",
        )
        lam_f = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("h", [_local("n")], tail=True)),
            sibling_free_vars=("h",),
            sibling_free_var_plans=(_local("h"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        lam_g = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="g",
        )
        b_h = ("h", lam_h)
        b_f = ("f", lam_f)
        b_g = ("g", lam_g)
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[b_h],
            body_plan=MenaiIRLetrec(
                bindings=[b_f, b_g],
                body_plan=_const(42),
                in_tail_position=True,
            ),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert not reach.is_live(0, id(b_f))
        assert not reach.is_live(0, id(b_g))
        assert not reach.is_live(0, id(b_h))


class TestReachabilityShadowing:
    """A reference resolves to the innermost binding of that name."""

    def test_inner_binding_shadows_outer(self):
        """
        An inner binding shadows the outer one; only the inner is live.

        (let ((x 1)) (let ((x 2)) x))
        """
        b_outer = ("x", _const(1))
        b_inner = ("x", _const(2))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_outer],
            body_plan=MenaiIRLet(
                bindings=[b_inner],
                body_plan=_local("x"),
                in_tail_position=True,
            ),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert reach.is_live(0, id(b_inner))
        assert not reach.is_live(0, id(b_outer))


class TestReachabilityLambdaFrames:
    """Reachability across lambda frame boundaries."""

    def test_capture_plan_marks_enclosing_binding_live(self):
        """
        A lambda's free-var plan is evaluated in the enclosing scope, so it
        makes the captured binding live.
        """
        lam = MenaiIRLambda(
            params=(),
            body_plan=MenaiIRReturn(value_plan=_local("outer_x")),
            sibling_free_vars=(),
            sibling_free_var_plans=(),
            outer_free_vars=("outer_x",),
            outer_free_var_plans=(_local("outer_x"),),
            param_count=0,
            is_variadic=False,
        )
        b_x = ("outer_x", _const(5))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_x],
            body_plan=lam,
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert reach.is_live(0, id(b_x))

    def test_binding_inside_unreachable_lambda_is_dead(self):
        """
        A binding referenced only inside a lambda that is itself unreachable is
        dead, because the lambda's body is never traced.
        """
        lam = MenaiIRLambda(
            params=(),
            body_plan=MenaiIRReturn(value_plan=_local("inner_x")),
            sibling_free_vars=(),
            sibling_free_var_plans=(),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=0,
            is_variadic=False,
        )
        b_x = ("inner_x", _const(5))
        b_lam = ("dead_lam", MenaiIRLet(
            bindings=[b_x],
            body_plan=lam,
            in_tail_position=True,
        ))
        ir = MenaiIRReturn(value_plan=MenaiIRLet(
            bindings=[b_lam],
            body_plan=_const(42),
            in_tail_position=True,
        ))
        reach = _analyze(ir)
        assert not reach.is_live(0, id(b_lam))
        assert not reach.is_live(0, id(b_x))


# ---------------------------------------------------------------------------
# Unit tests: optimizer removes an unreachable mutually-recursive group
# ---------------------------------------------------------------------------

class TestOptimizerRemovesUnreachableGroup:
    """The optimizer drops an unreachable mutually-recursive letrec group."""

    def test_unreachable_group_removed(self):
        """A two-binding unreachable cycle is eliminated entirely."""
        lam_f = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("g", [_local("n")], tail=True)),
            sibling_free_vars=("g",),
            sibling_free_var_plans=(_local("g"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        lam_g = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="g",
        )
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[("f", lam_f), ("g", lam_g)],
            body_plan=_const(42),
            in_tail_position=True,
        ))
        result, changed = MenaiIROptimizer().optimize(ir)
        assert changed is True
        assert isinstance(result, MenaiIRReturn)
        inner = result.value_plan
        assert isinstance(inner, MenaiIRConstant)
        assert inner.value == MenaiInteger(42)

    def test_reachable_group_preserved(self):
        """A reachable mutually-recursive group is kept."""
        lam_f = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("g", [_local("n")], tail=True)),
            sibling_free_vars=("g",),
            sibling_free_var_plans=(_local("g"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="f",
        )
        lam_g = MenaiIRLambda(
            params=("n",),
            body_plan=MenaiIRReturn(value_plan=_user_call("f", [_local("n")], tail=True)),
            sibling_free_vars=("f",),
            sibling_free_var_plans=(_local("f"),),
            outer_free_vars=(),
            outer_free_var_plans=(),
            param_count=1,
            is_variadic=False,
            binding_name="g",
        )
        ir = MenaiIRReturn(value_plan=MenaiIRLetrec(
            bindings=[("f", lam_f), ("g", lam_g)],
            body_plan=_user_call("f", [_const(1)], tail=True),
            in_tail_position=True,
        ))
        result, _ = MenaiIROptimizer().optimize(ir)
        assert isinstance(result, MenaiIRReturn)
        inner = result.value_plan
        assert isinstance(inner, MenaiIRLetrec)
        assert len(inner.bindings) == 2


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestReachabilityIntegration:
    """End-to-end tests over compiled Menai source."""

    def test_unused_mutually_recursive_functions_are_pruned(self):
        """
        A program that does not use a mutually-recursive group compiles without
        emitting closures for it.

        The group is unreachable, so dead-binding elimination removes it and the
        module bytecode contains no closure for either function.
        """
        menai = Menai()
        code = menai.compile("""
            (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                     (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
              42)
        """)
        from menai.bytecode.menai_bytecode import Opcode, unpack_instruction

        opcodes = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.MAKE_CLOSURE not in opcodes, (
            f"Expected the unreachable group to be pruned, got "
            f"{[Opcode(o).name for o in opcodes]}"
        )
        assert menai.evaluate("""
            (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                     (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
              42)
        """) == 42

    def test_used_mutually_recursive_functions_still_work(self):
        """A reachable mutually-recursive group is preserved and correct."""
        result = Menai().evaluate("""
            (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                     (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
              (list (even? 10) (odd? 7)))
        """)
        assert result == [True, True]

    def test_unused_self_recursive_function_pruned(self):
        """A single unused self-recursive function is pruned."""
        menai = Menai()
        code = menai.compile("""
            (letrec ((count-down (lambda (n) (if (integer=? n 0) 0 (count-down (integer- n 1))))))
              7)
        """)
        from menai.bytecode.menai_bytecode import Opcode, unpack_instruction

        opcodes = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.MAKE_CLOSURE not in opcodes

    def test_prelude_cost_nothing_when_unused(self):
        """
        The prelude's regexp parser is an unreachable mutually-recursive group
        in a program that does not use it, so it is pruned: the compiled module
        contains no closure at all.
        """
        menai = Menai()
        code = menai.compile("(integer+ 3 4)")
        from menai.bytecode.menai_bytecode import Opcode, unpack_instruction

        opcodes = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.MAKE_CLOSURE not in opcodes, (
            f"Expected the whole prelude to be pruned, got "
            f"{[Opcode(o).name for o in opcodes]}"
        )

    def test_regexp_still_works(self):
        """The regexp prelude functions still behave correctly when used."""
        menai = Menai()
        result = menai.evaluate('(string-match "hello world" (regexp "wor.d"))')
        assert result == [6, 11]
