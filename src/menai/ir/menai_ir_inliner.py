"""
Menai IR Inliner - function inlining at the IR level.

Walks the IR tree and substitutes lambda bodies at call sites where the
call target can be resolved to a known lambda.  Inlining is always safe in
Menai because the language is pure — there are no side effects to reorder.

The pass resolves two categories of call target:

1. Local lambdas bound in an enclosing let/letrec whose value is a
   MenaiIRLambda.  The scope stack maps binding names to lambda nodes as
   the tree is walked.

2. Prelude lambdas — functions compiled from the prelude source and made
   available via set_prelude_lambdas.  These are looked up by name when the
   call's func_plan is a global variable.

A lambda is only inlined when:

- Its body node count is at or below MAX_INLINE_NODES.
- It is not recursive (its binding name does not appear in its own body,
  and for letrec groups, none of the sibling names appear in the body).
- Its body does not contain a lambda that captures the inlined function's
  parameters via outer_free_vars.  Captures of sibling names or other
  internal bindings are safe because those bindings travel with the inlined
  body.
- The argument count matches the parameter count (arity must be exact).

The pass iterates to a fixed point: after each round of inlining, the
tree is walked again.  Inlining can expose new inlineable call sites (e.g.
inlining a wrapper reveals a call to another small function), so the pass
keeps going until no more inlining occurs.
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

        inliner = MenaiIRInliner(prelude_lambdas=lambdas)
        new_ir, changed = inliner.optimize(ir)
    """

    def __init__(self, prelude_lambdas: dict[str, MenaiIRLambda] | None = None) -> None:
        self._prelude_lambdas: dict[str, MenaiIRLambda] = prelude_lambdas or {}
        self._inlined = 0

    def set_prelude_lambdas(self, lambdas: dict[str, MenaiIRLambda]) -> None:
        """Update the prelude lambda map (called after prelude compilation)."""
        self._prelude_lambdas = lambdas

    def optimize(self, ir: MenaiIRExpr) -> tuple[MenaiIRExpr, bool]:
        """Return an inlined IR tree and a boolean indicating whether any changes were made."""
        self._inlined = 0
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
        scope_stack: list[dict[str, MenaiIRLambda]],
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
            return self._opt_lambda(ir, scope_stack)

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
        scope_stack: list[dict[str, MenaiIRLambda]],
        letrec_names: set[str],
    ) -> MenaiIRExpr:
        """Walk a let, adding lambda bindings to the scope for the body."""
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        new_scope: dict[str, MenaiIRLambda] = {}

        for name, value_plan in ir.bindings:
            opt_value = self._opt(value_plan, scope_stack, letrec_names)
            if isinstance(opt_value, MenaiIRLambda):
                new_scope[name] = opt_value

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
        scope_stack: list[dict[str, MenaiIRLambda]],
    ) -> MenaiIRExpr:
        """Walk a letrec, adding lambda bindings to the scope for the body."""
        names = {name for name, _ in ir.bindings}
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        new_scope: dict[str, MenaiIRLambda] = {}

        for name, value_plan in ir.bindings:
            opt_value = self._opt(value_plan, scope_stack, names)
            if isinstance(opt_value, MenaiIRLambda):
                new_scope[name] = opt_value

            opt_bindings.append((name, opt_value))

        child_stack = scope_stack + [new_scope]
        opt_body = self._opt(ir.body_plan, child_stack, set())

        return MenaiIRLetrec(
            bindings=opt_bindings,
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    def _opt_if(
        self,
        ir: MenaiIRIf,
        scope_stack: list[dict[str, MenaiIRLambda]],
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
        scope_stack: list[dict[str, MenaiIRLambda]],
    ) -> MenaiIRLambda:
        """Walk a lambda body in a fresh scope (params shadow outer bindings)."""
        param_scope = {name: ir for name in ir.params}
        child_stack = scope_stack + [param_scope]
        return MenaiIRLambda(
            params=ir.params,
            body_plan=self._opt(ir.body_plan, child_stack, set()),
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
        scope_stack: list[dict[str, MenaiIRLambda]],
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
        scope_stack: list[dict[str, MenaiIRLambda]],
    ) -> MenaiIRLambda | None:
        """Resolve a call target to a MenaiIRLambda, or None if not resolvable."""
        if not isinstance(func_plan, MenaiIRVariable):
            return None

        if func_plan.var_type == 'local':
            for scope in reversed(scope_stack):
                if func_plan.name in scope:
                    return scope[func_plan.name]

            return None

        if func_plan.var_type == 'global':
            return self._prelude_lambdas.get(func_plan.name)

        return None

    def _is_inlineable(
        self,
        target: MenaiIRLambda,
        func_plan: MenaiIRExpr,
        letrec_names: set[str],
        arg_count: int,
    ) -> bool:
        """Check whether a lambda is eligible for inlining."""
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

        if target.is_variadic:
            min_arity = target.param_count - 1
            if arg_count < min_arity:
                return False

        else:
            if arg_count != target.param_count:
                return False

        if target.sibling_free_vars or target.outer_free_vars:
            return False

        if isinstance(func_plan, MenaiIRVariable) and func_plan.var_type == 'local':
            if target.binding_name is not None and target.binding_name in letrec_names:
                return False

        if isinstance(func_plan, MenaiIRVariable) and func_plan.var_type == 'global':
            name = func_plan.name
            try:
                found = _name_in_tree(target.body_plan, name)

            except RecursionError:
                return False

            if found:
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
        """
        param_map: dict[str, MenaiIRExpr] = {}

        if target.is_variadic:
            min_arity = target.param_count - 1
            for i in range(min_arity):
                param_map[target.params[i]] = arg_plans[i]

            rest_args = arg_plans[min_arity:]
            param_map[target.params[min_arity]] = MenaiIRBuildList(element_plans=rest_args)

        else:
            for i, param in enumerate(target.params):
                if i < len(arg_plans):
                    param_map[param] = arg_plans[i]

        body = _substitute(target.body_plan, param_map, set())

        if isinstance(body, MenaiIRReturn):
            if is_tail_call:
                return body

            return body.value_plan

        if is_tail_call:
            return MenaiIRReturn(value_plan=body)

        return body


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

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_has_captures_of_params(f, params) for f in ir.field_plans)

    return False


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

    if isinstance(ir, MenaiIRBuildStruct):
        return 1 + sum(_count_nodes(f) for f in ir.field_plans)

    raise TypeError(f"_count_nodes: unhandled IR node type {type(ir).__name__}")


def _name_in_tree(ir: MenaiIRExpr, name: str) -> bool:
    """Check whether a variable name appears anywhere in the IR tree."""
    if isinstance(ir, MenaiIRVariable):
        return ir.name == name

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _name_in_tree(ir.message, name)

    if isinstance(ir, MenaiIRIf):
        return (_name_in_tree(ir.condition_plan, name)
                or _name_in_tree(ir.then_plan, name)
                or _name_in_tree(ir.else_plan, name))

    if isinstance(ir, MenaiIRLet):
        return (any(_name_in_tree(v, name) for _, v in ir.bindings)
                or _name_in_tree(ir.body_plan, name))

    if isinstance(ir, MenaiIRLetrec):
        return (any(_name_in_tree(v, name) for _, v in ir.bindings)
                or _name_in_tree(ir.body_plan, name))

    if isinstance(ir, MenaiIRLambda):
        return _name_in_tree(ir.body_plan, name)

    if isinstance(ir, MenaiIRCall):
        return _name_in_tree(ir.func_plan, name) or any(_name_in_tree(a, name) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _name_in_tree(ir.value_plan, name)

    if isinstance(ir, MenaiIRBuildList):
        return any(_name_in_tree(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_name_in_tree(k, name) or _name_in_tree(v, name) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_name_in_tree(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_name_in_tree(f, name) for f in ir.field_plans)

    return False


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
        if ir.var_type == 'local' and ir.name in param_map and ir.name not in shadowed:
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
