"""
Menai IR Optimizer - transformation pass over the IR tree.

Consumes an IRUseCounts annotation (produced by MenaiIRUseCounter) and an
IRReachability annotation (produced by MenaiIRReachability) and applies IR-level
optimizations that are safe because Menai is a pure functional language — every
binding is immutable and every expression is side-effect-free.
"""

from typing import cast

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
    MenaiIRLoop,
    MenaiIRRecur,
    MenaiIRQuote,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.menai_value import MenaiBoolean
from menai.ir.menai_ir_reachability import MenaiIRReachability, IRReachability
from menai.ir.menai_ir_use_counter import MenaiIRUseCounter, IRUseCounts
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass


def _same_sequence(new: tuple, old: tuple) -> bool:
    """
    Return True if two child sequences are element-wise identical by identity.

    Used by the optimizer's smart constructors: a rebuilt node whose children
    are all the same objects as before is unchanged, so the original node can be
    returned instead of a fresh copy.  Identity is the right test because the
    optimizer returns the input object itself for any subtree it did not change.
    """
    return len(new) == len(old) and all(a is b for a, b in zip(new, old))


class MenaiIROptimizer(MenaiIROptimizationPass):
    """
    IR-level optimization pass.

    Implements MenaiIROptimizationPass: call optimize(ir) to get back a
    transformed IR tree and a boolean indicating whether any changes were made.
    Use counts and reachability are computed internally so callers do not need
    to manage them.

    Usage::

        new_ir, changed = MenaiIROptimizer().optimize(ir)
    """

    def __init__(self) -> None:
        self._eliminations = 0
        self._counts: IRUseCounts | None = None
        self._reach: IRReachability | None = None

    def eliminations(self) -> int:
        """Return the number of eliminations performed by the last optimize() call."""
        return self._eliminations

    def optimize(self, ir: MenaiIRExpr) -> tuple[MenaiIRExpr, bool]:
        """Return an optimized IR tree and a boolean indicating whether any changes were made."""
        self._eliminations = 0
        new_ir = ir
        while True:
            self._counts = MenaiIRUseCounter().count(new_ir)
            self._reach = MenaiIRReachability().analyze(new_ir, self._counts.lambda_frame_ids)
            prev_eliminations = self._eliminations
            new_ir = self._opt(new_ir, frame_stack=[0])
            if self._eliminations == prev_eliminations:
                break

        return new_ir, self._eliminations > 0

    def _opt(self, ir: MenaiIRExpr, frame_stack: list[int]) -> MenaiIRExpr:
        """Recursively walk the IR tree and apply optimizations."""
        if isinstance(ir, MenaiIRLet):
            return self._opt_let(ir, frame_stack)

        if isinstance(ir, MenaiIRLetrec):
            return self._opt_letrec(ir, frame_stack)

        if isinstance(ir, MenaiIRIf):
            return self._opt_if(ir, frame_stack)

        if isinstance(ir, MenaiIRLambda):
            return self._opt_lambda(ir, frame_stack)

        if isinstance(ir, MenaiIRCall):
            return self._opt_call(ir, frame_stack)

        if isinstance(ir, MenaiIRLoop):
            return self._opt_loop(ir, frame_stack)

        if isinstance(ir, MenaiIRRecur):
            arg_plans = tuple(self._opt(a, frame_stack) for a in ir.arg_plans)
            if _same_sequence(arg_plans, ir.arg_plans):
                return ir

            return MenaiIRRecur(arg_plans=arg_plans, is_tail_call=ir.is_tail_call)

        if isinstance(ir, MenaiIRBuildList):
            element_plans = tuple(self._opt(e, frame_stack) for e in ir.element_plans)
            if _same_sequence(element_plans, ir.element_plans):
                return ir

            return MenaiIRBuildList(element_plans=element_plans)

        if isinstance(ir, MenaiIRBuildDict):
            pair_plans = tuple((self._opt(k, frame_stack), self._opt(v, frame_stack))
                               for k, v in ir.pair_plans)
            if all(new_k is old_k and new_v is old_v
                   for (new_k, new_v), (old_k, old_v) in zip(pair_plans, ir.pair_plans)):
                return ir

            return MenaiIRBuildDict(pair_plans=pair_plans)

        if isinstance(ir, MenaiIRBuildSet):
            element_plans = tuple(self._opt(e, frame_stack) for e in ir.element_plans)
            if _same_sequence(element_plans, ir.element_plans):
                return ir

            return MenaiIRBuildSet(element_plans=element_plans)

        if isinstance(ir, MenaiIRBuildVector):
            element_plans = tuple(self._opt(e, frame_stack) for e in ir.element_plans)
            if _same_sequence(element_plans, ir.element_plans):
                return ir

            return MenaiIRBuildVector(element_plans=element_plans)

        if isinstance(ir, MenaiIRBuildStruct):
            field_plans = tuple(self._opt(f, frame_stack) for f in ir.field_plans)
            if _same_sequence(field_plans, ir.field_plans):
                return ir

            return MenaiIRBuildStruct(struct_type=ir.struct_type, field_plans=field_plans)

        if isinstance(ir, MenaiIRReturn):
            value_plan = self._opt(ir.value_plan, frame_stack)
            if value_plan is ir.value_plan:
                return ir

            return MenaiIRReturn(value_plan=value_plan)

        if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList, MenaiIRError)):
            return ir

        raise TypeError(f"MenaiIROptimizer: unhandled IR node type {type(ir).__name__}")

    def _opt_let(self, ir: MenaiIRLet, frame_stack: list[int]) -> MenaiIRExpr:
        """
        Drop dead let bindings (unreachable from the evaluation roots).

        Reachability, not a use count, decides liveness: a binding referenced
        only by other unreachable bindings is itself dead, and a use count
        cannot see that.
        """
        current_frame = frame_stack[-1]
        reach = cast(IRReachability, self._reach)

        live: list[tuple[str, MenaiIRExpr]] = []
        changed = False
        for binding in ir.bindings:
            name, value_plan, *_ = binding
            if not reach.is_live(current_frame, id(binding)):
                self._eliminations += 1
                changed = True
                continue

            new_value_plan = self._opt(value_plan, frame_stack)
            if new_value_plan is not value_plan:
                changed = True

            live.append((name, new_value_plan))

        opt_body = self._opt(ir.body_plan, frame_stack)
        if opt_body is not ir.body_plan:
            changed = True

        if not live:
            return opt_body

        if not changed:
            return ir

        return MenaiIRLet(
            bindings=tuple(live),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    def _opt_letrec(self, ir: MenaiIRLetrec, frame_stack: list[int]) -> MenaiIRExpr:
        """
        Drop dead letrec bindings (unreachable from the evaluation roots).

        Reachability removes an entire unreachable mutually-recursive group as
        a unit.  A use count cannot: every member of an unreachable cycle is
        referenced by another member, so no member ever counts as unused.
        """
        current_frame = frame_stack[-1]
        reach = cast(IRReachability, self._reach)

        live: list[tuple[str, MenaiIRExpr]] = []
        changed = False
        for binding in ir.bindings:
            name, value_plan, *_ = binding
            if not reach.is_live(current_frame, id(binding)):
                self._eliminations += 1
                changed = True
                continue

            new_value_plan = self._opt(value_plan, frame_stack)
            if new_value_plan is not value_plan:
                changed = True

            live.append((name, new_value_plan))

        opt_body = self._opt(ir.body_plan, frame_stack)
        if opt_body is not ir.body_plan:
            changed = True

        if not live:
            return opt_body

        if not changed:
            return ir

        return MenaiIRLetrec(
            bindings=tuple(live),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    @staticmethod
    def _unwrap_return(node: MenaiIRExpr) -> tuple[MenaiIRExpr, bool]:
        """Return (inner, was_wrapped) — strips a MenaiIRReturn wrapper if present."""
        if isinstance(node, MenaiIRReturn):
            return node.value_plan, True

        return node, False

    @staticmethod
    def _is_boolean_typed(node: MenaiIRExpr) -> bool:
        """
        Return True if node is guaranteed to produce a boolean value at runtime.

        Used to guard the (if cond #t #f) → cond rewrite: replacing the if with
        cond directly is only sound when cond is known to be boolean, because the
        VM's JUMP_IF_TRUE/FALSE opcodes enforce that the condition is boolean.

        For builtin calls this check is exhaustive: every builtin that returns a
        boolean either has a name ending in '?' (all type predicates, equality and
        comparison operators) or is 'boolean-not'.  No other builtin returns a
        boolean.  User-defined functions are excluded because their return type is
        not known statically.
        """
        if isinstance(node, MenaiIRConstant) and isinstance(node.value, MenaiBoolean):
            return True

        if isinstance(node, MenaiIRCall) and node.is_builtin and node.builtin_name is not None:
            return node.builtin_name.endswith('?') or node.builtin_name == 'boolean-not'

        return False

    def _opt_if(self, ir: MenaiIRIf, frame_stack: list[int]) -> MenaiIRExpr:
        """Optimize an if-expression, folding constant conditions and simplifying branches."""
        opt_condition = self._opt(ir.condition_plan, frame_stack)
        opt_then = self._opt(ir.then_plan, frame_stack)
        opt_else = self._opt(ir.else_plan, frame_stack)

        # Constant-condition elimination:
        #   (if #t then else)  →  then
        #   (if #f then else)  →  else
        if isinstance(opt_condition, MenaiIRConstant) and isinstance(opt_condition.value, MenaiBoolean):
            self._eliminations += 1
            if opt_condition.value.value:
                return opt_then

            return opt_else

        # Boolean identity elimination:
        #   (if <cond> #t #f)  →  <cond>
        #   (if <cond> #f #t)  →  (boolean-not <cond>)
        #
        # The IR builder wraps tail-position branches in MenaiIRReturn.  Branches
        # can be asymmetrically wrapped (e.g. one is a tail call that needs no
        # wrapper, the other is a constant that does), so we use in_tail_position
        # to decide whether the replacement needs wrapping.  We look through any
        # MenaiIRReturn wrapper on each branch to reach the inner constant.  When
        # both branches are boolean constants, they are always symmetrically
        # wrapped (both wrapped or both bare), so then_wrapped is the right signal.
        then_inner, then_wrapped = self._unwrap_return(opt_then)
        else_inner, _ = self._unwrap_return(opt_else)
        if (isinstance(then_inner, MenaiIRConstant)
                and isinstance(then_inner.value, MenaiBoolean)
                and isinstance(else_inner, MenaiIRConstant)
                and isinstance(else_inner.value, MenaiBoolean)
                and self._is_boolean_typed(opt_condition)):
            if then_inner.value.value and not else_inner.value.value:
                # (if cond #t #f) →  cond  (or Return(cond) in tail position)
                self._eliminations += 1
                if then_wrapped:
                    return MenaiIRReturn(value_plan=opt_condition)

                return opt_condition

            if not then_inner.value.value and else_inner.value.value:
                # (if cond #f #t) → (boolean-not cond)
                self._eliminations += 1
                not_call = MenaiIRCall(
                    func_plan=MenaiIRVariable(
                            name='boolean-not'
                    ),
                    arg_plans=(opt_condition,),
                    is_tail_call=ir.in_tail_position,
                    is_builtin=True,
                    builtin_name='boolean-not',
                )
                if then_wrapped:
                    return MenaiIRReturn(value_plan=not_call)

                return not_call

        if (opt_condition is ir.condition_plan
                and opt_then is ir.then_plan
                and opt_else is ir.else_plan):
            return ir

        return MenaiIRIf(
            condition_plan=opt_condition,
            then_plan=opt_then,
            else_plan=opt_else,
            in_tail_position=ir.in_tail_position,
        )

    def _opt_lambda(self, ir: MenaiIRLambda, frame_stack: list[int]) -> MenaiIRLambda:
        """
        Optimize the body of a lambda and prune dead captures.

        After inlining, some captured free vars may no longer be referenced
        in the body.  This method identifies and removes those stale captures
        so that closures are smaller and closure setup is faster.
        """
        counts = cast(IRUseCounts, self._counts)
        lambda_frame_id = counts.lambda_frame_ids.get(id(ir))
        child_stack = frame_stack if lambda_frame_id is None else frame_stack + [lambda_frame_id]

        opt_body = self._opt(ir.body_plan, child_stack)

        sibling_fvs = ir.sibling_free_vars
        sibling_fv_plans = ir.sibling_free_var_plans
        outer_fvs = ir.outer_free_vars
        outer_fv_plans = ir.outer_free_var_plans
        pruned = False

        all_fv_names = sibling_fvs + outer_fvs
        if all_fv_names:
            used_names = self._collect_used_names(opt_body, set(ir.params))
            keep = [fv for fv in all_fv_names if fv in used_names]
            if len(keep) < len(all_fv_names):
                self._eliminations += len(all_fv_names) - len(keep)
                pruned = True
                keep_set = set(keep)
                sibling_fvs = tuple(fv for fv in sibling_fvs if fv in keep_set)
                sibling_fv_plans = tuple(p for fv, p in zip(ir.sibling_free_vars, ir.sibling_free_var_plans) if fv in keep_set)
                outer_fvs = tuple(fv for fv in outer_fvs if fv in keep_set)
                outer_fv_plans = tuple(p for fv, p in zip(ir.outer_free_vars, ir.outer_free_var_plans) if fv in keep_set)

        if not pruned and opt_body is ir.body_plan:
            return ir

        return MenaiIRLambda(
            params=ir.params,
            body_plan=opt_body,
            sibling_free_vars=sibling_fvs,
            sibling_free_var_plans=sibling_fv_plans,
            outer_free_vars=outer_fvs,
            outer_free_var_plans=outer_fv_plans,
            param_count=ir.param_count,
            is_variadic=ir.is_variadic,
            binding_name=ir.binding_name,
            source_line=ir.source_line,
            source_file=ir.source_file,
        )

    @staticmethod
    def _collect_used_names(ir: MenaiIRExpr, bound: set[str]) -> set[str]:
        """
        Collect all local variable names referenced in *ir* that are not
        in *bound*.

        Tracks shadowing by inner let/letrec bindings and lambda
        params/captures.  Nested lambda capture plans (free_var_plans)
        are evaluated in the enclosing scope, so their references are
        collected against the current *bound* set.
        """
        refs: set[str] = set()
        if isinstance(ir, MenaiIRVariable):
            if ir.name not in bound:
                refs.add(ir.name)

        elif isinstance(ir, MenaiIRLambda):
            for plan in ir.sibling_free_var_plans + ir.outer_free_var_plans:
                refs |= MenaiIROptimizer._collect_used_names(plan, bound)

            inner_bound = bound | set(ir.params) | set(ir.sibling_free_vars) | set(ir.outer_free_vars)
            refs |= MenaiIROptimizer._collect_used_names(ir.body_plan, inner_bound)

        elif isinstance(ir, MenaiIRLet):
            for _, val in ir.bindings:
                refs |= MenaiIROptimizer._collect_used_names(val, bound)

            new_bound = bound | {name for name, _ in ir.bindings}
            refs |= MenaiIROptimizer._collect_used_names(ir.body_plan, new_bound)

        elif isinstance(ir, MenaiIRLetrec):
            new_bound = bound | {name for name, _ in ir.bindings}
            for _, val in ir.bindings:
                refs |= MenaiIROptimizer._collect_used_names(val, new_bound)

            refs |= MenaiIROptimizer._collect_used_names(ir.body_plan, new_bound)

        elif isinstance(ir, MenaiIRIf):
            refs |= MenaiIROptimizer._collect_used_names(ir.condition_plan, bound)
            refs |= MenaiIROptimizer._collect_used_names(ir.then_plan, bound)
            refs |= MenaiIROptimizer._collect_used_names(ir.else_plan, bound)

        elif isinstance(ir, MenaiIRCall):
            refs |= MenaiIROptimizer._collect_used_names(ir.func_plan, bound)
            for a in ir.arg_plans:
                refs |= MenaiIROptimizer._collect_used_names(a, bound)

        elif isinstance(ir, MenaiIRReturn):
            refs |= MenaiIROptimizer._collect_used_names(ir.value_plan, bound)

        elif isinstance(ir, MenaiIRBuildList):
            for e in ir.element_plans:
                refs |= MenaiIROptimizer._collect_used_names(e, bound)

        elif isinstance(ir, MenaiIRBuildDict):
            for k, v in ir.pair_plans:
                refs |= MenaiIROptimizer._collect_used_names(k, bound)
                refs |= MenaiIROptimizer._collect_used_names(v, bound)

        elif isinstance(ir, MenaiIRBuildSet):
            for e in ir.element_plans:
                refs |= MenaiIROptimizer._collect_used_names(e, bound)

        elif isinstance(ir, MenaiIRBuildVector):
            for e in ir.element_plans:
                refs |= MenaiIROptimizer._collect_used_names(e, bound)

        elif isinstance(ir, MenaiIRBuildStruct):
            for f in ir.field_plans:
                refs |= MenaiIROptimizer._collect_used_names(f, bound)

        elif isinstance(ir, MenaiIRLoop):
            for init in ir.init_plans:
                refs |= MenaiIROptimizer._collect_used_names(init, bound)

            refs |= MenaiIROptimizer._collect_used_names(ir.body_plan, bound | set(ir.params))

        elif isinstance(ir, MenaiIRRecur):
            for a in ir.arg_plans:
                refs |= MenaiIROptimizer._collect_used_names(a, bound)

        elif isinstance(ir, MenaiIRError):
            refs |= MenaiIROptimizer._collect_used_names(ir.message, bound)

        return refs

    def _opt_call(self, ir: MenaiIRCall, frame_stack: list[int]) -> MenaiIRCall:
        """Optimize the function and argument plans of a call."""
        func_plan = self._opt(ir.func_plan, frame_stack)
        arg_plans = tuple(self._opt(a, frame_stack) for a in ir.arg_plans)
        if func_plan is ir.func_plan and _same_sequence(arg_plans, ir.arg_plans):
            return ir

        return MenaiIRCall(
            func_plan=func_plan,
            arg_plans=arg_plans,
            is_tail_call=ir.is_tail_call,
            is_builtin=ir.is_builtin,
            builtin_name=ir.builtin_name,
        )

    def _opt_loop(self, ir: MenaiIRLoop, frame_stack: list[int]) -> MenaiIRLoop:
        """Optimize the init plans and body of a loop."""
        init_plans = tuple(self._opt(init, frame_stack) for init in ir.init_plans)
        body_plan = self._opt(ir.body_plan, frame_stack)
        if _same_sequence(init_plans, ir.init_plans) and body_plan is ir.body_plan:
            return ir

        return MenaiIRLoop(
            params=ir.params,
            init_plans=init_plans,
            body_plan=body_plan,
            in_tail_position=ir.in_tail_position,
        )
