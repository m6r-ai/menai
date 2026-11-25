"""
Menai IR Optimizer - transformation pass over the IR tree.

Consumes an IRUseCounts annotation (produced by MenaiIRUseCounter) and an
IRReachability annotation (produced by MenaiIRReachability) and applies IR-level
optimizations that are safe because Menai is a pure functional language — every
binding is immutable and every expression is side-effect-free.
"""

import operator

from collections.abc import Callable
from typing import cast

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
    MenaiIRRecur,
    MenaiIRQuote,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.menai_value import MenaiBoolean
from menai.menai_value import MenaiInteger
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


def _fold_literal_test(
    ir: MenaiIRCall,
    arg_plans: tuple[MenaiIRExpr, ...],
) -> MenaiIRExpr | None:
    """
    Fold a test whose answer its argument's shape decides, or return None.

    `list-null?` is the case that matters here: the inliner binds a variadic rest
    parameter to a list literal, so a body that branches on "were there extra
    arguments" carries a test the AST constant folder never sees, because the
    literal only appears once inlining has run.  Folding it here lets the branch
    collapse and takes the untaken arm with it.
    """
    if not ir.is_builtin or ir.builtin_name != 'list-null?' or len(arg_plans) != 1:
        return None

    argument = arg_plans[0]
    if isinstance(argument, MenaiIREmptyList):
        return MenaiIRConstant(value=MenaiBoolean(value=True))

    if isinstance(argument, MenaiIRBuildList):
        return MenaiIRConstant(value=MenaiBoolean(value=not argument.element_plans))

    return None


_INTEGER_COMPARISONS: dict[str, Callable[[int, int], bool]] = {
    'integer=?': operator.eq,
    'integer!=?': operator.ne,
    'integer<?': operator.lt,
    'integer<=?': operator.le,
    'integer>?': operator.gt,
    'integer>=?': operator.ge,
}


def _fold_constant_comparison(
    ir: MenaiIRCall,
    arg_plans: tuple[MenaiIRExpr, ...],
) -> MenaiIRExpr | None:
    """
    Fold an integer comparison whose operands are both literals, or return None.

    The AST constant folder already folds a comparison written out in the source, so
    this exists for the one that only becomes foldable later: a variadic prelude
    wrapper dispatches on `($integer=? ($list-length args) 2)`, and both operands are
    literals only once the accessor on the rest list has folded.  Deciding the test
    is what lets the branch collapse and take the arms the call's arity cannot select
    with it, which is the difference between a dispatcher and one comparison.

    Only two integer literals are folded.  A comparison whose operands are not both
    integers raises, so folding it would suppress the raise.
    """
    if not ir.is_builtin or ir.builtin_name is None or len(arg_plans) != 2:
        return None

    compare = _INTEGER_COMPARISONS.get(ir.builtin_name)
    if compare is None:
        return None

    left, right = arg_plans
    if not (isinstance(left, MenaiIRConstant) and isinstance(left.value, MenaiInteger)):
        return None

    if not (isinstance(right, MenaiIRConstant) and isinstance(right.value, MenaiInteger)):
        return None

    return MenaiIRConstant(value=MenaiBoolean(value=compare(left.value.value, right.value.value)))


def _literal_container(plan: MenaiIRExpr) -> tuple[str, tuple[MenaiIRExpr, ...]] | None:
    """
    Return the kind and elements of a container literal, or None for any other plan.

    A MenaiIREmptyList is a list literal with no elements.  Nothing else has a shape
    that is known at compile time.
    """
    if isinstance(plan, MenaiIRBuildList):
        return 'list', plan.element_plans

    if isinstance(plan, MenaiIRBuildVector):
        return 'vector', plan.element_plans

    if isinstance(plan, MenaiIREmptyList):
        return 'list', ()

    return None


def _constant_index(plan: MenaiIRExpr | None) -> int | None:
    """Return the integer an index plan is a literal for, or None."""
    if isinstance(plan, MenaiIRConstant) and isinstance(plan.value, MenaiInteger):
        return plan.value.value

    return None


def _fold_literal_accessor(
    builtin_name: str,
    kind: str,
    elements: tuple[MenaiIRExpr, ...],
    index_plan: MenaiIRExpr | None,
) -> MenaiIRExpr | None:
    """
    Fold an accessor applied to a container literal, or return None.

    This is the fold the variadic prelude wrappers need.  Each wrapper dispatches on
    `($list-length args)` and then reads `($list-first args)` and `($list-last args)`,
    where `args` is a list literal once the call has been inlined.  Neither the AST
    constant folder nor branch constant propagation can decide those, because the
    elements are runtime values rather than constants, so the dispatcher survives and
    with it an allocation per call.

    Every fold agrees with the VM.  An accessor that would raise for the literal's
    shape is left alone: replacing it with a value would suppress the raise.
    """
    length = len(elements)

    if kind == 'list':
        if builtin_name == 'list-length':
            return MenaiIRConstant(value=MenaiInteger(value=length))

        if builtin_name == 'list-null?':
            return MenaiIRConstant(value=MenaiBoolean(value=length == 0))

        if builtin_name == 'list-first' and length >= 1:
            return elements[0]

        if builtin_name == 'list-last' and length >= 1:
            return elements[-1]

        if builtin_name == 'list-rest' and length >= 1:
            rest = elements[1:]
            return MenaiIRBuildList(element_plans=rest) if rest else MenaiIREmptyList()

        if builtin_name == 'list-nth':
            index = _constant_index(index_plan)
            if index is not None and 0 <= index < length:
                return elements[index]

        return None

    if builtin_name == 'vector-length':
        return MenaiIRConstant(value=MenaiInteger(value=length))

    if builtin_name == 'vector-nth':
        index = _constant_index(index_plan)
        if index is not None and 0 <= index < length:
            return elements[index]

    return None


def _fold_container_argument(ir: MenaiIRCall, arg_plans: tuple[MenaiIRExpr, ...]) -> MenaiIRExpr | None:
    """
    Fold an accessor whose argument is written inline as a container literal.

    The literal has no other reader, so the fold removes it along with the accessor
    and each element is still evaluated exactly once.
    """
    if not ir.is_builtin or ir.builtin_name is None or not arg_plans:
        return None

    container = _literal_container(arg_plans[0])
    if container is None:
        return None

    return _fold_literal_accessor(
        ir.builtin_name, container[0], container[1], arg_plans[1] if len(arg_plans) > 1 else None
    )


def _fold_literal_uses(
    ir: MenaiIRExpr,
    name: str,
    kind: str,
    elements: tuple[MenaiIRExpr, ...],
) -> MenaiIRExpr | None:
    """
    Replace every use of *name* with the value a container literal gives it.

    Returns None unless every use of the name is an accessor this fold decides, which
    is what makes dropping the binding safe.  A name rebound by an inner binding form,
    lambda or loop is left alone and its subtree is not entered.

    *elements* are plans to substitute, so the caller passes a variable for each
    element rather than the element's own expression: an element read at several
    sites must still be evaluated once.
    """
    if isinstance(ir, MenaiIRVariable):
        return None if ir.name == name else ir

    if isinstance(ir, MenaiIRCall):
        if ir.is_builtin and ir.builtin_name is not None and ir.arg_plans:
            head = ir.arg_plans[0]
            if isinstance(head, MenaiIRVariable) and head.name == name:
                folded = _fold_literal_accessor(
                    ir.builtin_name, kind, elements, ir.arg_plans[1] if len(ir.arg_plans) > 1 else None
                )
                return folded

        func_plan = _fold_literal_uses(ir.func_plan, name, kind, elements)
        if func_plan is None:
            return None

        arg_plans = _fold_literal_uses_in_sequence(ir.arg_plans, name, kind, elements)
        if arg_plans is None:
            return None

        if func_plan is ir.func_plan and arg_plans is ir.arg_plans:
            return ir

        return MenaiIRCall(
            func_plan=func_plan,
            arg_plans=arg_plans,
            is_tail_call=ir.is_tail_call,
            is_builtin=ir.is_builtin,
            builtin_name=ir.builtin_name,
        )

    if isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList, MenaiIRBuildEnum)):
        return ir

    if isinstance(ir, MenaiIRError):
        message = _fold_literal_uses(ir.message, name, kind, elements)
        return MenaiIRError(message=message) if message is not None else None

    if isinstance(ir, MenaiIRIf):
        condition = _fold_literal_uses(ir.condition_plan, name, kind, elements)
        then_plan = _fold_literal_uses(ir.then_plan, name, kind, elements)
        else_plan = _fold_literal_uses(ir.else_plan, name, kind, elements)
        if condition is None or then_plan is None or else_plan is None:
            return None

        if (condition is ir.condition_plan and then_plan is ir.then_plan
                and else_plan is ir.else_plan):
            return ir

        return MenaiIRIf(
            condition_plan=condition,
            then_plan=then_plan,
            else_plan=else_plan,
            in_tail_position=ir.in_tail_position,
        )

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        if name in {bound for bound, _ in ir.bindings}:
            return ir

        bindings = []
        for bound, value in ir.bindings:
            new_value = _fold_literal_uses(value, name, kind, elements)
            if new_value is None:
                return None

            bindings.append((bound, new_value))

        body_plan = _fold_literal_uses(ir.body_plan, name, kind, elements)
        if body_plan is None:
            return None

        return type(ir)(
            bindings=tuple(bindings),
            body_plan=body_plan,
            in_tail_position=ir.in_tail_position,
        )

    if isinstance(ir, MenaiIRLambda):
        if name in ir.params:
            return ir

        body_plan = _fold_literal_uses(ir.body_plan, name, kind, elements)
        if body_plan is None:
            return None

        return MenaiIRLambda(
            params=ir.params,
            body_plan=body_plan,
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

    if isinstance(ir, MenaiIRLoop):
        if name in ir.params:
            return ir

        init_plans = _fold_literal_uses_in_sequence(ir.init_plans, name, kind, elements)
        if init_plans is None:
            return None

        body_plan = _fold_literal_uses(ir.body_plan, name, kind, elements)
        if body_plan is None:
            return None

        return MenaiIRLoop(
            params=ir.params,
            init_plans=init_plans,
            body_plan=body_plan,
            in_tail_position=ir.in_tail_position,
        )

    if isinstance(ir, MenaiIRRecur):
        arg_plans = _fold_literal_uses_in_sequence(ir.arg_plans, name, kind, elements)
        if arg_plans is None:
            return None

        if arg_plans is ir.arg_plans:
            return ir

        return MenaiIRRecur(arg_plans=arg_plans, is_tail_call=ir.is_tail_call)

    if isinstance(ir, MenaiIRReturn):
        value_plan = _fold_literal_uses(ir.value_plan, name, kind, elements)
        if value_plan is None:
            return None

        return ir if value_plan is ir.value_plan else MenaiIRReturn(value_plan=value_plan)

    if isinstance(ir, MenaiIRBuildStruct):
        field_plans = _fold_literal_uses_in_sequence(ir.field_plans, name, kind, elements)
        if field_plans is None:
            return None

        if field_plans is ir.field_plans:
            return ir

        return MenaiIRBuildStruct(struct_type=ir.struct_type, field_plans=field_plans)

    if isinstance(ir, (MenaiIRBuildList, MenaiIRBuildSet, MenaiIRBuildVector)):
        element_plans = _fold_literal_uses_in_sequence(ir.element_plans, name, kind, elements)
        if element_plans is None:
            return None

        if element_plans is ir.element_plans:
            return ir

        return type(ir)(element_plans=element_plans)

    if isinstance(ir, MenaiIRBuildDict):
        pair_plans = []
        for key, value in ir.pair_plans:
            new_key = _fold_literal_uses(key, name, kind, elements)
            new_value = _fold_literal_uses(value, name, kind, elements)
            if new_key is None or new_value is None:
                return None

            pair_plans.append((new_key, new_value))

        return MenaiIRBuildDict(pair_plans=tuple(pair_plans))

    raise TypeError(f"_fold_literal_uses: unhandled IR node type {type(ir).__name__}")


def _fold_literal_uses_in_sequence(
    plans: tuple[MenaiIRExpr, ...],
    name: str,
    kind: str,
    elements: tuple[MenaiIRExpr, ...],
) -> tuple[MenaiIRExpr, ...] | None:
    """
    Fold a sequence of plans, returning the original tuple when nothing changed.

    Returning the same object lets the caller's identity test distinguish an
    unchanged sequence from a rewritten one.
    """
    folded = []
    changed = False
    for plan in plans:
        new_plan = _fold_literal_uses(plan, name, kind, elements)
        if new_plan is None:
            return None

        changed = changed or new_plan is not plan
        folded.append(new_plan)

    return tuple(folded) if changed else plans


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
        self._temp_counter = 0

    def _gen_temp(self) -> str:
        """
        Generate a unique temporary variable name.

        Uses the compiler-generated `#:` prefix so the name cannot collide with a
        user identifier.  The counter is per-instance and monotonically increasing,
        so repeated folding never reuses a name.
        """
        self._temp_counter += 1
        return f"#:fold-tmp-{self._temp_counter}"

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

        if isinstance(ir, MenaiIRBuildEnum):
            return ir

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

        A binding whose value is a container literal is also folded away when every
        use of its name is an accessor the literal's shape decides, so the literal
        and the accessor both disappear.  That is the shape a variadic prelude
        wrapper takes once a call to it has been inlined: its rest argument is bound
        to a literal and then read only by `$list-length`, `$list-first` and
        `$list-last`.
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

        live, opt_body, folded = self._fold_literal_bindings(live, opt_body)
        changed = changed or folded

        if not live:
            return opt_body

        if not changed:
            return ir

        return MenaiIRLet(
            bindings=tuple(live),
            body_plan=opt_body,
            in_tail_position=ir.in_tail_position,
        )

    def _fold_literal_bindings(
        self,
        bindings: list[tuple[str, MenaiIRExpr]],
        body_plan: MenaiIRExpr,
    ) -> tuple[list[tuple[str, MenaiIRExpr]], MenaiIRExpr, bool]:
        """
        Fold the uses of any binding whose value is a container literal.

        A binding is folded only when every use of its name in the body is an
        accessor the literal's shape decides, and no other binding's value reads the
        name.  Each element is then bound to its own variable, so an element that
        several accessors read is still evaluated once, and the literal's binding is
        dropped because nothing can read it any more.
        """
        folded = False

        index = 0
        while index < len(bindings):
            name, value_plan = bindings[index]
            index += 1

            container = _literal_container(value_plan)
            if container is None:
                continue

            if any(name in MenaiIROptimizer._collect_used_names(other, set())
                   for bound, other in bindings if bound != name):
                continue

            elements: list[MenaiIRExpr] = []
            element_bindings: list[tuple[str, MenaiIRExpr]] = []
            for element in container[1]:
                element_name = self._gen_temp()
                element_bindings.append((element_name, element))
                elements.append(MenaiIRVariable(name=element_name))

            opt_body = _fold_literal_uses(body_plan, name, container[0], tuple(elements))
            if opt_body is None:
                continue

            body_plan = opt_body
            bindings = bindings[:index - 1] + element_bindings + bindings[index:]
            index += len(element_bindings)
            self._eliminations += 1
            folded = True

        return bindings, body_plan, folded

    def _opt_letrec(self, ir: MenaiIRLetrec, frame_stack: list[int]) -> MenaiIRExpr:
        """
        Drop dead $letrec bindings (unreachable from the evaluation roots).

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

        Tracks shadowing by inner binding forms and lambda
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

    def _opt_call(self, ir: MenaiIRCall, frame_stack: list[int]) -> MenaiIRExpr:
        """Optimize a call, folding a literal test, an accessor, or a constant comparison."""
        func_plan = self._opt(ir.func_plan, frame_stack)
        arg_plans = tuple(self._opt(a, frame_stack) for a in ir.arg_plans)
        folded = _fold_literal_test(ir, arg_plans)

        if folded is None:
            folded = _fold_container_argument(ir, arg_plans)

        if folded is None:
            folded = _fold_constant_comparison(ir, arg_plans)

        if folded is not None:
            self._eliminations += 1
            return folded

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
