"""
Menai IR Inliner - function inlining at the IR level.

Walks the IR tree and substitutes lambda bodies at call sites where the
call target can be resolved to a known lambda.  Inlining is always safe in
Menai because the language is pure — there are no side effects to reorder.

The pass resolves two kinds of call target:

- A lambda bound in an enclosing let/letrec whose value is a MenaiIRLambda.
  The scope stack maps binding names to lambda nodes as the tree is walked.
  A lambda parameter is entered into the scope with a None value: it shadows
  any outer binding of the same name but resolves to no target, because a
  parameter is a runtime value rather than a statically-known lambda.
  Prelude functions are ordinary bindings in that scope stack: the prelude is
  spliced into every program as a letrec, so its functions are resolved the
  same way as any other local lambda.

- A direct lambda application, where the function position is itself a lambda
  (e.g. ((lambda (x) ...) arg)).  The lambda is written at the call site, so
  inlining it there removes a closure allocation and an indirect call without
  changing any scope.

A lambda is only inlined when:

- Its body node count is at or below MAX_INLINE_NODES.
- It is not recursive (its binding name does not appear in its own body,
  and for letrec groups, none of the sibling names appear in the body).
- Its body does not contain a lambda that captures the inlined function's
  parameters via outer_free_vars.  Captures of sibling names or other
  internal bindings are safe because those bindings travel with the inlined
  body.
- The argument count matches the parameter count (arity must be exact).
- A lambda bound elsewhere has no captures.  A direct lambda application is
  exempt from this: it is inlined at its own definition site, so its captures
  remain in scope after substitution.

The pass iterates to a fixed point: after each round of inlining, the
tree is walked again.  Inlining can expose new inlineable call sites (e.g.
inlining a wrapper reveals a call to another small function), so the pass
keeps going until no more inlining occurs.

When substituting, a parameter that occurs more than once in the callee body
and is bound to a non-trivial argument is first bound to a fresh let variable,
and the parameter references are substituted with that variable.  Without this,
substituting the argument expression at every occurrence would duplicate the
argument's computation once per occurrence (e.g. a callee that tests its
parameter in several branches would recompute an expensive argument in every
branch).  Binding the argument once and reusing the variable keeps the
computation to a single evaluation.  A parameter bound to a variable or a
constant is left alone: such an argument is already a single register read or
constant load, so duplicating it costs nothing.
"""

import sys

from menai.ir.menai_ir import (
    MenaiIRExpr,
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRBuildStruct,
    MenaiIRBuildList,
    MenaiIRBuildDict,
    MenaiIRBuildSet,
    MenaiIRBuildVector,
    MenaiIREmptyList,
    MenaiIRError,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRQuote,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass


MAX_INLINE_NODES = 50

# The recursive tree walks in this pass can go deep on large IR trees.
# Ensure the recursion limit is high enough to handle them.
if sys.getrecursionlimit() < 5000:
    sys.setrecursionlimit(5000)


class MenaiIRInliner(MenaiIROptimizationPass):
    """
    IR-level function inlining pass.

    Usage::

        inliner = MenaiIRInliner()
        new_ir, changed = inliner.optimize(ir)
    """

    def __init__(self) -> None:
        self._inlined = 0
        self._temp_counter = 0

    def _gen_temp(self) -> str:
        """
        Generate a unique temporary variable name for an inlined argument.

        Uses the compiler-generated ``#:`` prefix so the name cannot collide
        with a user identifier.  The counter is per-instance and monotonically
        increasing, so repeated inlining never reuses a name.
        """
        self._temp_counter += 1
        return f"#:inline-tmp-{self._temp_counter}"

    def optimize(self, ir: MenaiIRExpr) -> tuple[MenaiIRExpr, bool]:
        """Return an inlined IR tree and a boolean indicating whether any changes were made."""
        self._inlined = 0
        self._temp_counter = 0
        new_ir = ir
        while True:
            prev = self._inlined
            new_ir = self._opt(new_ir, scope_stack=[{}], letrec_names=set())
            if self._inlined == prev:
                break

        return new_ir, self._inlined > 0

    def _opt(
        self,
        ir: MenaiIRExpr,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
        letrec_names: set[str],
    ) -> MenaiIRExpr:
        """Recursively walk the IR tree and inline eligible call sites."""
        if isinstance(ir, MenaiIRLet):
            return self._opt_let(ir, scope_stack, letrec_names)

        if isinstance(ir, MenaiIRLetrec):
            return self._opt_letrec(ir, scope_stack)

        if isinstance(ir, MenaiIRIf):
            return self._opt_if(ir, scope_stack, letrec_names)

        if isinstance(ir, MenaiIRLambda):
            return self._opt_lambda(ir, scope_stack, letrec_names)

        if isinstance(ir, MenaiIRCall):
            return self._opt_call(ir, scope_stack, letrec_names)

        if isinstance(ir, MenaiIRReturn):
            return MenaiIRReturn(
                value_plan=self._opt(ir.value_plan, scope_stack, letrec_names),
            )

        if isinstance(ir, MenaiIRBuildList):
            return MenaiIRBuildList(
                element_plans=[self._opt(e, scope_stack, letrec_names) for e in ir.element_plans],
            )

        if isinstance(ir, MenaiIRBuildDict):
            return MenaiIRBuildDict(
                pair_plans=[(self._opt(k, scope_stack, letrec_names), self._opt(v, scope_stack, letrec_names))
                            for k, v in ir.pair_plans],
            )

        if isinstance(ir, MenaiIRBuildSet):
            return MenaiIRBuildSet(
                element_plans=[self._opt(e, scope_stack, letrec_names) for e in ir.element_plans],
            )

        if isinstance(ir, MenaiIRBuildVector):
            return MenaiIRBuildVector(
                element_plans=[self._opt(e, scope_stack, letrec_names) for e in ir.element_plans],
            )

        if isinstance(ir, MenaiIRBuildStruct):
            return MenaiIRBuildStruct(
                struct_type=ir.struct_type,
                field_plans=[self._opt(f, scope_stack, letrec_names) for f in ir.field_plans],
            )

        if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList, MenaiIRError)):
            return ir

        raise TypeError(f"MenaiIRInliner: unhandled IR node type {type(ir).__name__}")

    def _opt_let(
        self,
        ir: MenaiIRLet,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
        letrec_names: set[str],
    ) -> MenaiIRExpr:
        """
        Walk a let, adding its bindings to the scope for the body.

        Every binding name is entered into the scope so that it shadows any
        outer binding of the same name.  A binding whose value is a lambda maps
        to that lambda; any other value maps to None, meaning the name is bound
        but is not a statically-known lambda and so resolves to no target.
        """
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        new_scope: dict[str, MenaiIRLambda | None] = {}

        for name, value_plan in ir.bindings:
            opt_value = self._opt(value_plan, scope_stack, letrec_names)
            new_scope[name] = opt_value if isinstance(opt_value, MenaiIRLambda) else None

            opt_bindings.append((name, opt_value))

        child_stack = scope_stack + [new_scope]
        opt_body = self._opt(ir.body_plan, child_stack, set())

        return MenaiIRLet(
            bindings=opt_bindings,
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    def _opt_letrec(
        self,
        ir: MenaiIRLetrec,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
    ) -> MenaiIRExpr:
        """
        Walk a letrec, adding its bindings to the scope for the body.

        Every binding name is entered into the scope so that it shadows any
        outer binding of the same name.  A binding whose value is a lambda maps
        to that lambda; any other value maps to None, meaning the name is bound
        but is not a statically-known lambda and so resolves to no target.
        """
        names = {name for name, _ in ir.bindings}
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        new_scope: dict[str, MenaiIRLambda | None] = {}

        for name, value_plan in ir.bindings:
            opt_value = self._opt(value_plan, scope_stack, names)
            new_scope[name] = opt_value if isinstance(opt_value, MenaiIRLambda) else None

            opt_bindings.append((name, opt_value))

        child_stack = scope_stack + [new_scope]
        opt_body = self._opt(ir.body_plan, child_stack, names)

        return MenaiIRLetrec(
            bindings=opt_bindings,
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    def _opt_if(
        self,
        ir: MenaiIRIf,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
        letrec_names: set[str],
    ) -> MenaiIRExpr:
        """Optimize the branches of an if-expression."""
        return MenaiIRIf(
            condition_plan=self._opt(ir.condition_plan, scope_stack, letrec_names),
            then_plan=self._opt(ir.then_plan, scope_stack, letrec_names),
            else_plan=self._opt(ir.else_plan, scope_stack, letrec_names),
            in_tail_position=ir.in_tail_position,
        )

    def _opt_lambda(
        self,
        ir: MenaiIRLambda,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
        letrec_names: set[str],
    ) -> MenaiIRLambda:
        """
        Walk a lambda body in a fresh scope (params shadow outer bindings).

        The enclosing letrec's binding names are carried through the body walk,
        so a call to a letrec sibling inside the lambda body is recognised as a
        recursive call and is not inlined.

        Each parameter is entered into the scope as a non-resolvable binding.
        A parameter is a runtime value, not a statically-known lambda, so a call
        whose function position is a parameter must not resolve to anything.
        Entering the name with a None value makes it shadow any outer binding of
        the same name while resolving to no target.
        """
        param_scope: dict[str, MenaiIRLambda | None] = {name: None for name in ir.params}
        child_stack = scope_stack + [param_scope]
        return MenaiIRLambda(
            params=ir.params,
            body_plan=self._opt(ir.body_plan, child_stack, letrec_names),
            sibling_free_vars=ir.sibling_free_vars,
            sibling_free_var_plans=ir.sibling_free_var_plans,
            outer_free_vars=ir.outer_free_vars,
            outer_free_var_plans=ir.outer_free_var_plans,
            param_count=ir.param_count,
            is_variadic=ir.is_variadic,
            binding_name=ir.binding_name,
            source_line=ir.source_line,
            source_file=ir.source_file,
        )

    def _opt_call(
        self,
        ir: MenaiIRCall,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
        letrec_names: set[str],
    ) -> MenaiIRExpr:
        """Try to inline a call site; fall back to optimizing arguments."""
        target = None if ir.is_builtin else self._resolve_target(ir.func_plan, scope_stack)

        if target is not None and self._is_inlineable(target, ir.func_plan, letrec_names, len(ir.arg_plans)):
            inlined = self._inline(target, ir.arg_plans, ir.is_tail_call)
            self._inlined += 1
            return self._opt(inlined, scope_stack, letrec_names)

        return MenaiIRCall(
            func_plan=self._opt(ir.func_plan, scope_stack, letrec_names),
            arg_plans=[self._opt(a, scope_stack, letrec_names) for a in ir.arg_plans],
            is_tail_call=ir.is_tail_call,
            is_builtin=ir.is_builtin,
            builtin_name=ir.builtin_name,
        )

    def _resolve_target(
        self,
        func_plan: MenaiIRExpr,
        scope_stack: list[dict[str, MenaiIRLambda | None]],
    ) -> MenaiIRLambda | None:
        """
        Resolve a call target to a MenaiIRLambda, or None if not resolvable.

        A direct lambda application — a call whose function position is itself a
        lambda, as in ((lambda (x) ...) arg) — resolves to that lambda.  The
        lambda is written at the call site, so inlining it there changes no
        scoping: its captures are resolved against the enclosing scope at that
        exact position and remain in scope after substitution.

        A variable resolves to the lambda bound to that name in the innermost
        scope that binds it.  A scope entry may be None, meaning the name is
        bound there but is not a statically-known lambda (a lambda parameter).
        Such a name shadows any outer binding of the same name and resolves to
        no target, so the search stops at the innermost binding.
        """
        if isinstance(func_plan, MenaiIRLambda):
            return func_plan

        if not isinstance(func_plan, MenaiIRVariable):
            return None

        for scope in reversed(scope_stack):
            if func_plan.name in scope:
                return scope[func_plan.name]

        return None

    def _is_inlineable(
        self,
        target: MenaiIRLambda,
        func_plan: MenaiIRExpr,
        letrec_names: set[str],
        arg_count: int,
    ) -> bool:
        """Check whether a lambda is eligible for inlining."""
        # Cheap field checks first, so a candidate that fails one of them is
        # rejected without any tree walk.  Most candidates fail here.
        if target.is_variadic:
            min_arity = target.param_count - 1
            if arg_count < min_arity:
                return False

        else:
            if arg_count != target.param_count:
                return False

        # A lambda bound elsewhere is inlined at a call site that may be in a
        # different scope from where it was defined, so a capture could become
        # stale after substitution; reject those.  A direct lambda application
        # is inlined at its own definition site, so its captures stay in scope
        # and the check does not apply.
        if not isinstance(func_plan, MenaiIRLambda):
            if target.sibling_free_vars or target.outer_free_vars:
                return False

        if isinstance(func_plan, MenaiIRVariable):
            if target.binding_name is not None and target.binding_name in letrec_names:
                return False

        # Expensive tree walks last, only for candidates that survived the
        # field checks above.
        try:
            node_count = _count_nodes(target.body_plan)

        except RecursionError:
            return False

        if node_count > MAX_INLINE_NODES:
            return False

        if _has_captures_of_params(target.body_plan, set(target.params)):
            return False

        if _contains_letrec(target.body_plan):
            return False

        return True

    def _inline(
        self,
        target: MenaiIRLambda,
        arg_plans: list[MenaiIRExpr],
        is_tail_call: bool,
    ) -> MenaiIRExpr:
        """
        Substitute the lambda body at the call site.

        Parameters are replaced with the corresponding argument expressions.
        MenaiIRReturn wrappers are handled: if the body is wrapped in Return
        and the call is a tail call, the wrapper is preserved; otherwise the
        inner value is used directly.

        A parameter that occurs more than once in the callee body and is bound
        to a non-trivial argument is bound to a fresh let variable first, and
        the parameter references are substituted with that variable.  This
        avoids duplicating the argument's computation once per occurrence.
        """
        param_map: dict[str, MenaiIRExpr] = {}
        captured_bindings: list[tuple[str, MenaiIRExpr]] = []

        if target.is_variadic:
            min_arity = target.param_count - 1
            for i in range(min_arity):
                param_map[target.params[i]] = self._bind_argument(
                    target.params[i], arg_plans[i], target.body_plan, captured_bindings
                )

            rest_args = arg_plans[min_arity:]
            param_map[target.params[min_arity]] = self._bind_argument(
                target.params[min_arity],
                MenaiIRBuildList(element_plans=rest_args),
                target.body_plan,
                captured_bindings,
            )

        else:
            for i, param in enumerate(target.params):
                if i < len(arg_plans):
                    param_map[param] = self._bind_argument(
                        param, arg_plans[i], target.body_plan, captured_bindings
                    )

        body = _substitute(target.body_plan, param_map, set())

        result: MenaiIRExpr
        if isinstance(body, MenaiIRReturn):
            if is_tail_call:
                result = body

            else:
                result = body.value_plan

        elif is_tail_call:
            result = MenaiIRReturn(value_plan=body)

        else:
            result = body

        if captured_bindings:
            return MenaiIRLet(
                bindings=captured_bindings,
                body_plan=result,
                in_tail_position=is_tail_call,
            )

        return result

    def _bind_argument(
        self,
        param: str,
        arg_plan: MenaiIRExpr,
        body_plan: MenaiIRExpr,
        captured_bindings: list[tuple[str, MenaiIRExpr]],
    ) -> MenaiIRExpr:
        """
        Return the plan to substitute for *param*.

        A non-trivial argument bound to a parameter that occurs more than once
        in the callee body is hoisted into a fresh let binding, and the returned
        plan is a reference to that binding.  Any other argument is returned
        unchanged.
        """
        if isinstance(arg_plan, (MenaiIRVariable, MenaiIRConstant)):
            return arg_plan

        if _count_param_uses(body_plan, param, set()) <= 1:
            return arg_plan

        temp_name = self._gen_temp()
        captured_bindings.append((temp_name, arg_plan))
        return MenaiIRVariable(name=temp_name)


def _contains_letrec(ir: MenaiIRExpr) -> bool:
    """Check whether the IR tree contains a MenaiIRLetrec node."""
    if isinstance(ir, MenaiIRLetrec):
        return True

    if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _contains_letrec(ir.message)

    if isinstance(ir, MenaiIRIf):
        return (_contains_letrec(ir.condition_plan)
                or _contains_letrec(ir.then_plan)
                or _contains_letrec(ir.else_plan))

    if isinstance(ir, MenaiIRLet):
        return (any(_contains_letrec(v) for _, v in ir.bindings)
                or _contains_letrec(ir.body_plan))

    if isinstance(ir, MenaiIRCall):
        return _contains_letrec(ir.func_plan) or any(_contains_letrec(a) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _contains_letrec(ir.value_plan)

    if isinstance(ir, MenaiIRBuildList):
        return any(_contains_letrec(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_contains_letrec(k) or _contains_letrec(v) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_contains_letrec(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return any(_contains_letrec(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_contains_letrec(f) for f in ir.field_plans)

    return False


def _has_captures_of_params(ir: MenaiIRExpr, params: set[str]) -> bool:
    """
    Check whether the IR tree contains a lambda whose outer_free_vars
    overlap with the inlined function's parameter names.

    When a function is inlined, its parameters are substituted with argument
    expressions.  If a nested lambda captures one of those parameters via
    outer_free_vars, the capture becomes stale after substitution — the lambda
    still lists the parameter name as a capture, but the variable no longer
    exists in the enclosing scope.

    A lambda that captures only sibling names (from its own letrec group) or
    other non-parameter bindings is safe — those bindings travel with the
    inlined body unchanged.
    """
    if isinstance(ir, MenaiIRLambda):
        if set(ir.outer_free_vars) & params:
            return True

        return _has_captures_of_params(ir.body_plan, params)

    if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _has_captures_of_params(ir.message, params)

    if isinstance(ir, MenaiIRIf):
        return (_has_captures_of_params(ir.condition_plan, params)
                or _has_captures_of_params(ir.then_plan, params)
                or _has_captures_of_params(ir.else_plan, params))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (any(_has_captures_of_params(v, params) for _, v in ir.bindings)
                or _has_captures_of_params(ir.body_plan, params))

    if isinstance(ir, MenaiIRCall):
        return _has_captures_of_params(ir.func_plan, params) or any(_has_captures_of_params(a, params) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _has_captures_of_params(ir.value_plan, params)

    if isinstance(ir, MenaiIRBuildList):
        return any(_has_captures_of_params(e, params) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_has_captures_of_params(k, params) or _has_captures_of_params(v, params) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_has_captures_of_params(e, params) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return any(_has_captures_of_params(e, params) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_has_captures_of_params(f, params) for f in ir.field_plans)

    return False


def _count_param_uses(ir: MenaiIRExpr, param: str, shadowed: set[str]) -> int:
    """
    Count how many times *param* is referenced in *ir*.

    Mirrors the shadowing rules of _substitute exactly, so the count matches
    the number of occurrences that substitution would replace.  A reference is
    counted only when it is not shadowed by an inner let/letrec/lambda binder.

    Nested lambda capture plans (sibling_free_var_plans / outer_free_var_plans)
    are evaluated in the enclosing scope but are not substituted into, so they
    are not counted here either.
    """
    if isinstance(ir, MenaiIRVariable):
        return 1 if ir.name == param and ir.name not in shadowed else 0

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
        return 0

    if isinstance(ir, MenaiIRError):
        return _count_param_uses(ir.message, param, shadowed)

    if isinstance(ir, MenaiIRIf):
        return (_count_param_uses(ir.condition_plan, param, shadowed)
                + _count_param_uses(ir.then_plan, param, shadowed)
                + _count_param_uses(ir.else_plan, param, shadowed))

    if isinstance(ir, MenaiIRLet):
        binding_names = {name for name, _ in ir.bindings}
        return (sum(_count_param_uses(v, param, shadowed) for _, v in ir.bindings)
                + _count_param_uses(ir.body_plan, param, shadowed | binding_names))

    if isinstance(ir, MenaiIRLetrec):
        binding_names = {name for name, _ in ir.bindings}
        inner = shadowed | binding_names
        return (sum(_count_param_uses(v, param, inner) for _, v in ir.bindings)
                + _count_param_uses(ir.body_plan, param, inner))

    if isinstance(ir, MenaiIRLambda):
        inner = shadowed | set(ir.params) | set(ir.sibling_free_vars) | set(ir.outer_free_vars)
        return _count_param_uses(ir.body_plan, param, inner)

    if isinstance(ir, MenaiIRCall):
        return (_count_param_uses(ir.func_plan, param, shadowed)
                + sum(_count_param_uses(a, param, shadowed) for a in ir.arg_plans))

    if isinstance(ir, MenaiIRReturn):
        return _count_param_uses(ir.value_plan, param, shadowed)

    if isinstance(ir, MenaiIRBuildList):
        return sum(_count_param_uses(e, param, shadowed) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return sum(_count_param_uses(k, param, shadowed) + _count_param_uses(v, param, shadowed)
                   for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return sum(_count_param_uses(e, param, shadowed) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return sum(_count_param_uses(e, param, shadowed) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return sum(_count_param_uses(f, param, shadowed) for f in ir.field_plans)

    raise TypeError(f"_count_param_uses: unhandled IR node type {type(ir).__name__}")


def _count_nodes(ir: MenaiIRExpr) -> int:
    """Count the number of IR nodes in a tree."""
    if isinstance(ir, MenaiIRConstant):
        return 1

    if isinstance(ir, MenaiIRVariable):
        return 1

    if isinstance(ir, MenaiIRQuote):
        return 1

    if isinstance(ir, MenaiIREmptyList):
        return 1

    if isinstance(ir, MenaiIRError):
        return 1 + _count_nodes(ir.message)

    if isinstance(ir, MenaiIRIf):
        return 1 + _count_nodes(ir.condition_plan) + _count_nodes(ir.then_plan) + _count_nodes(ir.else_plan)

    if isinstance(ir, MenaiIRLet):
        return 1 + sum(_count_nodes(v) for _, v in ir.bindings) + _count_nodes(ir.body_plan)

    if isinstance(ir, MenaiIRLetrec):
        return 1 + sum(_count_nodes(v) for _, v in ir.bindings) + _count_nodes(ir.body_plan)

    if isinstance(ir, MenaiIRLambda):
        return 1 + _count_nodes(ir.body_plan)

    if isinstance(ir, MenaiIRCall):
        return 1 + _count_nodes(ir.func_plan) + sum(_count_nodes(a) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return 1 + _count_nodes(ir.value_plan)

    if isinstance(ir, MenaiIRBuildList):
        return 1 + sum(_count_nodes(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return 1 + sum(_count_nodes(k) + _count_nodes(v) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return 1 + sum(_count_nodes(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return 1 + sum(_count_nodes(e) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return 1 + sum(_count_nodes(f) for f in ir.field_plans)

    raise TypeError(f"_count_nodes: unhandled IR node type {type(ir).__name__}")


def _substitute(
    ir: MenaiIRExpr,
    param_map: dict[str, MenaiIRExpr],
    shadowed: set[str],
) -> MenaiIRExpr:
    """
    Substitute parameter references with argument expressions.

    shadowed tracks names that are bound by an inner let/letrec/lambda and
    should not be substituted.
    """
    if isinstance(ir, MenaiIRVariable):
        if ir.name in param_map and ir.name not in shadowed:
            return param_map[ir.name]

        return ir

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
        return ir

    if isinstance(ir, MenaiIRError):
        return MenaiIRError(message=_substitute(ir.message, param_map, shadowed))

    if isinstance(ir, MenaiIRIf):
        return MenaiIRIf(
            condition_plan=_substitute(ir.condition_plan, param_map, shadowed),
            then_plan=_substitute(ir.then_plan, param_map, shadowed),
            else_plan=_substitute(ir.else_plan, param_map, shadowed),
            in_tail_position=ir.in_tail_position,
        )

    if isinstance(ir, MenaiIRLet):
        return _substitute_let(ir, param_map, shadowed, is_letrec=False)

    if isinstance(ir, MenaiIRLetrec):
        return _substitute_let(ir, param_map, shadowed, is_letrec=True)

    if isinstance(ir, MenaiIRLambda):
        new_shadowed = shadowed | set(ir.params) | set(ir.sibling_free_vars) | set(ir.outer_free_vars)
        return MenaiIRLambda(
            params=ir.params,
            body_plan=_substitute(ir.body_plan, param_map, new_shadowed),
            sibling_free_vars=ir.sibling_free_vars,
            sibling_free_var_plans=ir.sibling_free_var_plans,
            outer_free_vars=ir.outer_free_vars,
            outer_free_var_plans=ir.outer_free_var_plans,
            param_count=ir.param_count,
            is_variadic=ir.is_variadic,
            binding_name=ir.binding_name,
            source_line=ir.source_line,
            source_file=ir.source_file,
        )

    if isinstance(ir, MenaiIRCall):
        return MenaiIRCall(
            func_plan=_substitute(ir.func_plan, param_map, shadowed),
            arg_plans=[_substitute(a, param_map, shadowed) for a in ir.arg_plans],
            is_tail_call=ir.is_tail_call,
            is_builtin=ir.is_builtin,
            builtin_name=ir.builtin_name,
        )

    if isinstance(ir, MenaiIRReturn):
        return MenaiIRReturn(
            value_plan=_substitute(ir.value_plan, param_map, shadowed),
        )

    if isinstance(ir, MenaiIRBuildList):
        return MenaiIRBuildList(
            element_plans=[_substitute(e, param_map, shadowed) for e in ir.element_plans],
        )

    if isinstance(ir, MenaiIRBuildDict):
        return MenaiIRBuildDict(
            pair_plans=[(_substitute(k, param_map, shadowed), _substitute(v, param_map, shadowed))
                        for k, v in ir.pair_plans],
        )

    if isinstance(ir, MenaiIRBuildSet):
        return MenaiIRBuildSet(
            element_plans=[_substitute(e, param_map, shadowed) for e in ir.element_plans],
        )

    if isinstance(ir, MenaiIRBuildVector):
        return MenaiIRBuildVector(
            element_plans=[_substitute(e, param_map, shadowed) for e in ir.element_plans],
        )

    if isinstance(ir, MenaiIRBuildStruct):
        return MenaiIRBuildStruct(
            struct_type=ir.struct_type,
            field_plans=[_substitute(f, param_map, shadowed) for f in ir.field_plans],
        )

    raise TypeError(f"_substitute: unhandled IR node type {type(ir).__name__}")


def _substitute_let(
    ir: MenaiIRLet | MenaiIRLetrec,
    param_map: dict[str, MenaiIRExpr],
    shadowed: set[str],
    is_letrec: bool,
) -> MenaiIRExpr:
    """Substitute inside a let/letrec, adding binding names to the shadowed set for the body."""
    binding_names = {name for name, _ in ir.bindings}

    values_shadowed = shadowed | binding_names if is_letrec else shadowed
    opt_bindings = [
        (name, _substitute(value, param_map, values_shadowed))
        for name, value in ir.bindings
    ]

    if is_letrec:
        opt_body = _substitute(ir.body_plan, param_map, shadowed | binding_names)
        return MenaiIRLetrec(
            bindings=opt_bindings,
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    opt_body = _substitute(ir.body_plan, param_map, shadowed | binding_names)
    return MenaiIRLet(
        bindings=opt_bindings,
        body_plan=opt_body,
        in_tail_position=ir.in_tail_position,
    )
