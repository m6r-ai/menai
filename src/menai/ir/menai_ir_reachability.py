"""
Menai IR Reachability - pure analysis pass over the IR tree.

Walks a MenaiIRExpr tree and determines which let/letrec bindings are reachable
from the evaluation roots, producing an IRReachability annotation that
downstream passes (MenaiIROptimizer) can consume.

Why reachability rather than use counting
------------------------------------------

Dead-binding elimination drops a binding whose value can never be evaluated.
A binding is dead when no reachable expression references it.  Counting
references is not sufficient: an unreachable *group* of mutually-recursive
bindings references itself, so every member has a non-zero reference count and
a count-based test can never remove any of them.  Tracing from the roots marks
the whole group dead as a unit, which is what makes an unused mutually-recursive
group (such as the prelude's regexp parser) eliminable.

Frame identity
--------------

The optimizer threads a frame_stack through its walk, built from the use
counter's lambda_frame_ids.  This pass therefore takes that same mapping as
input rather than allocating frame ids of its own: a lambda's frame id must be
the one the optimizer will push when it descends into that lambda.

The scope conventions mirror MenaiIRUseCounter exactly, because both analyses
must resolve a variable reference to the same binding.  The two differ only in
which expressions they visit: the use counter visits every reference; this pass
visits only expressions that can be evaluated, and follows a binding's value
plan only once the binding is known to be live.
"""

from dataclasses import dataclass, field

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


@dataclass
class IRReachability:
    """
    Reachability annotation for an IR tree.

    live
        Maps frame_id to the set of binding_ids that are reachable from the
        evaluation roots within that frame.  A binding_id is the id() of the
        binding tuple (name, value_plan) from a MenaiIRLet or MenaiIRLetrec.
    """
    live: dict[int, set[int]] = field(default_factory=dict)

    def is_live(self, frame_id: int, binding_id: int) -> bool:
        """Return True if the binding is reachable from the evaluation roots."""
        return binding_id in self.live.get(frame_id, ())


class MenaiIRReachability:
    """
    Pure analysis pass: determine which let/letrec bindings are reachable.

    No transformation is performed; the IR is not modified.

    Usage::

        counts = MenaiIRUseCounter().count(ir)
        reach = MenaiIRReachability().analyze(ir, counts.lambda_frame_ids)
        if not reach.is_live(frame_id=0, binding_id=id(binding)):
            ...  # binding is dead
    """

    def analyze(
        self,
        ir: MenaiIRExpr,
        lambda_frame_ids: dict[int, int],
    ) -> IRReachability:
        """
        Trace *ir* from its evaluation roots and return an IRReachability.

        Args:
            ir: Root of the IR tree.
            lambda_frame_ids: Mapping of id(MenaiIRLambda) to frame id, as
                produced by MenaiIRUseCounter.  Used so this pass assigns a
                lambda the same frame id the optimizer will push for it.

        Returns:
            IRReachability annotation for the entire tree.
        """
        result = IRReachability()
        result.live[0] = set()

        # Each work item is (expr, scope_stack, frame_id).  scope_stack is a
        # list of dicts mapping name -> (frame_id, binding_id), innermost last,
        # exactly as in MenaiIRUseCounter.
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]] = [
            (ir, [{}], 0)
        ]

        # A binding's value plan is evaluated in the scope that was in effect
        # where the binding was defined, so record that scope when the binding
        # is first seen.  Keyed by binding_id.
        binding_defs: dict[int, tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]] = {}

        while work:
            expr, scope_stack, frame_id = work.pop()
            self._visit(expr, scope_stack, frame_id, result, binding_defs, work, lambda_frame_ids)

        return result

    def _mark_live(
        self,
        frame_id: int,
        binding_id: int,
        result: IRReachability,
        binding_defs: dict[int, tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
    ) -> None:
        """
        Mark a binding live and, on the first marking, schedule its value plan.

        Scheduling the value plan is what propagates liveness: a live binding's
        value may reference further bindings, which then become live in turn.
        """
        live_in_frame = result.live.setdefault(frame_id, set())
        if binding_id in live_in_frame:
            return

        live_in_frame.add(binding_id)

        definition = binding_defs.get(binding_id)
        if definition is not None:
            value_plan, defining_scope, defining_frame = definition
            work.append((value_plan, defining_scope, defining_frame))

    def _resolve_name(
        self, name: str, scope_stack: list[dict[str, tuple[int, int]]]
    ) -> tuple[int, int] | None:
        """
        Search scope_stack (innermost first) for *name*.
        Returns (frame_id, binding_id) of the defining binding, or None if not found.
        """
        for scope in reversed(scope_stack):
            if name in scope:
                return scope[name]

        return None

    def _visit(
        self,
        ir: MenaiIRExpr,
        scope_stack: list[dict[str, tuple[int, int]]],
        frame_id: int,
        result: IRReachability,
        binding_defs: dict[int, tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        lambda_frame_ids: dict[int, int],
    ) -> None:
        """Visit one reachable expression, scheduling its reachable children."""
        if isinstance(ir, MenaiIRVariable):
            resolved = self._resolve_name(ir.name, scope_stack)
            if resolved is not None:
                def_frame_id, binding_id = resolved
                self._mark_live(def_frame_id, binding_id, result, binding_defs, work)

        elif isinstance(ir, MenaiIRLambda):
            self._visit_lambda(ir, scope_stack, frame_id, result, work, lambda_frame_ids)

        elif isinstance(ir, MenaiIRLet):
            self._visit_let(ir, scope_stack, frame_id, binding_defs, work)

        elif isinstance(ir, MenaiIRLetrec):
            self._visit_letrec(ir, scope_stack, frame_id, binding_defs, work)

        elif isinstance(ir, MenaiIRLoop):
            for init in ir.init_plans:
                work.append((init, scope_stack, frame_id))

            loop_scope: dict[str, tuple[int, int]] = {
                name: (frame_id, id(ir)) for name in ir.params
            }
            work.append((ir.body_plan, scope_stack + [loop_scope], frame_id))

        elif isinstance(ir, MenaiIRRecur):
            for arg in ir.arg_plans:
                work.append((arg, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRIf):
            work.append((ir.condition_plan, scope_stack, frame_id))
            work.append((ir.then_plan, scope_stack, frame_id))
            work.append((ir.else_plan, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRCall):
            work.append((ir.func_plan, scope_stack, frame_id))
            for arg in ir.arg_plans:
                work.append((arg, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildList):
            for elem in ir.element_plans:
                work.append((elem, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildDict):
            for key_plan, val_plan in ir.pair_plans:
                work.append((key_plan, scope_stack, frame_id))
                work.append((val_plan, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildSet):
            for elem in ir.element_plans:
                work.append((elem, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildVector):
            for elem in ir.element_plans:
                work.append((elem, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildStruct):
            for field_plan in ir.field_plans:
                work.append((field_plan, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRBuildEnum):
            pass

        elif isinstance(ir, MenaiIRReturn):
            work.append((ir.value_plan, scope_stack, frame_id))

        elif isinstance(ir, MenaiIRError):
            work.append((ir.message, scope_stack, frame_id))

        elif isinstance(ir, (MenaiIRConstant, MenaiIRQuote, MenaiIREmptyList)):
            pass  # Leaf nodes — nothing to trace.

        else:
            raise TypeError(f"MenaiIRReachability: unhandled IR node type {type(ir).__name__}")

    def _visit_lambda(
        self,
        ir: MenaiIRLambda,
        scope_stack: list[dict[str, tuple[int, int]]],
        frame_id: int,
        result: IRReachability,
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        lambda_frame_ids: dict[int, int],
    ) -> None:
        """
        Trace a reachable lambda.

        The lambda's free-var plans are evaluated in the enclosing scope, so
        they are scheduled against the current scope_stack and frame.  The body
        runs in the lambda's own frame — the id the optimizer will push — whose
        scope holds the params and captured free vars.
        """
        for plan in ir.sibling_free_var_plans + ir.outer_free_var_plans:
            work.append((plan, scope_stack, frame_id))

        lambda_frame_id = lambda_frame_ids.get(id(ir))
        if lambda_frame_id is None:
            # The use counter assigns a frame to every lambda it walks, and it
            # walks the whole tree, so a reachable lambda always has one.
            raise TypeError("MenaiIRReachability: reachable lambda has no frame id")

        result.live.setdefault(lambda_frame_id, set())

        lambda_scope: dict[str, tuple[int, int]] = {}
        for name in ir.params + ir.sibling_free_vars + ir.outer_free_vars:
            synthetic_id = -id(ir) - hash(name)
            lambda_scope[name] = (lambda_frame_id, synthetic_id)

        work.append((ir.body_plan, scope_stack + [lambda_scope], lambda_frame_id))

    def _visit_let(
        self,
        ir: MenaiIRLet,
        scope_stack: list[dict[str, tuple[int, int]]],
        frame_id: int,
        binding_defs: dict[int, tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
    ) -> None:
        """
        Trace a reachable let.

        Binding values are evaluated in the enclosing scope (parallel let
        semantics) and are scheduled only once their binding is marked live.
        The body is reachable and is scheduled with the let's own scope.
        """
        let_scope: dict[str, tuple[int, int]] = {}
        for binding in ir.bindings:
            name, value_plan, *_ = binding
            binding_id = id(binding)
            binding_defs[binding_id] = (value_plan, scope_stack, frame_id)
            let_scope[name] = (frame_id, binding_id)

        work.append((ir.body_plan, scope_stack + [let_scope], frame_id))

    def _visit_letrec(
        self,
        ir: MenaiIRLetrec,
        scope_stack: list[dict[str, tuple[int, int]]],
        frame_id: int,
        binding_defs: dict[int, tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
        work: list[tuple[MenaiIRExpr, list[dict[str, tuple[int, int]]], int]],
    ) -> None:
        """
        Trace a reachable letrec.

        All binding names are in scope for both binding values and the body
        (mutual recursion).  Binding values are scheduled only once their
        binding is marked live, so an unreachable group is never traced.
        """
        letrec_scope: dict[str, tuple[int, int]] = {}
        inner_stack = scope_stack + [letrec_scope]
        for binding in ir.bindings:
            name, value_plan, *_ = binding
            binding_id = id(binding)
            binding_defs[binding_id] = (value_plan, inner_stack, frame_id)
            letrec_scope[name] = (frame_id, binding_id)

        work.append((ir.body_plan, inner_stack, frame_id))
