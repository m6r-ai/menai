"""
CFG builder for the Menai compiler.

Translates a symbolic MenaiIR tree into a MenaiCFGFunction in SSA form.
"""

from dataclasses import dataclass, field

from menai.cfg.menai_cfg import (
    MenaiCFGApplyInstr,
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGParamInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeEnumInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGPhiInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGTailApplyTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGValue,
    MenaiCFGInstr,
    MenaiCFGTerminator,
)
from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRBuildDict,
    MenaiIRBuildList,
    MenaiIRBuildSet,
    MenaiIRBuildVector,
    MenaiIRBuildStruct,
    MenaiIRBuildEnum,
    MenaiIREmptyList,
    MenaiIRExpr,
    MenaiIRError,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLoop,
    MenaiIRRecur,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRQuote,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.menai_value import MenaiList, Menai_VECTOR_EMPTY


@dataclass
class _DraftBlock:
    """
    A mutable block used only while the builder is constructing a function.

    The builder emits instructions and terminators incrementally, which is far
    simpler against a mutable block than against the frozen MenaiCFGBlock.  The
    draft is converted to a frozen MenaiCFGBlock when the function is finalised,
    so no draft ever escapes the builder.  The frozen CFG is the value that
    passes see (ADR-0033).
    """
    id: int
    label: str
    instrs: list[MenaiCFGInstr] = field(default_factory=list)
    patch_instrs: list[MenaiCFGPatchClosureInstr] = field(default_factory=list)
    terminator: MenaiCFGTerminator | None = None


def _freeze_block(draft: _DraftBlock) -> MenaiCFGBlock:
    """Convert a builder draft block into a frozen MenaiCFGBlock."""
    return MenaiCFGBlock(
        id=draft.id,
        label=draft.label,
        instrs=tuple(draft.instrs),
        patch_instrs=tuple(draft.patch_instrs),
        terminator=draft.terminator,
    )


@dataclass
class MenaiCFGScope:
    """
    A single lexical scope frame mapping variable names to SSA values.

    Frames are chained via `parent`; lookup walks innermost-first.
    """
    bindings: dict[str, MenaiCFGValue] = field(default_factory=dict)
    parent: 'MenaiCFGScope | None' = None

    def lookup(self, name: str) -> MenaiCFGValue | None:
        """Search this frame and all ancestors for `name`."""
        frame: MenaiCFGScope | None = self
        while frame is not None:
            if name in frame.bindings:
                return frame.bindings[name]

            frame = frame.parent

        return None

    def bind(self, name: str, value: MenaiCFGValue) -> None:
        """Add a binding to this (innermost) frame."""
        self.bindings[name] = value

    def child(self) -> 'MenaiCFGScope':
        """Return a new child scope whose parent is this frame."""
        return MenaiCFGScope(parent=self)


@dataclass
class _FunctionState:
    """
    Mutable state threaded through the build of a single MenaiCFGFunction.

    Isolated per lambda so that nested lambdas get their own counters and
    block lists.
    """
    blocks: list[_DraftBlock] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    free_vars: list[str] = field(default_factory=list)
    is_variadic: bool = False
    binding_name: str | None = None
    source_line: int = 0
    source_file: str = ""
    value_counter: int = 0
    block_counter: int = 0
    self_value: 'MenaiCFGValue | None' = None  # SSA value of the function's own self-capture
                                                # free var, set for letrec-bound lambdas only.
    loop_context: 'tuple[_DraftBlock, list[MenaiCFGValue]] | None' = None
    # When inside a MenaiIRLoop, holds (loop_entry_block, param_ssa_values) so
    # that MenaiIRRecur can emit a SelfLoopTerm with the correct target and
    # destination values.  None at all other times.

    def new_value(self, hint: str = "") -> MenaiCFGValue:
        """Allocate a new SSA value with an optional hint for debugging."""
        v = MenaiCFGValue(id=self.value_counter, hint=hint)
        self.value_counter += 1
        return v

    def new_block(self, label: str) -> _DraftBlock:
        """Allocate a new CFG block with the given label."""
        b = _DraftBlock(id=self.block_counter, label=label)
        self.block_counter += 1
        self.blocks.append(b)
        return b

    def freeze(self) -> MenaiCFGFunction:
        """Convert the accumulated draft blocks into a frozen MenaiCFGFunction."""
        return MenaiCFGFunction(
            blocks=tuple(_freeze_block(b) for b in self.blocks),
            params=tuple(self.params),
            free_vars=tuple(self.free_vars),
            is_variadic=self.is_variadic,
            binding_name=self.binding_name,
            source_line=self.source_line,
            source_file=self.source_file,
        )


class MenaiCFGBuilder:
    """
    Builds a MenaiCFGFunction from a (symbolic, unaddressed) MenaiIR tree.

    Usage::

        cfg = MenaiCFGBuilder().build(ir_expr)
    """

    def __init__(self) -> None:
        # Instance-level letrec sibling context.  Set to the sibling-name set
        # during Phase 2b of _build_letrec (evaluating non-lambda binding RHS
        # expressions) so that _build_lambda_expr can detect lambdas that have
        # sibling captures and need PATCH_CLOSURE treatment.  None at all other
        # times, including during normal lambda-only letrec Phase 1.
        self._letrec_sibling_names: set | None = None

        # Collected during Phase 2b: (closure_val, ir_lambda) pairs for lambdas
        # embedded in non-lambda letrec binding RHS expressions that have at
        # least one sibling capture.  Processed (patched) after Phase 2b.
        self._letrec_deferred_patches: list[tuple[MenaiCFGValue, MenaiIRLambda]] | None = None

    def build(self, ir: MenaiIRExpr) -> MenaiCFGFunction:
        """
        Build the top-level MenaiCFGFunction from an IR expression.

        Args:
            ir: Root of the optimised, symbolic IR tree (output of the IR
                optimisation passes).

        Returns:
            MenaiCFGFunction for the top-level module body.
        """
        state = _FunctionState()
        entry = state.new_block("entry")
        scope = MenaiCFGScope()

        result_val, current_block = self._build_expr(
            ir, entry, scope, state, tail=True
        )

        # If the expression returned a value rather than terminating the block,
        # wrap it in a return.
        if current_block.terminator is None:
            current_block.terminator = MenaiCFGReturnTerm(value=result_val)

        return state.freeze()

    def _build_expr(
        self,
        ir: MenaiIRExpr,
        block: _DraftBlock,
        scope: MenaiCFGScope,
        state: _FunctionState,
        tail: bool,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Emit instructions for `ir` into `block` (and successor blocks as
        needed), returning the SSA value that holds the result and the
        (possibly new) current block after emission.

        When `tail` is True the expression is in tail position: calls become
        tail-call terminators and the caller will not emit a further return.

        Args:
            ir:    IR node to lower.
            block: Current basic block to emit into.
            scope: Current lexical scope.
            state: Per-function mutable state (counters, block list).
            tail:  True if this expression is in tail position.

        Returns:
            (result_value, current_block) — the SSA value produced and the
            block that is "current" after emission (may differ from `block`
            if new blocks were created, e.g. for if-expressions).
        """
        if isinstance(ir, MenaiIRConstant):
            return self._build_constant(ir, block, state)

        if isinstance(ir, MenaiIREmptyList):
            return self._build_empty_list(block, state)

        if isinstance(ir, MenaiIRQuote):
            return self._build_quote(ir, block, state)

        if isinstance(ir, MenaiIRVariable):
            return self._build_variable(ir, block, scope)

        if isinstance(ir, MenaiIRIf):
            return self._build_if(ir, block, scope, state, tail)

        if isinstance(ir, MenaiIRLet):
            return self._build_let(ir, block, scope, state, tail)

        if isinstance(ir, MenaiIRLetrec):
            return self._build_letrec(ir, block, scope, state, tail)

        if isinstance(ir, MenaiIRLoop):
            return self._build_loop(ir, block, scope, state, tail)

        if isinstance(ir, MenaiIRRecur):
            return self._build_recur(ir, block, scope, state)

        if isinstance(ir, MenaiIRLambda):
            return self._build_lambda_expr(ir, block, scope, state)

        if isinstance(ir, MenaiIRCall):
            return self._build_call(ir, block, scope, state, tail)

        if isinstance(ir, MenaiIRBuildList):
            return self._build_list(ir, block, scope, state)

        if isinstance(ir, MenaiIRBuildDict):
            return self._build_dict(ir, block, scope, state)

        if isinstance(ir, MenaiIRBuildSet):
            return self._build_set(ir, block, scope, state)

        if isinstance(ir, MenaiIRBuildVector):
            return self._build_vector(ir, block, scope, state)

        if isinstance(ir, MenaiIRBuildStruct):
            return self._build_struct(ir, block, scope, state)

        if isinstance(ir, MenaiIRBuildEnum):
            return self._build_enum(ir, block, state)

        if isinstance(ir, MenaiIRReturn):
            # MenaiIRReturn is the IR tree's explicit return wrapper.
            # We honour tail=True here since the IR already marked this.
            return self._build_expr(ir.value_plan, block, scope, state, tail=True)

        if isinstance(ir, MenaiIRError):
            return self._build_error(ir, block, scope, state)

        raise TypeError(f"MenaiCFGBuilder: unhandled IR node {type(ir).__name__}")

    def _build_constant(
        self, ir: MenaiIRConstant, block: _DraftBlock, state: _FunctionState
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build a CFG constant instruction from an IR constant."""
        result = state.new_value("const")
        block.instrs.append(MenaiCFGConstInstr(result=result, value=ir.value))
        return result, block

    def _build_empty_list(
        self, block: _DraftBlock, state: _FunctionState
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build a CFG constant instruction for an empty list literal."""
        result = state.new_value("empty_list")
        block.instrs.append(MenaiCFGConstInstr(result=result, value=MenaiList()))
        return result, block

    def _build_quote(
        self, ir: MenaiIRQuote, block: _DraftBlock, state: _FunctionState
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build a CFG constant instruction from a quoted value."""
        result = state.new_value("quoted")
        block.instrs.append(MenaiCFGConstInstr(result=result, value=ir.quoted_value))
        return result, block

    def _build_variable(
        self, ir: MenaiIRVariable, block: _DraftBlock, scope: MenaiCFGScope
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Resolve a variable reference to a CFG value."""
        val = scope.lookup(ir.name)
        assert val is not None, (
            f"MenaiCFGBuilder: unresolved variable {ir.name!r}"
        )
        return val, block

    def _build_error(
        self, ir: MenaiIRError, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Evaluate the error message and terminate the block with a raise."""
        # Evaluate the message expression, then terminate the block.
        # The returned placeholder value is never used (the block has no successors).
        msg_val, block = self._build_expr(ir.message, block, scope, state, tail=False)
        block.terminator = MenaiCFGRaiseTerm(message=msg_val)
        placeholder = state.new_value("error")
        return placeholder, block

    def _build_if(
        self, ir: MenaiIRIf, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState, tail: bool
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build conditional branching CFG from an if expression."""
        # If the condition is (boolean-not <inner>), emit <inner> as the branch
        # condition and swap then/else.  This avoids a BOOLEAN_NOT instruction
        # followed by a conditional jump in the opposite direction.
        condition_plan = ir.condition_plan
        negate = (
            isinstance(condition_plan, MenaiIRCall)
            and condition_plan.is_builtin
            and condition_plan.builtin_name == 'boolean-not'
            and len(condition_plan.arg_plans) == 1
        )
        if negate:
            assert isinstance(condition_plan, MenaiIRCall)
            inner_plan = condition_plan.arg_plans[0]

        else:
            inner_plan = condition_plan

        cond_val, block = self._build_expr(inner_plan, block, scope, state, tail=False)

        # Create the branch target blocks.  The join block is created lazily
        # below — only if at least one branch actually reaches it.
        then_block = state.new_block("then")
        else_block = state.new_block("else")

        block.terminator = MenaiCFGBranchTerm(
            cond=cond_val,
            true_block=(else_block if negate else then_block).id,
            false_block=(then_block if negate else else_block).id,
        )

        # Build then branch.
        then_val, then_exit = self._build_expr(
            ir.then_plan, then_block, scope, state, tail=tail
        )

        # Build else branch.
        else_val, else_exit = self._build_expr(
            ir.else_plan, else_block, scope, state, tail=tail
        )

        then_falls_through = then_exit.terminator is None
        else_falls_through = else_exit.terminator is None

        if not then_falls_through and not else_falls_through:
            # Both branches are tail-terminated; no join block needed at all.
            # Return else_exit as the current block — it is already terminated,
            # so the caller's terminator-is-None guard will not emit a return.
            placeholder = state.new_value("if_result")
            return placeholder, else_exit

        # At least one branch falls through — create the join block now.
        join_block = state.new_block("join")

        if then_falls_through:
            then_exit.terminator = MenaiCFGJumpTerm(target=join_block.id)

        if else_falls_through:
            else_exit.terminator = MenaiCFGJumpTerm(target=join_block.id)

        # If only one branch reaches join, the value is unambiguous — no phi.
        # The join block will be empty and SimplifyBlocks will eliminate it.
        # If both branches reach it, emit a phi to merge the two values.
        if then_falls_through and not else_falls_through:
            return then_val, join_block

        if else_falls_through and not then_falls_through:
            return else_val, join_block

        phi_result = state.new_value("if_result")
        join_block.instrs.append(MenaiCFGPhiInstr(
            result=phi_result,
            incoming=((then_val, then_exit.id), (else_val, else_exit.id)),
        ))
        return phi_result, join_block

    def _build_let(
        self, ir: MenaiIRLet, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState, tail: bool
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build a let-binding scope, evaluating bindings then the body in a child scope."""
        # Binding values are evaluated in the outer scope (parallel let).
        binding_vals: list[tuple[str, MenaiCFGValue]] = []
        for name, value_plan in ir.bindings:
            val, block = self._build_expr(value_plan, block, scope, state, tail=False)
            binding_vals.append((name, val))

        # Body is evaluated in a child scope that contains all binding names.
        body_scope = scope.child()
        for name, val in binding_vals:
            body_scope.bind(name, val)

        return self._build_expr(ir.body_plan, block, body_scope, state, tail=tail)

    def _build_letrec(
        self, ir: MenaiIRLetrec, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState, tail: bool
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a letrec in three phases.

        Phase 1: For lambda bindings, build child CFGs and emit
                 MenaiCFGMakeClosureInstr with only OUTER (non-sibling)
                 captures.  Sibling captures cannot be loaded yet because the
                 sibling closures haven't been created.  The VM pre-allocates
                 None slots for the full free_vars list; PATCH_CLOSURE fills
                 in siblings.  Non-lambda bindings are deferred to Phase 2b.

        Phase 2: Register all closure SSA values in letrec_scope so that
                 sibling references resolve.

        Phase 2b: Evaluate non-lambda binding values now that all sibling
                 closure SSA values are in letrec_scope.  Any lambdas nested
                 inside the value expression that capture sibling names are
                 built with needs_patching=True and their sibling captures are
                 collected for Phase 3.

        Phase 3: Emit MenaiCFGPatchClosureInstr for every sibling capture of
                 every lambda binding, using the full capture_index from the
                 child function's free_vars list.
        """
        sibling_names = {name for name, _ in ir.bindings}
        # Build a child scope for letrec-bound names (populated in Phase 2).
        letrec_scope = scope.child()

        # Phase 1: build each lambda's CFG and emit MAKE_CLOSURE with only
        # outer captures (those not in sibling_names).
        binding_vals: dict[str, MenaiCFGValue] = {}

        for name, value_plan in ir.bindings:
            if not isinstance(value_plan, MenaiIRLambda):
                # Non-lambda binding (e.g. a list or call expression whose RHS
                # contains a nested lambda closing over this binding's name).
                # Allocate a placeholder SSA value now so sibling lambdas can
                # reference this name; the real value is computed in Phase 2b.
                placeholder = state.new_value(name)
                binding_vals[name] = placeholder
                continue

            child_func = self._build_lambda_function(value_plan)

            # Collect outer captures only — evaluate them in the current scope.
            # Sibling captures are deferred to Phase 3 (PATCH_CLOSURE).
            outer_captures: list[MenaiCFGValue] = []
            for fv_plan, fv_name in zip(
                value_plan.sibling_free_var_plans + value_plan.outer_free_var_plans,
                value_plan.sibling_free_vars + value_plan.outer_free_vars,
            ):
                if fv_name not in sibling_names:
                    fv_val, block = self._build_expr(fv_plan, block, scope, state, tail=False)
                    outer_captures.append(fv_val)

            has_sibling_captures = bool(value_plan.sibling_free_vars)
            closure_val = state.new_value(name)
            make_instr = MenaiCFGMakeClosureInstr(
                result=closure_val,
                function=child_func,
                captures=tuple(outer_captures),
                needs_patching=has_sibling_captures,
            )
            block.instrs.append(make_instr)
            binding_vals[name] = closure_val

        # Phase 2: register all closure values in letrec_scope so sibling
        # references resolve during Phase 3.
        for name, closure_val in binding_vals.items():
            letrec_scope.bind(name, closure_val)

        # Phase 2b: evaluate non-lambda binding values now that all sibling
        # closure SSA values are in letrec_scope.  Any nested lambdas in the
        # value expression that have sibling captures are intercepted by
        # _build_lambda_expr (which checks self._letrec_sibling_names) and
        # recorded in self._letrec_deferred_patches for Phase 3b below.
        prev_sibling_names = self._letrec_sibling_names
        prev_deferred_patches = self._letrec_deferred_patches
        self._letrec_sibling_names = sibling_names
        self._letrec_deferred_patches = []
        for name, value_plan in ir.bindings:
            if isinstance(value_plan, MenaiIRLambda):
                continue

            real_val, block = self._build_expr(value_plan, block, letrec_scope, state, tail=False)
            letrec_scope.bind(name, real_val)

        deferred_non_lambda_patches = self._letrec_deferred_patches
        self._letrec_sibling_names = prev_sibling_names
        self._letrec_deferred_patches = prev_deferred_patches

        # Phase 3: emit PATCH_CLOSURE for every sibling capture of every lambda.
        # capture_index is the position in the child function's full free_vars
        # list (sibling_free_vars + outer_free_vars), which is what the VM uses.
        # Appended to block.instrs (not patch_instrs) so they execute before
        # the letrec body, which is also built into the same block's instrs.
        for name, value_plan in ir.bindings:
            if not isinstance(value_plan, MenaiIRLambda):
                continue  # Non-lambda bindings have no closure captures to patch

            closure_val = binding_vals[name]

            for capture_index, fv_name in enumerate(
                value_plan.sibling_free_vars + value_plan.outer_free_vars
            ):
                if fv_name in {n for n, _ in ir.bindings}:
                    patch_val = letrec_scope.lookup(fv_name)
                    assert patch_val is not None
                    block.instrs.append(MenaiCFGPatchClosureInstr(
                        closure=closure_val,
                        capture_index=capture_index,
                        value=patch_val,
                    ))

        # Phase 3b: emit PATCH_CLOSURE for lambdas embedded in non-lambda
        # binding RHS expressions (collected during Phase 2b).  After Phase 2b,
        # all non-lambda binding names are in letrec_scope with their real
        # values, so we can patch the sibling captures now.
        for closure_val, lambda_ir in deferred_non_lambda_patches:
            for capture_index, fv_name in enumerate(
                lambda_ir.sibling_free_vars + lambda_ir.outer_free_vars
            ):
                if fv_name in sibling_names:
                    patch_val = letrec_scope.lookup(fv_name)
                    assert patch_val is not None, (
                        f"MenaiCFGBuilder: sibling free var {fv_name!r} not in letrec_scope"
                    )
                    block.instrs.append(MenaiCFGPatchClosureInstr(
                        closure=closure_val,
                        capture_index=capture_index,
                        value=patch_val,
                    ))

        # Build the body with all letrec names in scope.
        return self._build_expr(ir.body_plan, block, letrec_scope, state, tail=tail)

    def _build_loop(
        self, ir: MenaiIRLoop, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState, tail: bool
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a MenaiIRLoop as an inline loop in the current function.

        The init plans are evaluated in the current scope, producing SSA
        values that become the initial values of the loop-carried variables.
        A new block is created as the loop entry point.  The loop params are
        bound to fresh phi results in the loop entry block, and the body is
        built starting from that block.

        MenaiIRRecur nodes in the body emit MenaiCFGSelfLoopTerm with
        param_vals set to the loop param SSA values and target set to the
        loop entry block.
        """
        # Evaluate init plans in the current scope.
        init_vals: list[MenaiCFGValue] = []
        for init_plan in ir.init_plans:
            init_val, block = self._build_expr(init_plan, block, scope, state, tail=False)
            init_vals.append(init_val)

        # Create the loop entry block.  The block before the loop jumps to it.
        loop_entry = state.new_block("loop_entry")
        pre_loop_block = block
        block.terminator = MenaiCFGJumpTerm(target=loop_entry.id)

        # Create phi nodes for the loop params in the loop entry block.  Each
        # phi merges the init value (from the pre-loop block) with the
        # back-edge value (from the recur block, filled in by _build_recur).
        param_vals: list[MenaiCFGValue] = []
        loop_scope = scope.child()
        for i, param_name in enumerate(ir.params):
            param_val = state.new_value(param_name)
            loop_entry.instrs.append(MenaiCFGPhiInstr(
                result=param_val,
                incoming=((init_vals[i], pre_loop_block.id),),
            ))
            param_vals.append(param_val)
            loop_scope.bind(param_name, param_val)

        # Save the previous loop context and set the new one.  Nested loops
        # restore the outer context when they finish.
        prev_loop_context = state.loop_context
        state.loop_context = (loop_entry, param_vals)

        # Build the body starting from the loop entry block.
        result_val, current_block = self._build_expr(
            ir.body_plan, loop_entry, loop_scope, state, tail=tail
        )

        # Restore the previous loop context.
        state.loop_context = prev_loop_context

        # In tail position the loop's result is the function's result, so an
        # unterminated block gets a return.  In non-tail position the block
        # must fall through so the enclosing expression can consume the value.
        if current_block.terminator is None and tail:
            current_block.terminator = MenaiCFGReturnTerm(value=result_val)

        return result_val, current_block

    def _build_recur(
        self, ir: MenaiIRRecur, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a loop back-edge (MenaiIRRecur) as a MenaiCFGSelfLoopTerm.

        The recur's args are moved into the loop param SSA values, and control
        jumps to the loop entry block.
        """
        assert state.loop_context is not None, (
            "MenaiCFGBuilder: MenaiIRRecur encountered outside a MenaiIRLoop"
        )
        loop_entry, param_vals = state.loop_context

        arg_vals: list[MenaiCFGValue] = []
        for arg_plan in ir.arg_plans:
            arg_val, block = self._build_expr(arg_plan, block, scope, state, tail=False)
            arg_vals.append(arg_val)

        # Add the back-edge block as a predecessor to each phi node.
        for i, param_val in enumerate(param_vals):
            for instr_idx, instr in enumerate(loop_entry.instrs):
                if isinstance(instr, MenaiCFGPhiInstr) and instr.result is param_val:
                    loop_entry.instrs[instr_idx] = MenaiCFGPhiInstr(
                        result=instr.result,
                        incoming=instr.incoming + ((arg_vals[i], block.id),),
                    )
                    break

        block.terminator = MenaiCFGSelfLoopTerm(
            args=tuple(arg_vals),
            param_vals=tuple(param_vals),
            target=loop_entry.id,
        )
        placeholder = state.new_value("recur")
        return placeholder, block

    def _build_lambda_expr(
        self, ir: MenaiIRLambda, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a lambda that appears as a value expression (not inside letrec).

        Recursively builds the child MenaiCFGFunction, then emits a
        MenaiCFGMakeClosureInstr in the parent block.

        Captures are loaded by evaluating each free_var_plan as an IR
        expression.  After copy propagation these may be constants or other
        non-variable expressions, not necessarily MenaiIRVariable nodes, so
        we dispatch through _build_expr rather than doing a scope lookup by name.

        Letrec Phase 2b interception: if self._letrec_sibling_names is set (we
        are inside the evaluation of a non-lambda letrec binding's RHS) and
        this lambda has sibling captures, we treat it like a letrec lambda:
        emit MAKE_CLOSURE with needs_patching=True and record it in
        self._letrec_deferred_patches so Phase 3b can PATCH_CLOSURE the
        sibling captures after all non-lambda binding values are available.
        """
        child_func = self._build_lambda_function(ir)

        # Check for letrec Phase 2b interception.
        sibling_names = self._letrec_sibling_names
        has_sibling_captures = bool(ir.sibling_free_vars) and sibling_names is not None
        if has_sibling_captures:
            assert sibling_names is not None
            # Evaluate only outer (non-sibling) captures now.
            outer_captures: list[MenaiCFGValue] = []
            for fv_plan, fv_name in zip(
                ir.sibling_free_var_plans + ir.outer_free_var_plans,
                ir.sibling_free_vars + ir.outer_free_vars,
            ):
                if fv_name not in sibling_names:
                    fv_val, block = self._build_expr(fv_plan, block, scope, state, tail=False)
                    outer_captures.append(fv_val)

            result = state.new_value(ir.binding_name or "lambda")
            block.instrs.append(MenaiCFGMakeClosureInstr(
                result=result,
                function=child_func,
                captures=tuple(outer_captures),
                needs_patching=True,
            ))
            assert self._letrec_deferred_patches is not None
            self._letrec_deferred_patches.append((result, ir))
            return result, block

        # Evaluate each capture plan in the current block and scope.
        captures: list[MenaiCFGValue] = []
        for fv_plan in ir.sibling_free_var_plans + ir.outer_free_var_plans:
            fv_val, block = self._build_expr(fv_plan, block, scope, state, tail=False)
            captures.append(fv_val)

        result = state.new_value(ir.binding_name or "lambda")
        block.instrs.append(MenaiCFGMakeClosureInstr(
            result=result,
            function=child_func,
            captures=tuple(captures),
        ))
        return result, block

    def _build_lambda_function(self, ir: MenaiIRLambda) -> MenaiCFGFunction:
        """
        Recursively build a MenaiCFGFunction for a MenaiIRLambda node.

        The child function has its own block list, value counter, and block
        counter.  The enclosing scope is NOT accessible from within the child
        (all captures are explicit in ir.sibling_free_vars / outer_free_vars).

        Args:
            ir: The lambda IR node.

        Returns:
            A fully-built MenaiCFGFunction for this lambda.
        """
        state = _FunctionState(
            params=list(ir.params),
            free_vars=list(ir.sibling_free_vars + ir.outer_free_vars),
            is_variadic=ir.is_variadic,
            binding_name=ir.binding_name,
            source_line=ir.source_line,
            source_file=ir.source_file,
        )
        entry = state.new_block("entry")

        # Build the lambda's own scope: params first, then captured free vars.
        lambda_scope = MenaiCFGScope()

        for idx, param_name in enumerate(ir.params):
            param_val = state.new_value(param_name)
            entry.instrs.append(MenaiCFGParamInstr(
                result=param_val,
                index=idx,
                param_name=param_name,
            ))
            lambda_scope.bind(param_name, param_val)

        free_var_names = ir.sibling_free_vars + ir.outer_free_vars
        for idx, fv_name in enumerate(free_var_names):
            fv_val = state.new_value(fv_name)
            entry.instrs.append(MenaiCFGFreeVarInstr(
                result=fv_val,
                index=idx,
                var_name=fv_name,
            ))
            lambda_scope.bind(fv_name, fv_val)
            if fv_name == ir.binding_name:
                state.self_value = fv_val

        # Build the body.
        result_val, current_block = self._build_expr(
            ir.body_plan, entry, lambda_scope, state, tail=True
        )

        if current_block.terminator is None:
            current_block.terminator = MenaiCFGReturnTerm(value=result_val)

        return state.freeze()

    def _build_call(
        self, ir: MenaiIRCall, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState, tail: bool
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """Build a function call, delegating to builtin or user-call builders."""
        if ir.is_builtin:
            return self._build_builtin_call(ir, block, scope, state, tail)

        # Evaluate arguments.
        arg_vals: list[MenaiCFGValue] = []
        for arg_plan in ir.arg_plans:
            arg_val, block = self._build_expr(arg_plan, block, scope, state, tail=False)
            arg_vals.append(arg_val)

        # Evaluate the function expression.
        func_val, block = self._build_expr(ir.func_plan, block, scope, state, tail=False)

        if tail:
            # Detect direct self-recursive tail call.
            if (isinstance(ir.func_plan, MenaiIRVariable)
                    and ir.func_plan.name == state.binding_name
                    and state.self_value is not None
                    and func_val is state.self_value):
                block.terminator = MenaiCFGSelfLoopTerm(args=tuple(arg_vals))
                placeholder = state.new_value("self_loop")
                return placeholder, block

            block.terminator = MenaiCFGTailCallTerm(func=func_val, args=tuple(arg_vals))
            placeholder = state.new_value("tail_call")
            return placeholder, block

        result = state.new_value("call_result")
        block.instrs.append(MenaiCFGCallInstr(
            result=result,
            func=func_val,
            args=tuple(arg_vals),
        ))
        return result, block

    def _build_list(
        self, ir: MenaiIRBuildList, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a list literal.

        Evaluates each element plan, then emits a single MenaiCFGMakeListInstr
        carrying all element values.  The VM codegen lowers this to MAKE_LIST,
        which allocates the list in one call.
        """
        elem_vals: list[MenaiCFGValue] = []
        for elem_plan in ir.element_plans:
            elem_val, block = self._build_expr(elem_plan, block, scope, state, tail=False)
            elem_vals.append(elem_val)

        result = state.new_value("list")
        block.instrs.append(MenaiCFGMakeListInstr(result=result, args=tuple(elem_vals)))
        return result, block

    def _build_vector(
        self, ir: MenaiIRBuildVector, block: _DraftBlock, scope: MenaiCFGScope, state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a vector literal.

        Evaluates each element plan, then emits a single MenaiCFGMakeVectorInstr
        carrying all element values.  The VM codegen lowers this to MAKE_VECTOR,
        which allocates the vector in one call.  When there are zero elements,
        emits a MenaiCFGConstInstr with Menai_VECTOR_EMPTY instead.
        """
        if not ir.element_plans:
            result = state.new_value("empty_vector")
            block.instrs.append(MenaiCFGConstInstr(result=result, value=Menai_VECTOR_EMPTY))
            return result, block

        elem_vals: list[MenaiCFGValue] = []
        for elem_plan in ir.element_plans:
            elem_val, block = self._build_expr(elem_plan, block, scope, state, tail=False)
            elem_vals.append(elem_val)

        result = state.new_value("vector")
        block.instrs.append(MenaiCFGMakeVectorInstr(result=result, args=tuple(elem_vals)))
        return result, block

    def _build_dict(
        self,
        ir: MenaiIRBuildDict,
        block: _DraftBlock,
        scope: MenaiCFGScope,
        state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a dict literal.

        Evaluates each key and value plan, then emits a single
        MenaiCFGMakeDictInstr carrying all pairs.  The VM codegen lowers this
        to MAKE_DICT, which allocates the dict in a single call.
        """
        pair_vals: list[tuple[MenaiCFGValue, MenaiCFGValue]] = []
        for key_plan, val_plan in ir.pair_plans:
            key_val, block = self._build_expr(key_plan, block, scope, state, tail=False)
            val_val, block = self._build_expr(val_plan, block, scope, state, tail=False)
            pair_vals.append((key_val, val_val))

        result = state.new_value("dict")
        block.instrs.append(MenaiCFGMakeDictInstr(result=result, pairs=tuple(pair_vals)))
        return result, block

    def _build_set(
        self,
        ir: MenaiIRBuildSet,
        block: _DraftBlock,
        scope: MenaiCFGScope,
        state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a set literal.

        Evaluates each element plan, then emits a single MenaiCFGMakeSetInstr
        carrying all element values.  The VM codegen lowers this to MAKE_SET,
        which allocates the set in a single call.
        """
        elem_vals: list[MenaiCFGValue] = []
        for elem_plan in ir.element_plans:
            elem_val, block = self._build_expr(elem_plan, block, scope, state, tail=False)
            elem_vals.append(elem_val)

        result = state.new_value("set")
        block.instrs.append(MenaiCFGMakeSetInstr(result=result, args=tuple(elem_vals)))
        return result, block

    def _build_struct(
        self,
        ir: MenaiIRBuildStruct,
        block: _DraftBlock,
        scope: MenaiCFGScope,
        state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build a struct constructor call.

        Evaluates each field value plan then emits a MenaiCFGMakeStructInstr
        carrying the compile-time MenaiStructType descriptor directly.
        """
        field_vals: list[MenaiCFGValue] = []
        for field_plan in ir.field_plans:
            field_val, block = self._build_expr(field_plan, block, scope, state, tail=False)
            field_vals.append(field_val)

        result = state.new_value(f"struct_{ir.struct_type.name}")
        block.instrs.append(MenaiCFGMakeStructInstr(
            result=result,
            struct_type=ir.struct_type,
            args=tuple(field_vals),
        ))
        return result, block

    def _build_enum(
        self,
        ir: MenaiIRBuildEnum,
        block: _DraftBlock,
        state: _FunctionState,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Build an enum constructor call.

        Emits a MenaiCFGMakeEnumInstr carrying the compile-time MenaiEnumType
        descriptor and the variant index.  Both are compile-time constants, so
        there are no sub-expressions to evaluate.
        """
        result = state.new_value(f"enum_{ir.enum_type.name}")
        block.instrs.append(MenaiCFGMakeEnumInstr(
            result=result,
            enum_type=ir.enum_type,
            variant_index=ir.variant_index,
        ))
        return result, block

    def _build_builtin_call(
        self,
        ir: MenaiIRCall,
        block: _DraftBlock,
        scope: MenaiCFGScope,
        state: _FunctionState,
        tail: bool = False,
    ) -> tuple[MenaiCFGValue, _DraftBlock]:
        """
        Emit a builtin call.  Most builtins are never in tail position (they
        are opcode-backed primitives that return a value inline).

        The 'apply' builtin is special-cased: in non-tail position it emits
        MenaiCFGApplyInstr (lowered to APPLY); in tail position it emits a
        MenaiCFGTailApplyTerm (lowered to TAIL_APPLY), which is essential for
        tail-recursive functions that recurse via apply.
        """
        assert ir.builtin_name is not None

        # Evaluate all argument plans.
        arg_vals: list[MenaiCFGValue] = []
        for arg_plan in ir.arg_plans:
            arg_val, block = self._build_expr(arg_plan, block, scope, state, tail=False)
            arg_vals.append(arg_val)

        if ir.builtin_name == 'apply':
            # apply is always (apply func arg-list) — two args.
            assert len(arg_vals) == 2
            if tail:
                block.terminator = MenaiCFGTailApplyTerm(
                    func=arg_vals[0],
                    arg_list=arg_vals[1],
                )
                placeholder = state.new_value("tail_apply")
                return placeholder, block

            result = state.new_value("apply_result")
            block.instrs.append(MenaiCFGApplyInstr(
                result=result,
                func=arg_vals[0],
                arg_list=arg_vals[1],
            ))
            return result, block

        result = state.new_value(ir.builtin_name)
        block.instrs.append(MenaiCFGBuiltinInstr(
            result=result,
            op=ir.builtin_name,
            args=tuple(arg_vals),
        ))
        return result, block
