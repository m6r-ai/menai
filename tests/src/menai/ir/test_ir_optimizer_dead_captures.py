"""
Tests for dead capture pruning in MenaiIROptimizer.

When the IR inliner substitutes a lambda body at a call site, the capturing
lambda's free-var lists are not automatically updated.  The optimizer prunes
captures that are no longer referenced in the body, producing smaller closures
and faster closure setup.

Strategy
--------
1. Unit tests that construct IR trees by hand, run the optimizer, and verify
   that stale captures are removed while live captures are retained.

2. Integration tests that compile real Menai source and verify correct results
   and reduced capture counts.
"""

from __future__ import annotations

from menai import Menai
from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiBoolean, MenaiInteger, MenaiString


def _int_const(n: int) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiInteger(n))


def _str_const(s: str) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiString(s))


def _bool_const(b: bool) -> MenaiIRConstant:
    return MenaiIRConstant(value=MenaiBoolean(b))


def _local(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name, var_type='local')


def _global(name: str) -> MenaiIRVariable:
    return MenaiIRVariable(name=name, var_type='global')


def _builtin_call(name: str, args: list, tail: bool = False) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=_global('$' + name),
        arg_plans=args,
        is_tail_call=tail,
        is_builtin=True,
        builtin_name=name,
    )


def _user_call(func: MenaiIRVariable, args: list, tail: bool = False) -> MenaiIRCall:
    return MenaiIRCall(
        func_plan=func,
        arg_plans=args,
        is_tail_call=tail,
        is_builtin=False,
        builtin_name=None,
    )


def _run_opt(ir):
    """Run the optimizer and return (new_ir, changed)."""
    return MenaiIROptimizer().optimize(ir)


# ---------------------------------------------------------------------------
# Unit tests: stale outer free vars are pruned
# ---------------------------------------------------------------------------

class TestStaleOuterFreeVarPruning:
    """A lambda that captures an outer var which is no longer used in the body."""

    def test_single_stale_outer_free_var_pruned(self):
        """Lambda captures 'unused' and 'used'; only 'used' survives."""
        lam = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [_local('x'), _local('used')])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['used', 'unused'],
            outer_free_var_plans=[_local('used'), _local('unused')],
            param_count=1,
            is_variadic=False,
            binding_name='f',
        )
        ir = MenaiIRLet(
            bindings=[('f', lam)],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('f'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)
        assert changed is True

        # Find the lambda in the result (it may be inside a Let or directly the body)
        def find_lambda(node):
            if isinstance(node, MenaiIRLambda):
                return node

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v)
                    if r:
                        return r

                return find_lambda(node.body_plan)

            return None

        opt_lam = find_lambda(result)
        assert opt_lam is not None
        assert 'used' in opt_lam.outer_free_vars
        assert 'unused' not in opt_lam.outer_free_vars
        assert len(opt_lam.outer_free_var_plans) == 1

    def test_all_outer_free_vars_stale_pruned(self):
        """When all outer free vars are stale, the lists become empty."""
        lam = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [_local('x'), _int_const(1)])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['stale1', 'stale2'],
            outer_free_var_plans=[_local('stale1'), _local('stale2')],
            param_count=1,
            is_variadic=False,
            binding_name='f',
        )
        ir = MenaiIRLet(
            bindings=[('f', lam)],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('f'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)
        assert changed is True

        def find_lambda(node):
            if isinstance(node, MenaiIRLambda):
                return node

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v)
                    if r:
                        return r

                return find_lambda(node.body_plan)

            return None

        opt_lam = find_lambda(result)
        assert opt_lam is not None
        assert opt_lam.outer_free_vars == []
        assert opt_lam.outer_free_var_plans == []

    def test_no_pruning_when_all_captures_used(self):
        """No captures are pruned when all are referenced in the body."""
        lam = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [
                    _local('x'), _builtin_call('integer+', [_local('a'), _local('b')])
                ])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['a', 'b'],
            outer_free_var_plans=[_local('a'), _local('b')],
            param_count=1,
            is_variadic=False,
            binding_name='f',
        )
        ir = MenaiIRLet(
            bindings=[('f', lam)],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('f'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)
        assert changed is False

        def find_lambda(node):
            if isinstance(node, MenaiIRLambda):
                return node

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v)
                    if r:
                        return r

                return find_lambda(node.body_plan)

            return None

        opt_lam = find_lambda(result)
        assert opt_lam is not None
        assert opt_lam.outer_free_vars == ['a', 'b']
        assert len(opt_lam.outer_free_var_plans) == 2


# ---------------------------------------------------------------------------
# Unit tests: stale sibling free vars are pruned
# ---------------------------------------------------------------------------

class TestStaleSiblingFreeVarPruning:
    """A lambda in a letrec that captures a sibling which is no longer used."""

    def test_stale_sibling_free_var_pruned(self):
        """Letrec lambda captures 'sibling-used' and 'sibling-stale'; only used survives."""
        lam = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('sibling-used'), [_local('x')], tail=True)
            ),
            sibling_free_vars=['sibling-used', 'sibling-stale'],
            sibling_free_var_plans=[_local('sibling-used'), _local('sibling-stale')],
            outer_free_vars=[],
            outer_free_var_plans=[],
            param_count=1,
            is_variadic=False,
            binding_name='f',
        )
        sibling_used = MenaiIRLambda(
            params=['y'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [_local('y'), _int_const(1)])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=[],
            outer_free_var_plans=[],
            param_count=1,
            is_variadic=False,
            binding_name='sibling-used',
        )
        sibling_stale = MenaiIRLambda(
            params=['z'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [_local('z'), _int_const(2)])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=[],
            outer_free_var_plans=[],
            param_count=1,
            is_variadic=False,
            binding_name='sibling-stale',
        )
        ir = MenaiIRLetrec(
            bindings=[
                ('f', lam),
                ('sibling-used', sibling_used),
                ('sibling-stale', sibling_stale),
            ],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('f'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)
        assert changed is True

        def find_lambda(node, name):
            if isinstance(node, MenaiIRLambda):
                if node.binding_name == name:
                    return node

                return find_lambda(node.body_plan, name)

            if isinstance(node, MenaiIRLetrec):
                for _, v in node.bindings:
                    r = find_lambda(v, name)
                    if r:
                        return r

                return find_lambda(node.body_plan, name)

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v, name)
                    if r:
                        return r

                return find_lambda(node.body_plan, name)

            return None

        opt_f = find_lambda(result, 'f')
        assert opt_f is not None
        assert 'sibling-used' in opt_f.sibling_free_vars
        assert 'sibling-stale' not in opt_f.sibling_free_vars
        assert len(opt_f.sibling_free_var_plans) == 1


# ---------------------------------------------------------------------------
# Unit tests: nested lambda captures are respected
# ---------------------------------------------------------------------------

class TestNestedLambdaCapturesRespected:
    """A capture used only by a nested lambda must not be pruned."""

    def test_capture_used_by_nested_lambda_retained(self):
        """
        Outer lambda captures 'helper'; nested lambda captures it via
        outer_free_vars.  'helper' has its own capture ('offset') so the
        inliner will not inline it, keeping the nested lambda's reference
        to 'helper' live.
        """
        inner = MenaiIRLambda(
            params=['y'],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('helper'), [_local('y')], tail=True)
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['helper'],
            outer_free_var_plans=[_local('helper')],
            param_count=1,
            is_variadic=False,
            binding_name=None,
        )
        outer = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRReturn(
                value_plan=inner
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['helper'],
            outer_free_var_plans=[_local('helper')],
            param_count=1,
            is_variadic=False,
            binding_name='outer',
        )
        helper = MenaiIRLambda(
            params=['z'],
            body_plan=MenaiIRReturn(
                value_plan=_builtin_call('integer+', [_local('z'), _local('offset')])
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['offset'],
            outer_free_var_plans=[_local('offset')],
            param_count=1,
            is_variadic=False,
            binding_name='helper',
        )
        ir = MenaiIRLet(
            bindings=[('offset', _int_const(1)), ('helper', helper), ('outer', outer)],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('outer'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)

        def find_lambda(node, name):
            if isinstance(node, MenaiIRLambda):
                if node.binding_name == name:
                    return node

                return find_lambda(node.body_plan, name)

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v, name)
                    if r:
                        return r

                return find_lambda(node.body_plan, name)

            return None

        opt_outer = find_lambda(result, 'outer')
        assert opt_outer is not None
        assert 'helper' in opt_outer.outer_free_vars
        assert len(opt_outer.outer_free_var_plans) == 1


# ---------------------------------------------------------------------------
# Unit tests: shadowing is respected
# ---------------------------------------------------------------------------

class TestShadowingRespected:
    """A capture shadowed by an inner binding must not count as used."""

    def test_shadowed_capture_pruned(self):
        """A let binding shadows the capture; the capture is stale."""
        lam = MenaiIRLambda(
            params=['x'],
            body_plan=MenaiIRLet(
                bindings=[('captured', _int_const(99))],
                body_plan=MenaiIRReturn(
                    value_plan=_builtin_call('integer+', [_local('x'), _local('captured')])
                ),
                in_tail_position=True,
            ),
            sibling_free_vars=[],
            sibling_free_var_plans=[],
            outer_free_vars=['captured'],
            outer_free_var_plans=[_local('captured')],
            param_count=1,
            is_variadic=False,
            binding_name='f',
        )
        ir = MenaiIRLet(
            bindings=[('f', lam)],
            body_plan=MenaiIRReturn(
                value_plan=_user_call(_local('f'), [_int_const(42)], tail=True)
            ),
            in_tail_position=True,
        )
        result, changed = _run_opt(ir)
        assert changed is True

        def find_lambda(node):
            if isinstance(node, MenaiIRLambda):
                return node

            if isinstance(node, MenaiIRLet):
                for _, v in node.bindings:
                    r = find_lambda(v)
                    if r:
                        return r

                return find_lambda(node.body_plan)

            return None

        opt_lam = find_lambda(result)
        assert opt_lam is not None
        assert opt_lam.outer_free_vars == []
        assert opt_lam.outer_free_var_plans == []


# ---------------------------------------------------------------------------
# Integration tests: correct runtime semantics
# ---------------------------------------------------------------------------

class TestDeadCapturePruningIntegration:
    """End-to-end: programs with inlined functions must produce correct results."""

    def test_inlined_helper_produces_correct_result(self):
        """A helper that is inlined should not affect the result."""
        result = Menai().evaluate("""
            (letrec ((is-digit? (lambda (ch)
                                  (and (string>=? ch "0") (string<=? ch "9"))))
                     (scan (lambda (s i len)
                             (letrec ((loop (lambda (j)
                                              (if (integer>=? j len)
                                                  j
                                                  (if (or (string=? (string-ref s j) "-")
                                                          (is-digit? (string-ref s j)))
                                                      (loop (integer+ j 1))
                                                      j)))))
                               (loop i)))))
              (scan "123abc" 0 6))
        """)
        assert result == 3

    def test_inlined_function_correct_with_captures(self):
        """A function with captures that gets partially inlined works correctly."""
        result = Menai().evaluate("""
            (let ((base 10))
              (let ((add-base (lambda (x) (integer+ x base))))
                (let ((use-it (lambda (f y) (integer+ (f y) (f (integer+ y 1))))))
                  (use-it add-base 5))))
        """)
        assert result == 31

    def test_mutually_recursive_not_broken(self):
        """Mutually recursive functions with captures must still work."""
        result = Menai().evaluate("""
            (let ((limit 100))
              (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                       (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
                (even? 10)))
        """)
        assert result == True

    def test_json_parser_still_correct(self):
        """The JSON parser (the motivating example) must still parse correctly."""
        menai = Menai()
        result = menai.evaluate("""
            (letrec (
              (is-digit? (lambda (ch) (and (string>=? ch "0") (string<=? ch "9"))))
              (scan-number-end
               (lambda (s i len)
                 (letrec ((loop (lambda (j)
                                  (if (integer>=? j len)
                                      j
                                      (let ((ch (string-ref s j)))
                                        (if (or (string=? ch "-")
                                            (or (string=? ch "+")
                                            (or (string=? ch ".")
                                            (or (string=? ch "e")
                                            (or (string=? ch "E")
                                                (is-digit? ch))))))
                                            (loop (integer+ j 1))
                                            j))))))
                   (loop i))))
              (parse-number
               (lambda (s pos)
                 (let ((end (scan-number-end s pos (string-length s))))
                   (list (string->number (string-slice s pos end)) end))))
            )
              (parse-number "12345abc" 0))
        """)
        assert result[0] == 12345
        assert result[1] == 5
