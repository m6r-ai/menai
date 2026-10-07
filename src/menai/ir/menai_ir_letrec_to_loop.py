"""
Menai IR pass: convert single-binding tail-recursive letrecs to loops.

Transforms a letrec with a single lambda binding whose body is a single tail
call to that lambda into a MenaiIRLoop, eliminating the closure allocation and
call overhead.  The lambda's self-referencing tail calls become MenaiIRRecur
back-edges.

This pass runs before the inliner so that:

1. The inliner can see and inline calls inside the loop body.
2. The IR optimizer can optimise the loop body (dead binding elimination,
   if-folding, etc.).

Pattern recognised
------------------

::

    (letrec ((name (lambda (p1 ... pN) ...body...)))
      (name arg1 ... argN))

where the body's self-referencing calls are tail calls (the call to ``name``
appears in tail position within the lambda body).

The letrec body is a single call to the letrec-bound lambda.  The call may be
direct (``MenaiIRCall`` with ``func_plan = MenaiIRVariable(name)``) or wrapped
in ``MenaiIRReturn`` (when the letrec is in tail position).

Transform
---------

The letrec is replaced by::

    MenaiIRLoop(
        params=[p1, ... pN],
        init_plans=[arg1, ... argN],
        body_plan=<lambda body with self-calls -> MenaiIRRecur>,
    )

The lambda's self-referencing tail calls (``(name new-arg1 ... new-argN)``)
are replaced by ``MenaiIRRecur(arg_plans=[new-arg1, ... new-argN])``.

Only self-referencing tail calls are converted.  A non-tail self-call, or a
reference to the name outside call position, causes the letrec to be rejected
rather than converted, because the loop name is not in scope after conversion.
"""

from menai.ir.menai_ir import (
    MenaiIRExpr,
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRBuildStruct,
    MenaiIRBuildEnum,
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
    MenaiIRQuote,
    MenaiIRRecur,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass


class MenaiIRLetrecToLoop(MenaiIROptimizationPass):
    """
    IR-level pass that converts eligible letrecs to MenaiIRLoop nodes.

    See the module docstring for the transformation details.
    """

    def optimize(self, ir: MenaiIRExpr) -> tuple[MenaiIRExpr, bool]:
        """Return a transformed IR tree and a boolean indicating whether any changes were made."""
        new_ir, changed = self._opt(ir, loop_name=None)
        return new_ir, changed

    def _opt(self, ir: MenaiIRExpr, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """
        Recursively walk the IR tree and convert eligible letrecs.

        ``loop_name`` is the name of the enclosing loop (if any), used to
        identify self-referencing calls that should become MenaiIRRecur nodes.
        """
        if isinstance(ir, MenaiIRLetrec):
            return self._opt_letrec(ir, loop_name)

        if isinstance(ir, MenaiIRLet):
            return self._opt_let(ir, loop_name)

        if isinstance(ir, MenaiIRIf):
            return self._opt_if(ir, loop_name)

        if isinstance(ir, MenaiIRLambda):
            return self._opt_lambda(ir)

        if isinstance(ir, MenaiIRCall):
            return self._opt_call(ir, loop_name)

        if isinstance(ir, MenaiIRReturn):
            new_value, changed = self._opt(ir.value_plan, loop_name)
            return MenaiIRReturn(value_plan=new_value), changed

        if isinstance(ir, MenaiIRBuildList):
            return self._opt_build_list(ir, loop_name)

        if isinstance(ir, MenaiIRBuildDict):
            return self._opt_build_dict(ir, loop_name)

        if isinstance(ir, MenaiIRBuildSet):
            return self._opt_build_set(ir, loop_name)

        if isinstance(ir, MenaiIRBuildVector):
            return self._opt_build_vector(ir, loop_name)

        if isinstance(ir, MenaiIRBuildStruct):
            return self._opt_build_struct(ir, loop_name)

        if isinstance(ir, MenaiIRBuildEnum):
            return ir, False

        if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList, MenaiIRError)):
            return ir, False

        if isinstance(ir, MenaiIRLoop):
            return self._opt_loop(ir, loop_name)

        if isinstance(ir, MenaiIRRecur):
            return ir, False

        raise TypeError(f"MenaiIRLetrecToLoop: unhandled IR node type {type(ir).__name__}")

    def _opt_letrec(self, ir: MenaiIRLetrec, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Check if this letrec is convertible to a loop; if so, convert it."""
        convertible, name, lam, init_args = self._analyze_letrec(ir)
        if convertible:
            assert lam is not None
            new_body = self._convert_body(lam.body_plan, name)
            return MenaiIRLoop(
                params=tuple(lam.params),
                init_plans=tuple(init_args),
                body_plan=new_body,
                in_tail_position=ir.in_tail_position,
            ), True

        # Not convertible — walk children normally.
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        changed = False
        for bname, value_plan in ir.bindings:
            opt_value, c = self._opt(value_plan, loop_name)
            opt_bindings.append((bname, opt_value))
            changed = changed or c

        opt_body, c = self._opt(ir.body_plan, loop_name)
        changed = changed or c

        return MenaiIRLetrec(
            bindings=tuple(opt_bindings),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        ), changed

    @staticmethod
    def _analyze_letrec(
        ir: MenaiIRLetrec,
    ) -> tuple[bool, str, MenaiIRLambda | None, list[MenaiIRExpr]]:
        """
        Determine whether a letrec is convertible to a MenaiIRLoop.

        Returns (convertible, name, lambda, init_args).  When the letrec is not
        convertible the lambda is None and the other fields are empty.
        """
        # Must have exactly one binding.
        if len(ir.bindings) != 1:
            return False, "", None, []

        name, value_plan = ir.bindings[0]
        if not isinstance(value_plan, MenaiIRLambda):
            return False, "", None, []

        lam = value_plan

        # The lambda must be self-referencing: it is bound to the letrec name
        # and captures itself as a sibling free var.
        if lam.binding_name != name:
            return False, "", None, []

        if name not in lam.sibling_free_vars:
            return False, "", None, []

        # The letrec body must be a single call to the lambda, possibly wrapped
        # in MenaiIRReturn for tail position.
        body = ir.body_plan

        if isinstance(body, MenaiIRReturn):
            body = body.value_plan

        if not isinstance(body, MenaiIRCall):
            return False, "", None, []

        if body.is_builtin:
            return False, "", None, []

        if not isinstance(body.func_plan, MenaiIRVariable):
            return False, "", None, []

        if body.func_plan.name != name:
            return False, "", None, []

        # Arity must match.
        if len(body.arg_plans) != lam.param_count:
            return False, "", None, []

        # The init arguments (the body call's arguments) are evaluated in the
        # enclosing scope after conversion, where the letrec name is no longer
        # bound.  A reference to the name in an init argument — e.g. a nested
        # non-tail self-call like (move (move x 1) 1) — would become
        # unresolvable, so such a letrec is not convertible.
        if any(_references_name(arg, name) for arg in body.arg_plans):
            return False, "", None, []

        # The lambda body must not contain a nested lambda that references the
        # letrec name.  Such a lambda would need to call the loop via Recur, but
        # it is a separate scope — the loop name is not visible inside it after
        # conversion.
        if _nested_lambda_references_name(lam.body_plan, name):
            return False, "", None, []

        # The lambda body must not reference the letrec name as a variable
        # outside direct call position.  After conversion the name is no longer
        # in scope — only the loop params are.  A reference like (apply name ...)
        # or (lambda () name) would become unresolvable.
        if _references_name_outside_call(lam.body_plan, name):
            return False, "", None, []

        # Every self-referencing call in the lambda body must provide exactly as
        # many arguments as the lambda has parameters.  A function-level
        # self-loop can omit args (unprovided params retain their values), but a
        # MenaiIRRecur must supply all loop-carried variables.
        if not _self_calls_correct_arity(lam.body_plan, name, lam.param_count):
            return False, "", None, []

        # Every self-referencing call in the lambda body must be a tail call.  A
        # Recur node terminates its block; a non-tail self-call would orphan the
        # continuation that consumes its result.
        if not _self_calls_all_tail(lam.body_plan, name):
            return False, "", None, []

        return True, name, lam, list(body.arg_plans)

    def _convert_body(self, body: MenaiIRExpr, loop_name: str) -> MenaiIRExpr:
        """
        Walk the lambda body and replace self-referencing tail calls with
        MenaiIRRecur nodes.
        """
        new_body, _ = self._opt(body, loop_name=loop_name)
        return new_body

    def _opt_call(self, ir: MenaiIRCall, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Replace self-referencing tail calls with MenaiIRRecur."""
        if loop_name is not None and not ir.is_builtin:
            if isinstance(ir.func_plan, MenaiIRVariable) and ir.func_plan.name == loop_name:
                return MenaiIRRecur(
                    arg_plans=tuple(ir.arg_plans),
                    is_tail_call=ir.is_tail_call,
                ), True

        # Not a self-call — optimise children.
        opt_func, changed = self._opt(ir.func_plan, loop_name)
        opt_args: list[MenaiIRExpr] = []
        for arg in ir.arg_plans:
            opt_arg, c = self._opt(arg, loop_name)
            opt_args.append(opt_arg)
            changed = changed or c

        return MenaiIRCall(
            func_plan=opt_func,
            arg_plans=tuple(opt_args),
            is_tail_call=ir.is_tail_call,
            is_builtin=ir.is_builtin,
            builtin_name=ir.builtin_name,
        ), changed

    def _opt_let(self, ir: MenaiIRLet, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a let, passing loop_name through."""
        opt_bindings: list[tuple[str, MenaiIRExpr]] = []
        changed = False
        for name, value_plan in ir.bindings:
            opt_value, c = self._opt(value_plan, loop_name)
            opt_bindings.append((name, opt_value))
            changed = changed or c

        opt_body, c = self._opt(ir.body_plan, loop_name)
        changed = changed or c

        return MenaiIRLet(
            bindings=tuple(opt_bindings),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        ), changed

    def _opt_if(self, ir: MenaiIRIf, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk an if-expression, passing loop_name through."""
        opt_cond, c1 = self._opt(ir.condition_plan, loop_name)
        opt_then, c2 = self._opt(ir.then_plan, loop_name)
        opt_else, c3 = self._opt(ir.else_plan, loop_name)
        return MenaiIRIf(
            condition_plan=opt_cond,
            then_plan=opt_then,
            else_plan=opt_else,
            in_tail_position=ir.in_tail_position,
        ), c1 or c2 or c3

    def _opt_lambda(self, ir: MenaiIRLambda) -> tuple[MenaiIRExpr, bool]:
        """
        Walk a lambda body.  The enclosing loop name does not propagate into the
        lambda body — a nested lambda creates a new scope, so self-referencing
        calls inside it refer to the nested lambda, not the enclosing loop.
        """
        opt_body, changed = self._opt(ir.body_plan, loop_name=None)
        return MenaiIRLambda(
            params=ir.params,
            body_plan=opt_body,
            sibling_free_vars=ir.sibling_free_vars,
            sibling_free_var_plans=ir.sibling_free_var_plans,
            outer_free_vars=ir.outer_free_vars,
            outer_free_var_plans=ir.outer_free_var_plans,
            param_count=ir.param_count,
            is_variadic=ir.is_variadic,
            binding_name=ir.binding_name,
            source_line=ir.source_line,
            source_file=ir.source_file,
        ), changed

    def _opt_loop(self, ir: MenaiIRLoop, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk an existing MenaiIRLoop (from a previous fixed-point iteration)."""
        opt_init: list[MenaiIRExpr] = []
        changed = False
        for init in ir.init_plans:
            opt, c = self._opt(init, loop_name)
            opt_init.append(opt)
            changed = changed or c

        # Self-referencing calls were already rewritten to MenaiIRRecur when the
        # loop was created.  Walk the body with loop_name=None — a name-based
        # rewrite here would wrongly convert calls to unrelated locals that
        # share a param name.
        opt_body, c = self._opt(ir.body_plan, loop_name=None)
        changed = changed or c

        return MenaiIRLoop(
            params=ir.params,
            init_plans=tuple(opt_init),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        ), changed

    def _opt_build_list(self, ir: MenaiIRBuildList, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a list literal, passing loop_name through to its elements."""
        opt_elems: list[MenaiIRExpr] = []
        changed = False
        for elem in ir.element_plans:
            opt, c = self._opt(elem, loop_name)
            opt_elems.append(opt)
            changed = changed or c

        return MenaiIRBuildList(element_plans=tuple(opt_elems)), changed

    def _opt_build_dict(self, ir: MenaiIRBuildDict, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a dict literal, passing loop_name through to its keys and values."""
        opt_pairs: list[tuple[MenaiIRExpr, MenaiIRExpr]] = []
        changed = False
        for k, v in ir.pair_plans:
            opt_k, c1 = self._opt(k, loop_name)
            opt_v, c2 = self._opt(v, loop_name)
            opt_pairs.append((opt_k, opt_v))
            changed = changed or c1 or c2

        return MenaiIRBuildDict(pair_plans=tuple(opt_pairs)), changed

    def _opt_build_set(self, ir: MenaiIRBuildSet, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a set literal, passing loop_name through to its elements."""
        opt_elems: list[MenaiIRExpr] = []
        changed = False
        for elem in ir.element_plans:
            opt, c = self._opt(elem, loop_name)
            opt_elems.append(opt)
            changed = changed or c

        return MenaiIRBuildSet(element_plans=tuple(opt_elems)), changed

    def _opt_build_vector(self, ir: MenaiIRBuildVector, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a vector literal, passing loop_name through to its elements."""
        opt_elems: list[MenaiIRExpr] = []
        changed = False
        for elem in ir.element_plans:
            opt, c = self._opt(elem, loop_name)
            opt_elems.append(opt)
            changed = changed or c

        return MenaiIRBuildVector(element_plans=tuple(opt_elems)), changed

    def _opt_build_struct(self, ir: MenaiIRBuildStruct, loop_name: str | None) -> tuple[MenaiIRExpr, bool]:
        """Walk a struct constructor, passing loop_name through to its fields."""
        opt_fields: list[MenaiIRExpr] = []
        changed = False
        for field in ir.field_plans:
            opt, c = self._opt(field, loop_name)
            opt_fields.append(opt)
            changed = changed or c

        return MenaiIRBuildStruct(struct_type=ir.struct_type, field_plans=tuple(opt_fields)), changed


def _nested_lambda_references_name(ir: MenaiIRExpr, name: str) -> bool:
    """
    Check whether *ir* contains a MenaiIRLambda whose body references *name*.

    Used to reject letrec-to-loop conversion when a nested lambda calls the
    letrec-bound function — such calls cannot become MenaiIRRecur because the
    loop name is not visible inside the nested lambda's scope.
    """
    if isinstance(ir, MenaiIRLambda):
        return _references_name(ir.body_plan, name)

    if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _nested_lambda_references_name(ir.message, name)

    if isinstance(ir, MenaiIRIf):
        return (_nested_lambda_references_name(ir.condition_plan, name)
                or _nested_lambda_references_name(ir.then_plan, name)
                or _nested_lambda_references_name(ir.else_plan, name))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (any(_nested_lambda_references_name(v, name) for _, v in ir.bindings)
                or _nested_lambda_references_name(ir.body_plan, name))

    if isinstance(ir, MenaiIRCall):
        return (_nested_lambda_references_name(ir.func_plan, name)
                or any(_nested_lambda_references_name(a, name) for a in ir.arg_plans))

    if isinstance(ir, MenaiIRReturn):
        return _nested_lambda_references_name(ir.value_plan, name)

    if isinstance(ir, MenaiIRBuildList):
        return any(_nested_lambda_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_nested_lambda_references_name(k, name) or _nested_lambda_references_name(v, name)
                   for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_nested_lambda_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return any(_nested_lambda_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_nested_lambda_references_name(f, name) for f in ir.field_plans)

    if isinstance(ir, MenaiIRBuildEnum):
        return False

    if isinstance(ir, MenaiIRLoop):
        return (any(_nested_lambda_references_name(init, name) for init in ir.init_plans)
                or _nested_lambda_references_name(ir.body_plan, name))

    if isinstance(ir, MenaiIRRecur):
        return any(_nested_lambda_references_name(a, name) for a in ir.arg_plans)

    return False


def _references_name(ir: MenaiIRExpr, name: str) -> bool:
    """Check whether *ir* references *name* as a local variable anywhere."""
    if isinstance(ir, MenaiIRVariable):
        return ir.name == name

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _references_name(ir.message, name)

    if isinstance(ir, MenaiIRIf):
        return (_references_name(ir.condition_plan, name)
                or _references_name(ir.then_plan, name)
                or _references_name(ir.else_plan, name))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (any(_references_name(v, name) for _, v in ir.bindings)
                or _references_name(ir.body_plan, name))

    if isinstance(ir, MenaiIRLambda):
        return _references_name(ir.body_plan, name)

    if isinstance(ir, MenaiIRCall):
        return _references_name(ir.func_plan, name) or any(_references_name(a, name) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _references_name(ir.value_plan, name)

    if isinstance(ir, MenaiIRBuildList):
        return any(_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_references_name(k, name) or _references_name(v, name) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return any(_references_name(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_references_name(f, name) for f in ir.field_plans)

    if isinstance(ir, MenaiIRBuildEnum):
        return False

    if isinstance(ir, MenaiIRLoop):
        return (any(_references_name(init, name) for init in ir.init_plans)
                or _references_name(ir.body_plan, name))

    if isinstance(ir, MenaiIRRecur):
        return any(_references_name(a, name) for a in ir.arg_plans)

    return False


def _references_name_outside_call(ir: MenaiIRExpr, name: str) -> bool:
    """
    Check whether *ir* references *name* as a local variable anywhere except in
    direct call position (i.e. as the ``func_plan`` of a ``MenaiIRCall``).

    After letrec-to-loop conversion, the letrec name is no longer in scope.
    Self-referencing calls are converted to ``MenaiIRRecur``, so they are safe.
    But any other reference — as a variable, as an argument to ``apply``, inside
    a list literal, etc. — would become unresolvable.
    """
    if isinstance(ir, MenaiIRVariable):
        return ir.name == name

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
        return False

    if isinstance(ir, MenaiIRError):
        return _references_name_outside_call(ir.message, name)

    if isinstance(ir, MenaiIRIf):
        return (_references_name_outside_call(ir.condition_plan, name)
                or _references_name_outside_call(ir.then_plan, name)
                or _references_name_outside_call(ir.else_plan, name))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (any(_references_name_outside_call(v, name) for _, v in ir.bindings)
                or _references_name_outside_call(ir.body_plan, name))

    if isinstance(ir, MenaiIRLambda):
        return _references_name_outside_call(ir.body_plan, name)

    if isinstance(ir, MenaiIRCall):
        # The func_plan in direct call position is converted to MenaiIRRecur, so
        # it is safe.  Arguments and non-direct func_plans are not.
        if (isinstance(ir.func_plan, MenaiIRVariable)
                and ir.func_plan.name == name
                and not ir.is_builtin):
            return any(_references_name_outside_call(a, name) for a in ir.arg_plans)

        return (_references_name_outside_call(ir.func_plan, name)
                or any(_references_name_outside_call(a, name) for a in ir.arg_plans))

    if isinstance(ir, MenaiIRReturn):
        return _references_name_outside_call(ir.value_plan, name)

    if isinstance(ir, MenaiIRBuildList):
        return any(_references_name_outside_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return any(_references_name_outside_call(k, name) or _references_name_outside_call(v, name)
                   for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return any(_references_name_outside_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return any(_references_name_outside_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return any(_references_name_outside_call(f, name) for f in ir.field_plans)

    if isinstance(ir, MenaiIRBuildEnum):
        return False

    if isinstance(ir, MenaiIRLoop):
        return (any(_references_name_outside_call(init, name) for init in ir.init_plans)
                or _references_name_outside_call(ir.body_plan, name))

    if isinstance(ir, MenaiIRRecur):
        return any(_references_name_outside_call(a, name) for a in ir.arg_plans)

    return False


def _self_calls_correct_arity(ir: MenaiIRExpr, name: str, param_count: int) -> bool:
    """
    Check whether every self-referencing call to *name* in *ir* provides exactly
    *param_count* arguments.

    A function-level self-loop can omit trailing arguments (unprovided params
    retain their values from the previous iteration), but a MenaiIRRecur must
    supply all loop-carried variables.  A call with the wrong arity would
    produce a loop that updates only a subset of loop params, leaving the rest
    undefined.
    """
    if isinstance(ir, MenaiIRCall) and not ir.is_builtin:
        if isinstance(ir.func_plan, MenaiIRVariable) and ir.func_plan.name == name:
            if len(ir.arg_plans) != param_count:
                return False

        return (_self_calls_correct_arity(ir.func_plan, name, param_count)
                and all(_self_calls_correct_arity(a, name, param_count) for a in ir.arg_plans))

    if isinstance(ir, MenaiIRReturn):
        return _self_calls_correct_arity(ir.value_plan, name, param_count)

    if isinstance(ir, MenaiIRIf):
        return (_self_calls_correct_arity(ir.condition_plan, name, param_count)
                and _self_calls_correct_arity(ir.then_plan, name, param_count)
                and _self_calls_correct_arity(ir.else_plan, name, param_count))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (all(_self_calls_correct_arity(v, name, param_count) for _, v in ir.bindings)
                and _self_calls_correct_arity(ir.body_plan, name, param_count))

    if isinstance(ir, MenaiIRLambda):
        return _self_calls_correct_arity(ir.body_plan, name, param_count)

    if isinstance(ir, MenaiIRBuildList):
        return all(_self_calls_correct_arity(e, name, param_count) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return all(_self_calls_correct_arity(k, name, param_count)
                   and _self_calls_correct_arity(v, name, param_count)
                   for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return all(_self_calls_correct_arity(e, name, param_count) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return all(_self_calls_correct_arity(e, name, param_count) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return all(_self_calls_correct_arity(f, name, param_count) for f in ir.field_plans)

    if isinstance(ir, MenaiIRBuildEnum):
        return True

    if isinstance(ir, MenaiIRError):
        return _self_calls_correct_arity(ir.message, name, param_count)

    if isinstance(ir, MenaiIRLoop):
        return (all(_self_calls_correct_arity(init, name, param_count) for init in ir.init_plans)
                and _self_calls_correct_arity(ir.body_plan, name, param_count))

    if isinstance(ir, MenaiIRRecur):
        return all(_self_calls_correct_arity(a, name, param_count) for a in ir.arg_plans)

    return True


def _self_calls_all_tail(ir: MenaiIRExpr, name: str) -> bool:
    """
    Check whether every self-referencing call to *name* in *ir* appears in tail
    position.

    Tail position means: the expression is the value of the enclosing lambda
    body (possibly wrapped in MenaiIRReturn), or it is in a tail branch of an
    if-expression (the then/else plans of an if whose own result flows to the
    lambda's result).  Calls appearing in let binding values, if conditions,
    call arguments, or build nodes are non-tail — their result is consumed by a
    continuation.
    """
    return _tail_ok(ir, name)


def _tail_ok(ir: MenaiIRExpr, name: str) -> bool:
    """Tail-position check for _self_calls_all_tail."""
    if isinstance(ir, MenaiIRReturn):
        return _tail_ok(ir.value_plan, name)

    if isinstance(ir, MenaiIRCall):
        if isinstance(ir.func_plan, MenaiIRVariable) and ir.func_plan.name == name:
            # A self-call in tail position is fine here — but its arguments must
            # not contain non-tail self-calls.
            return all(_no_self_call(a, name) for a in ir.arg_plans)

        return all(_no_self_call(a, name) for a in ir.arg_plans) and _no_self_call(ir.func_plan, name)

    if isinstance(ir, MenaiIRIf):
        return (_no_self_call(ir.condition_plan, name)
                and _tail_ok(ir.then_plan, name)
                and _tail_ok(ir.else_plan, name))

    if isinstance(ir, MenaiIRLet):
        return (all(_no_self_call(v, name) for _, v in ir.bindings)
                and _tail_ok(ir.body_plan, name))

    if isinstance(ir, (MenaiIRConstant, MenaiIRVariable, MenaiIRQuote, MenaiIREmptyList, MenaiIRError)):
        return True

    # Any other node in tail position (build nodes, lambdas, loops) cannot itself
    # be a self-call, but its children are checked conservatively for non-tail
    # self-calls via _no_self_call.
    return _no_self_call(ir, name)


def _no_self_call(ir: MenaiIRExpr, name: str) -> bool:
    """Check that *ir* contains no call to *name* anywhere (tail or not)."""
    if isinstance(ir, MenaiIRCall):
        if isinstance(ir.func_plan, MenaiIRVariable) and ir.func_plan.name == name:
            return False

        return _no_self_call(ir.func_plan, name) and all(_no_self_call(a, name) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _no_self_call(ir.value_plan, name)

    if isinstance(ir, MenaiIRIf):
        return (_no_self_call(ir.condition_plan, name)
                and _no_self_call(ir.then_plan, name)
                and _no_self_call(ir.else_plan, name))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (all(_no_self_call(v, name) for _, v in ir.bindings)
                and _no_self_call(ir.body_plan, name))

    if isinstance(ir, MenaiIRLambda):
        return _no_self_call(ir.body_plan, name)

    if isinstance(ir, MenaiIRBuildList):
        return all(_no_self_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        return all(_no_self_call(k, name) and _no_self_call(v, name) for k, v in ir.pair_plans)

    if isinstance(ir, MenaiIRBuildSet):
        return all(_no_self_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildVector):
        return all(_no_self_call(e, name) for e in ir.element_plans)

    if isinstance(ir, MenaiIRBuildStruct):
        return all(_no_self_call(f, name) for f in ir.field_plans)

    if isinstance(ir, MenaiIRBuildEnum):
        return True

    if isinstance(ir, MenaiIRError):
        return _no_self_call(ir.message, name)

    if isinstance(ir, MenaiIRLoop):
        return (all(_no_self_call(init, name) for init in ir.init_plans)
                and _no_self_call(ir.body_plan, name))

    if isinstance(ir, MenaiIRRecur):
        return all(_no_self_call(a, name) for a in ir.arg_plans)

    # Constants, variables, quotes, empty lists: no calls.
    return True
