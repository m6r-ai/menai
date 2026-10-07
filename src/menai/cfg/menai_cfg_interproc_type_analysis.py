"""
CFG pass: interprocedural flow-based type analysis.

This pass proves types across function boundaries and uses the proven struct
type identities to resolve symbol-based struct field access into constant-index
access.  It is a whole-program pass: it needs the call graph, not a single
function.

Analysis
--------
The pass computes a TypeFact (see menai_cfg_type_fact) for every SSA value in
every function reachable from the root.  Within a function the facts are
derived from constants, builtin result signatures, struct constructors, phi
joins, struct-is-instance? branch refinement, and the values captured by free
variables.  Parameters start from an assumption about their type; a free
variable's fact is the fact of the parent value that fills its capture slot,
so a proven type flows into a nested function through a capture.

Across functions the pass propagates the fact of each call argument into the
corresponding parameter of the callee.  Because a caller's argument facts
depend on the caller's parameter facts, which depend on the callers of the
caller, this is iterated to a fixed point.  The fact lattice is finite and the
join is monotone, so the fixed point is reached.

Call sites within a recursion cycle are handled specially.  A call inside a
cycle has its arguments computed from the very parameters the call would be
used to infer, so its argument facts describe a later iteration, not the first
invocation.  The pass therefore tracks, per parameter, the join over call sites
outside the function's recursion component (the external facts), the join over
call sites inside it (the internal facts), and whether any external call site
exists for the parameter at all.

A parameter with an external call site takes the join of its external and
internal facts.  A parameter whose only external call site passes an unknown
value is unconstrained and takes ANY, so that it degrades any parameter it is
passed to rather than being dropped by the join.  A parameter with no external
call site takes its internal facts when the recursion component is externally
grounded (it contains a function with an external call site, through which every
function in it is reachable); otherwise it stays at BOTTOM and its runtime guards
are retained.  A direct self-recursive tail call is a MenaiCFGSelfLoopTerm; a
call between mutually-recursive functions is an ordinary call.  Both are internal
when caller and callee share a strongly-connected component of the call graph.
Return facts are still propagated through cycles: a function's return value
genuinely is the join over every return path, recursive ones included.

Callee resolution
-----------------
A call's callee is an SSA value, not a function reference.  The pass resolves
it by tracking which function each SSA value denotes: the result of a
MenaiCFGMakeClosureInstr denotes that instruction's function, and a phi whose
incoming values all denote the same function denotes that function.  Every
other value (parameters, free variables, globals) is unresolvable and
contributes nothing.  This is the same limitation the IR inliner has: calls
through function-valued parameters are not resolved.

Rewriting
---------
After the fixed point, for every function the pass rewrites struct field access
where the receiver's struct type identity is proven and the field argument is a
constant symbol:

    (struct-get p 'x)   ->  struct_get_indexed p, <index>
    (struct-with p 'x v) ->  struct_with_indexed p, <index>, v

The index is resolved from the MenaiStructType's field order.  A struct-get
whose receiver's type is not proven, or whose field argument is not a constant
symbol, is left unchanged and continues to use the runtime hash lookup.  The
rewrite is a pure optimisation: it never changes observable behaviour.

The receiver's type is read from the block-local facts, which include the
refinement applied on a struct-is-instance? true edge, merged over the global
per-value facts.  A receiver whose type is proven only by such a refinement has
no definition-site fact, so reading the global facts alone would miss it.

The pass mutates the CFG in place and returns the same root function.
"""

from collections import deque
from dataclasses import replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGApplyInstr,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGuardInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeEnumInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGParamInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGPhiInstr,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructWithIndexedInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGSwitchIntegerTerm,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGTailApplyTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGValue,
    predecessors_by_block,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGWholeProgramPass,
    collect_functions,
)
from menai.cfg.menai_cfg_type_fact import ANY, TypeFact, BOTTOM, fact_for_value, join
from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.menai_value import MenaiBoolean, MenaiStructType, MenaiSymbol

# Builtins whose result is a struct of the same type as their first argument.
_STRUCT_PRESERVING_OPS = {'struct-with'}

# Builtins that read or write a struct field by symbol name.
_FIELD_BY_SYMBOL_OPS = {'struct-get', 'struct-with'}

# Builtins through which a function can enter or leave a container.  Maps the
# builtin name to the argument positions whose contents may reach the result:
# a function value at one of these positions (or inside a container at one of
# these positions) may later be fetched back out of the result and called, so
# it is part of the result's provenance.
#
# Three shapes are covered, and all three matter:
#   - a value stored into a container (list-prepend, dict-with, vector-with, ...);
#   - an element fetched out of a container (list-first, dict-get, vector-nth,
#     ...);
#   - a container built from other containers, where the result's contents are
#     drawn from an operand (list-concat, set-union, list->set, list-slice, ...).
#
# A builtin that only compares against or removes a value (list-member?,
# set-without, ...) still contributes its container argument: a function in the
# input container may be in the result container.
#
# This table must list every builtin that can move a value into or out of a
# container.  A missing entry is not a missed optimisation: it lets a function
# that does escape appear not to, so its parameter facts stay over-precise and
# a field access on them can be rewritten unsoundly.
_CONTAINER_FLOW_OPS = {
    'list-prepend': (1,),
    'list-append': (1,),
    'list-first': (0,),
    'list-last': (0,),
    'list-rest': (0,),
    'list-nth': (0,),
    'list-index': (0,),
    'list-slice': (0,),
    'list-without': (0,),
    'list-concat': (0, 1),
    'list->set': (0,),
    'list->vector': (0,),
    'dict-with': (1, 2),
    'dict-get': (0,),
    'dict-keys': (0,),
    'dict-values': (0,),
    'dict-without': (0,),
    'dict-merge': (0, 1),
    'set-with': (1,),
    'set-without': (0,),
    'set-union': (0, 1),
    'set-intersection': (0, 1),
    'set-difference': (0, 1),
    'set->list': (0,),
    'vector-with': (2,),
    'vector-nth': (0,),
    'vector-slice': (0,),
    'vector-concat': (0, 1),
    'vector->list': (0,),
    'struct-with': (1, 2),
}

# Instruction types that define a result SSA value.  Guard and patch
# instructions are excluded: they have no result.
_VALUE_INSTR_TYPES = (
    MenaiCFGConstInstr,
    MenaiCFGParamInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGApplyInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeEnumInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructWithIndexedInstr,
    MenaiCFGPhiInstr,
)


class _FunctionInfo:
    """
    Per-function analysis state: the possible callees of each call, the
    functions each SSA value may denote, the current parameter facts, and the
    current return fact.

    `callee_of_value` maps an SSA value id to the set of ids of the functions
    that value may denote.  It is a *may* relation, not an exact one: a value
    can denote more than one function when it is a phi of two closures, or when
    it is fetched from a container holding more than one function.  A value
    that denotes no known function is absent from the map.  Functions are
    identified by `id(func)`, because `MenaiCFGFunction` is not hashable.

    Parameter facts are tracked from three sources.  `external_param_facts` is
    the join over call sites outside the function's recursion component;
    `internal_param_facts` is the join over call sites inside it;
    `external_param_present` records, per parameter, whether any call site
    outside the component exists at all.  The presence flag is distinct from a
    non-BOTTOM external fact: a call site whose argument's type is unknown
    leaves the external fact at BOTTOM, which is not the same as having no call
    site there.  `param_facts` is the effective fact derived from all three.

    `parent` and `parent_closure` locate the enclosing function and the
    make_closure instruction that created this one, so a free variable's fact
    can be read from the parent value it captures.  `value_facts` caches the
    most recent per-value facts computed for this function, so that a child's
    free-var facts can be derived from its parent's facts.
    """

    def __init__(
        self,
        func: MenaiCFGFunction,
        parent: '_FunctionInfo | None' = None,
        parent_closure: MenaiCFGMakeClosureInstr | None = None,
    ) -> None:
        self.func = func
        self.parent = parent
        self.parent_closure = parent_closure
        self.callee_of_value: dict[int, set[int]] = {}
        self.external_param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.internal_param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.external_param_present: list[bool] = [False] * func.param_count()
        self.param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.return_fact: TypeFact = BOTTOM
        self.value_facts: dict[int, TypeFact] = {}
        self.callee_ids: set[int] = set()
        self.input_signature: object = None


class MenaiCFGInterprocTypeAnalysis(MenaiCFGWholeProgramPass):
    """
    Whole-program type analysis that resolves struct field access to indices.

    See the module docstring for the algorithm.
    """

    def __init__(self) -> None:
        """Initialise the per-compilation strongly-connected-component map."""
        self._scc_of: dict[int, int] = {}
        self._grounded_sccs: set[int] = set()
        self._context: MenaiCFGContext | None = None

    def _optimize_module(
        self,
        root: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Run the interprocedural analysis and rewrite struct field access."""
        self._context = context
        functions = collect_functions(root)
        parent_of = _parent_map(root)
        info_of: dict[int, _FunctionInfo] = {}
        for func in functions:
            parent_entry = parent_of.get(id(func))
            parent_info = info_of.get(id(parent_entry[0])) if parent_entry is not None else None
            info_of[id(func)] = _FunctionInfo(
                func,
                parent=parent_info,
                parent_closure=parent_entry[1] if parent_entry is not None else None,
            )

        infos = list(info_of.values())

        self._resolve_all_callees(root, functions, info_of)

        for info in infos:
            info.callee_ids = _all_callee_ids(info)

        self._scc_of = _call_graph_sccs(infos)
        self._grounded_sccs = self._externally_grounded_sccs(infos)

        self._propagate_to_fixed_point(infos, info_of)
        self._saturate_unknown_externals(infos, info_of)
        self._saturate_escaped(infos, info_of)

        changed = False
        rewritten: dict[int, MenaiCFGFunction] = {}
        for info in infos:
            facts = self._intra_propagate_memoised(info, info_of)
            original_id = id(info.func)
            new_func, func_changed = self._rewrite_field_access(info, facts)
            if func_changed:
                rewritten[original_id] = new_func
                info.func = new_func
                changed = True

            # Record the facts against the function that will be in the tree
            # after the rewrite, so consumers keyed by function identity find
            # them.
            context.set_facts(info.func, facts)

        if not changed:
            return root, False

        # Rebuild the function tree with the rewritten functions.  The root is
        # the first entry in `functions`; nested functions are reached through
        # the MakeClosure instructions of their parents, so the tree is rebuilt
        # bottom-up by replacing each function's nested closures.
        return _rebuild_function_tree(root, rewritten), True

    def _saturate_unknown_externals(
        self,
        infos: list[_FunctionInfo],
        info_of: dict[int, _FunctionInfo],
    ) -> None:
        """
        Make parameters reached by an unknown external argument unconstrained.

        Once the fixed point has converged an external call site whose argument
        is still BOTTOM passes a value of genuinely unknown type, so the
        parameter it feeds can be anything and must degrade any parameter it is
        in turn passed to.  The external fact is set to ANY and the fixed point
        is re-run, because the parameter's fact may already have grounded other
        parameters.

        This is done after convergence rather than during it because a
        not-yet-computed argument is also BOTTOM while the fixed point is
        running; treating that as unknown would latch ANY onto a parameter whose
        argument later resolves to a concrete type.
        """
        while True:
            saturated = False
            for info in infos:
                for index in range(info.func.param_count()):
                    if info.external_param_present[index] and info.external_param_facts[index].is_bottom():
                        info.external_param_facts[index] = ANY
                        info.param_facts = self._effective_param_facts(info)
                        saturated = True

            if not saturated:
                return

            self._propagate_to_fixed_point(infos, info_of)

    def _saturate_escaped(
        self,
        infos: list[_FunctionInfo],
        info_of: dict[int, _FunctionInfo],
    ) -> None:
        """
        Make the parameters of every escaping function unconstrained.

        A function escapes when a value that may denote it is used as the
        callee of a call the analysis cannot resolve, is passed as an argument
        to such a call, is returned, or is captured by a closure that itself
        escapes.  Such a function may be invoked from a call site the analysis
        cannot see, with arguments of any type, so none of its parameters can
        be assumed to have a type.

        The parameters are forced to ANY and the fixed point is re-run, so the
        unconstrained fact propagates forward through every call the escaped
        function makes.  That forward propagation is what makes the analysis
        transitive: a function called by an escaped function is reached with
        ANY arguments and is degraded in turn, without escape having to be
        propagated backwards through the call graph explicitly.
        """
        escaping = self._escaping_functions(infos)
        if not escaping:
            return

        changed = False
        for info in infos:
            if id(info.func) not in escaping:
                continue

            func_changed = False
            for index in range(info.func.param_count()):
                if not info.external_param_facts[index].is_any():
                    info.external_param_facts[index] = ANY
                    info.external_param_present[index] = True
                    func_changed = True

            if func_changed:
                changed = True
                info.param_facts = self._effective_param_facts(info)

        if changed:
            self._propagate_to_fixed_point(infos, info_of)

    def _escaping_functions(self, infos: list[_FunctionInfo]) -> set[int]:
        """
        Return the ids of the functions that may be called from a call site the
        analysis cannot resolve.

        A value escapes when it occupies a position from which it can be
        invoked by code the analysis cannot see: the callee of an unresolved
        call, an argument to one, or a returned value.  A function escapes when
        a value that may denote it is in an escaping position.

        Escape is transitive through closures: a closure that escapes makes
        everything it captures escape, because the closure's body may pass a
        captured value to anything.  A value installed into a capture slot by
        PATCH_CLOSURE is treated the same way.  The computation is therefore a
        fixed point over the escaping values and the functions they may denote.

        SSA value ids are unique only within a function, so escaping values are
        keyed by (function id, value id) rather than by value id alone.
        """
        escaping_values: set[tuple[int, int]] = set()
        for info in infos:
            for value_id in _escaping_value_ids(info.func, info.callee_of_value):
                escaping_values.add((id(info.func), value_id))

        escaping: set[int] = set()
        changed = True
        while changed:
            changed = False
            for info in infos:
                callee_of_value = info.callee_of_value
                func_id = id(info.func)

                for owner_id, value_id in escaping_values:
                    if owner_id != func_id:
                        continue

                    for callee_id in callee_of_value.get(value_id, set()):
                        if callee_id not in escaping:
                            escaping.add(callee_id)
                            changed = True

                for block in info.func.blocks:
                    for instr in block.instrs:
                        if isinstance(instr, MenaiCFGMakeClosureInstr):
                            if (func_id, instr.result.id) not in escaping_values:
                                continue

                            for capture in instr.captures:
                                if (func_id, capture.id) not in escaping_values:
                                    escaping_values.add((func_id, capture.id))
                                    changed = True

                        elif isinstance(instr, MenaiCFGPatchClosureInstr):
                            # A patched value is reachable from the closure, so
                            # it escapes when the closure does.
                            if (func_id, instr.closure.id) not in escaping_values:
                                continue

                            if (func_id, instr.value.id) not in escaping_values:
                                escaping_values.add((func_id, instr.value.id))
                                changed = True

        return escaping

    def _externally_grounded_sccs(self, infos: list[_FunctionInfo]) -> set[int]:
        """
        Return the ids of the recursion components that have an external entry.

        A component is externally grounded when at least one of its functions
        has a call site from outside the component.  Every function in such a
        component is reachable from that entry through calls within the
        component, so the facts that flow around it are grounded by it.
        """
        grounded: set[int] = set()
        for info in infos:
            for callee_id, _, internal in self._call_sites(info):
                if not internal:
                    grounded.add(self._scc_of[callee_id])

        return grounded

    def _effective_param_facts(self, info: _FunctionInfo) -> list[TypeFact]:
        """
        Derive a function's effective parameter facts from the external and
        internal joins and the presence of external call sites.

        A parameter with an external call site is the join of its external and
        internal facts.  A parameter with no external call site uses its
        internal facts when the recursion component is externally grounded;
        otherwise it stays BOTTOM and its runtime guards are retained.

        An external call site whose argument's type is unknown contributes
        BOTTOM, which the join ignores.  Such a parameter is made unconstrained
        by `_saturate_unknown_externals` once the fixed point has converged,
        not here: while the fixed point is running an argument's fact may be
        BOTTOM only because it has not been computed yet.
        """
        grounded = self._scc_of[id(info.func)] in self._grounded_sccs
        result: list[TypeFact] = []
        for index in range(info.func.param_count()):
            external = info.external_param_facts[index]
            internal = info.internal_param_facts[index]
            if info.external_param_present[index]:
                result.append(join(external, internal))

            elif grounded:
                result.append(internal)

            else:
                result.append(BOTTOM)

        return result

    def _resolve_all_callees(
        self,
        root: MenaiCFGFunction,
        functions: list[MenaiCFGFunction],
        info_of: dict[int, _FunctionInfo],
    ) -> None:
        """
        Resolve the callee of every value in every function, parents first.

        Functions are processed in pre-order (collect_functions returns the
        root first), so a child's creating make_closure instruction has already
        been seen in its parent when the child is processed.  This matters
        because a function that references a letrec sibling does so through a
        free variable, and the free variable's callee is the value captured by
        the make_closure instruction in the parent.
        """
        parent_of = _parent_map(root)
        name_of = {id(func): func.binding_name for func in functions}
        for func in functions:
            parent_entry = parent_of.get(id(func))
            parent_callees = (
                info_of[id(parent_entry[0])].callee_of_value
                if parent_entry is not None
                else {}
            )
            self._resolve_callees(
                info_of[id(func)],
                parent_entry[1] if parent_entry is not None else None,
                parent_callees,
                name_of,
            )

    def _resolve_callees(
        self,
        info: _FunctionInfo,
        parent_closure: MenaiCFGMakeClosureInstr | None,
        parent_callees: dict[int, set[int]],
        name_of: dict[int, str | None],
    ) -> None:
        """
        Determine which functions each SSA value may denote.

        Sources followed:
          - a make_closure result denotes its function;
          - a phi denotes the union of the functions its incoming values denote;
          - a free variable denotes whatever function the corresponding
            capture denotes in the parent function.  This is how a function
            reaches a letrec sibling: the sibling is captured, not created
            locally;
          - a container value denotes every function its elements denote, and a
            value fetched from a container denotes every function the container
            denotes.  This is what connects a function stored in a list or dict
            to a call made through the value fetched back out of it.

        The relation is a fixed point: container elements flow into the
        container, out of it again, and through phis, so the map is iterated
        until no value's set grows.
        """
        func = info.func
        callee_of_value = info.callee_of_value

        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    callee_of_value.setdefault(instr.result.id, set()).add(id(instr.function))

        if parent_closure is not None:
            for block in func.blocks:
                for instr in block.instrs:
                    if isinstance(instr, MenaiCFGFreeVarInstr):
                        resolved = _free_var_callee(instr, parent_closure, parent_callees, name_of)
                        if resolved:
                            callee_of_value.setdefault(instr.result.id, set()).update(resolved)

        changed = True
        while changed:
            changed = False
            for block in func.blocks:
                for instr in block.instrs:
                    if not isinstance(instr, _VALUE_INSTR_TYPES):
                        continue

                    resolved = self._instr_callees(instr, callee_of_value)
                    if resolved - callee_of_value.get(instr.result.id, set()):
                        callee_of_value.setdefault(instr.result.id, set()).update(resolved)
                        changed = True

    def _instr_callees(
        self,
        instr: object,
        callee_of_value: dict[int, set[int]],
    ) -> set[int]:
        """Resolve the functions an instruction's result may denote."""
        if isinstance(instr, MenaiCFGPhiInstr):
            result: set[int] = set()
            for incoming_val, _ in instr.incoming:
                result |= callee_of_value.get(incoming_val.id, set())

            return result

        if isinstance(instr, MenaiCFGMakeListInstr):
            return _union_of_args(instr.args, callee_of_value)

        if isinstance(instr, MenaiCFGMakeVectorInstr):
            return _union_of_args(instr.args, callee_of_value)

        if isinstance(instr, MenaiCFGMakeSetInstr):
            return _union_of_args(instr.args, callee_of_value)

        if isinstance(instr, MenaiCFGMakeStructInstr):
            return _union_of_args(instr.args, callee_of_value)

        if isinstance(instr, MenaiCFGMakeDictInstr):
            result = set()
            for key, val in instr.pairs:
                result |= callee_of_value.get(key.id, set())
                result |= callee_of_value.get(val.id, set())

            return result

        if isinstance(instr, MenaiCFGBuiltinInstr):
            flow_indices = _CONTAINER_FLOW_OPS.get(instr.op)
            if flow_indices is not None:
                result = set()
                for index in flow_indices:
                    if index < len(instr.args):
                        result |= callee_of_value.get(instr.args[index].id, set())

                return result

        return set()

    def _propagate_to_fixed_point(
        self,
        infos: list[_FunctionInfo],
        info_of: dict[int, _FunctionInfo],
    ) -> None:
        """
        Iterate the interprocedural parameter-fact and return-fact
        propagation until neither changes.

        Return facts matter because a struct type often enters a call chain
        through a function's return value rather than a constructor at the
        call site: a recursive search passes its cube parameter through
        functions that each return a cube.  Without return facts the struct
        type never reaches the parameters that read its fields.

        Each function's per-value facts are cached on its _FunctionInfo after
        it is processed, so a child's free-var facts can be derived from its
        parent's facts.  The initial worklist is in parents-first order, so a
        parent's cache is normally up to date when its child is first processed.

        The propagation is a worklist rather than a round-robin sweep: a
        function is re-processed only when something it depends on changed.
        The dependencies are a caller's argument facts flowing into a callee's
        parameters, a callee's return fact flowing back to its callers, and a
        parent's per-value facts flowing into its children's free variables.
        """
        callers_of, children_of = self._dependency_edges(infos)

        worklist = deque(id(info.func) for info in infos)
        queued = set(worklist)
        while worklist:
            func_id = worklist.popleft()
            queued.discard(func_id)
            info = info_of[func_id]

            previous_facts = info.value_facts
            facts = self._intra_propagate_memoised(info, info_of)
            info.value_facts = facts

            for callee_id in self._propagate_call_args(info, facts, info_of):
                if callee_id not in queued:
                    worklist.append(callee_id)
                    queued.add(callee_id)

            if self._propagate_return_fact(info, facts, info_of):
                for caller_id in callers_of[func_id]:
                    if caller_id not in queued:
                        worklist.append(caller_id)
                        queued.add(caller_id)

            if facts != previous_facts:
                for child_id in children_of[func_id]:
                    if child_id not in queued:
                        worklist.append(child_id)
                        queued.add(child_id)

    def _dependency_edges(
        self,
        infos: list[_FunctionInfo],
    ) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
        """
        Build the reverse edges that drive the worklist fixed point.

        Two cross-function dependencies are not captured by pushing into a
        callee when a caller is processed:

          - a caller reads its callees' return facts, so a change to a callee's
            return fact must re-process the caller;
          - a child reads its parent's per-value facts for its free variables,
            so a change to a parent's facts must re-process the child.

        Returns (callers_of, children_of), keyed by id(func).  `callers_of`
        maps a function to the functions with a call site to it; `children_of`
        maps a function to the functions nested within it.
        """
        callers_of: dict[int, set[int]] = {id(info.func): set() for info in infos}
        children_of: dict[int, set[int]] = {id(info.func): set() for info in infos}
        for info in infos:
            for callee_id, _, _ in self._call_sites(info):
                callers_of[callee_id].add(id(info.func))

            if info.parent is not None:
                children_of[id(info.parent.func)].add(id(info.func))

        return callers_of, children_of

    def _propagate_return_fact(
        self,
        info: _FunctionInfo,
        facts: dict[int, TypeFact],
        info_of: dict[int, _FunctionInfo],
    ) -> bool:
        """
        Join the facts of all of a function's return points into its return
        fact.

        A return terminator returns a value; a tail call returns the callee's
        return fact; a self-loop returns the function's own return fact.  All
        are joined together (and with the existing return fact, so the fact
        only ever moves up the lattice).

        Returns True if the return fact changed.
        """
        result = info.return_fact
        for block in info.func.blocks:
            term = block.terminator
            if term is None:
                continue

            if isinstance(term, MenaiCFGReturnTerm):
                result = join(result, facts.get(term.value.id, BOTTOM))

            elif isinstance(term, MenaiCFGTailCallTerm):
                callees = info.callee_of_value.get(term.func.id, set())
                if not callees:
                    # An unresolved tail-call callee may return a value of any
                    # type, so it contributes ANY.  Contributing BOTTOM would
                    # drop this return path from the join and let a known return
                    # type from another path survive as if it were the only one.
                    result = join(result, ANY)

                else:
                    for callee_id in callees:
                        result = join(result, info_of[callee_id].return_fact)

            elif isinstance(term, MenaiCFGSelfLoopTerm):
                result = join(result, info.return_fact)

        if result != info.return_fact:
            info.return_fact = result
            return True

        return False

    def _propagate_call_args(
        self,
        info: _FunctionInfo,
        facts: dict[int, TypeFact],
        info_of: dict[int, _FunctionInfo],
    ) -> set[int]:
        """
        Join the fact of each call argument into the callee's parameter facts.

        External call sites (outside the callee's recursion component) update
        the callee's external facts; intra-component call sites update its
        internal facts.  The callee's effective parameter facts are then
        recomputed from the two and the presence of external call sites.

        Returns the ids of the callees whose parameter facts changed.
        """
        changed: set[int] = set()
        for callee_id, args, internal in self._call_sites(info):
            callee = info_of[callee_id]
            if self._join_arg_facts(callee, args, facts, internal):
                callee.param_facts = self._effective_param_facts(callee)
                changed.add(callee_id)

        return changed

    @staticmethod
    def _join_arg_facts(
        callee: _FunctionInfo,
        args: tuple[MenaiCFGValue, ...],
        facts: dict[int, TypeFact],
        internal: bool,
    ) -> bool:
        """
        Join a call's argument facts into the callee's external or internal
        parameter facts.

        An external call site also records that the parameter has one, so that
        a parameter reached by an unknown external argument is distinguished
        from one with no external call site at all.

        For a variadic callee the fixed parameters receive the corresponding
        argument facts and the rest parameter receives a list fact, since the
        VM packs the remaining arguments into a list.
        """
        changed = False
        target = callee.internal_param_facts if internal else callee.external_param_facts
        param_count = len(target)
        if param_count == 0:
            return False

        fixed = param_count - 1 if callee.func.is_variadic else param_count
        for i in range(min(fixed, len(args))):
            if not internal:
                if not callee.external_param_present[i]:
                    callee.external_param_present[i] = True
                    changed = True

            if _join_param(target, i, facts.get(args[i].id, BOTTOM)):
                changed = True

        if callee.func.is_variadic:
            if _join_param(target, param_count - 1, TypeFact(kind='list')):
                changed = True

        return changed

    def _call_sites(
        self,
        info: _FunctionInfo,
    ) -> list[tuple[int, tuple[MenaiCFGValue, ...], bool]]:
        """
        Enumerate the call sites that contribute to a callee's parameter facts,
        as (callee_id, argument_values, internal) triples.

        A call whose callee may denote more than one function contributes one
        call site per possible callee: the call really can reach any of them,
        so each must receive the argument facts.

        A direct self-recursive tail call (a MenaiCFGSelfLoopTerm with no
        param_vals) targets the enclosing function itself.  A MenaiIRLoop
        back-edge (a SelfLoopTerm with param_vals set) is *not* a call: its
        args feed the loop's own phi nodes, not the enclosing function's
        parameters, so it contributes no call site.

        `internal` is True for a call site within the caller's own
        strongly-connected component of the call graph, i.e. inside a recursion
        cycle.  Such a call's arguments are computed from the very parameters
        the call would be used to infer, so its argument facts describe a later
        iteration, not the first invocation.  The caller routes them to the
        callee's internal facts, which ground a parameter only when the
        recursion component is externally grounded.
        Return-fact propagation is unaffected: a function's return value
        genuinely is the join over every path, recursive ones included.
        """
        result: list[tuple[int, tuple[MenaiCFGValue, ...], bool]] = []
        caller_scc = self._scc_of[id(info.func)]
        for block in info.func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGCallInstr):
                    for callee_id in info.callee_of_value.get(instr.func.id, set()):
                        result.append((callee_id, instr.args, self._scc_of[callee_id] == caller_scc))

            term = block.terminator
            if isinstance(term, MenaiCFGTailCallTerm):
                for callee_id in info.callee_of_value.get(term.func.id, set()):
                    result.append((callee_id, term.args, self._scc_of[callee_id] == caller_scc))

            elif isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is None:
                result.append((id(info.func), term.args, True))

        return result

    def _intra_propagate_memoised(
        self,
        info: _FunctionInfo,
        info_of: dict[int, _FunctionInfo],
    ) -> dict[int, TypeFact]:
        """
        Return the function's per-value facts, recomputing only if its inputs
        changed since the last computation.

        A function's facts are a pure function of three inputs: its parameter
        facts, the return facts of the functions it may call, and its parent's
        per-value facts (read for free variables).  The worklist re-processes a
        function whenever a dependency changed, but a dependency change does not
        always change this function's facts — a caller re-enqueued because a
        callee's return fact moved may still join to the same result.  Skipping
        the recomputation when the inputs are unchanged therefore avoids
        re-deriving facts that cannot have moved.
        """
        signature = self._input_signature(info, info_of)
        if signature == info.input_signature:
            return info.value_facts

        facts = self._intra_propagate(info, info.param_facts, info_of)
        info.input_signature = signature
        return facts

    @staticmethod
    def _input_signature(
        info: _FunctionInfo,
        info_of: dict[int, _FunctionInfo],
    ) -> object:
        """
        Return a hashable signature of the inputs that determine a function's
        per-value facts.

        The parent's facts are captured by the identity of its facts dict: the
        parent is re-processed (and so its dict replaced) only when it is
        enqueued, and a child is enqueued only when the parent's facts actually
        changed, so identity is a sufficient proxy for the parent's contents.
        """
        parent_facts = id(info.parent.value_facts) if info.parent is not None else None
        callee_returns = frozenset(
            (callee_id,) + _fact_key(info_of[callee_id].return_fact)
            for callee_id in info.callee_ids
        )
        params = tuple(_fact_key(fact) for fact in info.param_facts)
        return (params, callee_returns, parent_facts)

    def _intra_propagate(
        self,
        info: _FunctionInfo,
        param_facts: list[TypeFact],
        info_of: dict[int, _FunctionInfo],
    ) -> dict[int, TypeFact]:
        """
        Compute the type fact of every SSA value in a function, given the
        assumed parameter facts.

        Iterates to a fixed point so that phi nodes with back-edge inputs
        converge.  Struct-is-instance? branch refinement is applied by
        treating the true-edge successor's incoming facts as refined.
        """
        func = info.func
        facts: dict[int, TypeFact] = {}
        value_defs = _value_defs(func)
        preds_by_block = predecessors_by_block(func)

        while True:
            changed = False
            for block in func.blocks:
                # Merge the global per-value facts with this block's incoming
                # facts, block-local winning.  A phi's incoming value may be
                # defined in a block that dominates a predecessor rather than
                # in the predecessor itself, so it is absent from the
                # predecessor's outgoing facts; without the global facts the
                # phi would join it as BOTTOM and lose a proven type.  A value
                # refined by a struct-is-instance? branch exists only in the
                # block-local facts, which therefore take precedence.
                block_facts = dict(facts)
                block_facts.update(self._block_incoming(
                    block, preds_by_block, facts, param_facts, value_defs, info,
                ))
                for instr in block.instrs:
                    if not isinstance(instr, _VALUE_INSTR_TYPES):
                        continue

                    new_fact = self._instr_fact(instr, block_facts, param_facts, info, info_of)
                    block_facts[instr.result.id] = new_fact
                    if facts.get(instr.result.id) != new_fact:
                        facts[instr.result.id] = new_fact
                        changed = True

            if not changed:
                break

        return facts

    def _block_incoming(
        self,
        block: MenaiCFGBlock,
        preds_by_block: dict[int, list[MenaiCFGBlock]],
        facts: dict[int, TypeFact],
        param_facts: list[TypeFact],
        value_defs: dict[int, object],
        info: _FunctionInfo,
    ) -> dict[int, TypeFact]:
        """
        Compute the incoming facts for a block: parameter facts for the entry
        block, and the join of predecessor outgoing facts otherwise.

        A value defined in any predecessor is included with its fact from
        that predecessor, joined with its fact from any other predecessor that
        also defines it.  A value defined in only one predecessor is therefore
        still visible, which a phi node needs to join its incoming values
        correctly; dropping it would make the phi's fact too precise.

        A block reached from a struct-is-instance? true edge inherits the
        refined struct type of the predicate's argument.
        """
        preds = preds_by_block[block.id]
        if not preds:
            return self._entry_facts(block, param_facts)

        result: dict[int, TypeFact] = {}
        for pred in preds:
            pred_facts = self._outgoing_facts(pred, facts)
            refinement = self._true_edge_refinement(pred, block, value_defs, info)
            if refinement is not None:
                val_id, refined = refinement
                pred_facts = dict(pred_facts)
                pred_facts[val_id] = refined

            for val_id, fact in pred_facts.items():
                if val_id in result:
                    result[val_id] = join(result[val_id], fact)

                else:
                    result[val_id] = fact

        return result

    @staticmethod
    def _entry_facts(
        block: MenaiCFGBlock,
        param_facts: list[TypeFact],
    ) -> dict[int, TypeFact]:
        """Map each parameter instruction in the entry block to its assumed fact."""
        result: dict[int, TypeFact] = {}
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGParamInstr) and instr.index < len(param_facts):
                result[instr.result.id] = param_facts[instr.index]

        return result

    @staticmethod
    def _outgoing_facts(
        block: MenaiCFGBlock,
        facts: dict[int, TypeFact],
    ) -> dict[int, TypeFact]:
        """Return the facts established by a block's instructions."""
        result: dict[int, TypeFact] = {}
        for instr in block.instrs:
            if not isinstance(instr, _VALUE_INSTR_TYPES):
                continue

            fact = facts.get(instr.result.id)
            if fact is not None:
                result[instr.result.id] = fact

        return result

    def _true_edge_refinement(
        self,
        pred: MenaiCFGBlock,
        succ: MenaiCFGBlock,
        value_defs: dict[int, object],
        info: _FunctionInfo,
    ) -> tuple[int, TypeFact] | None:
        """
        If pred branches to succ on the true edge and the condition is
        (struct-is-instance? v TypeName), return (v.id, Known('struct', type)).

        The struct type is resolved from the structtype argument, which may be a
        constant (a struct declared in this function) or a free variable (a
        struct declared in an enclosing function or imported).

        The condition may also be a phi that joins a struct-is-instance? result
        with constant #f values, which is the shape an (and ...) guard lowers to.
        On the true edge only the struct-is-instance? branch can have been taken,
        so the refinement holds there too.
        """
        term = pred.terminator
        if not isinstance(term, MenaiCFGBranchTerm) or term.true_block != succ.id:
            return None

        cond_instr = value_defs.get(term.cond.id)
        if isinstance(cond_instr, MenaiCFGPhiInstr):
            return self._phi_true_edge_refinement(cond_instr, value_defs, info)

        return self._instance_test_refinement(cond_instr, value_defs, info)

    def _instance_test_refinement(
        self,
        instr: object,
        value_defs: dict[int, object],
        info: _FunctionInfo,
    ) -> tuple[int, TypeFact] | None:
        """
        If instr is (struct-is-instance? v TypeName) whose structtype argument
        resolves to a struct type, return (v.id, Known('struct', type)), else
        None.
        """
        if (
            isinstance(instr, MenaiCFGBuiltinInstr)
            and instr.op == 'struct-is-instance?'
            and len(instr.args) == 2
        ):
            struct_type = self._struct_type_of_value(instr.args[1].id, value_defs, info)
            if struct_type is not None:
                if self._context is not None:
                    self._context.record_struct_type_of_test(
                        info.func, instr.args[1].id, struct_type,
                    )

                return instr.args[0].id, TypeFact(kind='struct', struct_type=struct_type)

        return None

    def _phi_true_edge_refinement(
        self,
        phi: MenaiCFGPhiInstr,
        value_defs: dict[int, object],
        info: _FunctionInfo,
    ) -> tuple[int, TypeFact] | None:
        """
        Refine through a phi that joins a struct-is-instance? result with #f.

        This is the shape an (and (struct? v) (struct-is-instance? v T)) guard
        lowers to: the struct? test guards the struct-is-instance? test, and the
        two are joined by a phi.  The true edge can only have been reached
        through the struct-is-instance? branch, because the other incoming values
        are constant #f, so the refinement is sound.

        Requires exactly one incoming value to be a struct-is-instance? result
        and every other incoming value to be a constant false.
        """
        refinement: tuple[int, TypeFact] | None = None
        for incoming_val, _ in phi.incoming:
            candidate = self._instance_test_refinement(
                value_defs.get(incoming_val.id), value_defs, info,
            )
            if candidate is not None:
                if refinement is not None:
                    return None

                refinement = candidate
                continue

            if not self._is_constant_false(value_defs.get(incoming_val.id)):
                return None

        return refinement

    @staticmethod
    def _is_constant_false(instr: object) -> bool:
        """Return True if instr defines the constant #f."""
        return (
            isinstance(instr, MenaiCFGConstInstr)
            and isinstance(instr.value, MenaiBoolean)
            and not instr.value.value
        )

    def _instr_fact(
        self,
        instr: object,
        facts: dict[int, TypeFact],
        param_facts: list[TypeFact],
        info: _FunctionInfo,
        info_of: dict[int, _FunctionInfo],
    ) -> TypeFact:
        """Determine the result fact of a single instruction."""
        if isinstance(instr, MenaiCFGConstInstr):
            return fact_for_value(instr.value)

        if isinstance(instr, MenaiCFGMakeStructInstr):
            return TypeFact(kind='struct', struct_type=instr.struct_type)

        if isinstance(instr, MenaiCFGMakeEnumInstr):
            return TypeFact(kind='enum', enum_type=instr.enum_type)

        if isinstance(instr, MenaiCFGStructGetIndexedInstr):
            return ANY

        if isinstance(instr, MenaiCFGStructWithIndexedInstr):
            receiver = facts.get(instr.struct.id, BOTTOM)
            if receiver.kind == 'struct':
                return receiver

            return ANY

        if isinstance(instr, MenaiCFGMakeListInstr):
            return TypeFact(kind='list')

        if isinstance(instr, MenaiCFGMakeVectorInstr):
            return TypeFact(kind='vector')

        if isinstance(instr, MenaiCFGMakeSetInstr):
            return TypeFact(kind='set')

        if isinstance(instr, MenaiCFGMakeDictInstr):
            return TypeFact(kind='dict')

        if isinstance(instr, MenaiCFGPhiInstr):
            return self._phi_fact(instr, facts)

        if isinstance(instr, MenaiCFGParamInstr):
            if instr.index < len(param_facts):
                return param_facts[instr.index]

            return BOTTOM

        if isinstance(instr, MenaiCFGFreeVarInstr):
            return self._free_var_fact(instr, info)

        if isinstance(instr, MenaiCFGCallInstr):
            callees = info.callee_of_value.get(instr.func.id, set())
            if not callees:
                # The callee could not be resolved (e.g. a function-valued
                # parameter), so the call may reach any function and return a
                # value of any type.  This is ANY, not BOTTOM: BOTTOM is the
                # join identity, so returning it would let a known return type
                # from another path survive the join and be reported as proven.
                return ANY

            result = BOTTOM
            for callee_id in callees:
                result = join(result, info_of[callee_id].return_fact)

            return result

        if isinstance(instr, MenaiCFGBuiltinInstr):
            return self._builtin_fact(instr, facts)

        return BOTTOM

    def _free_var_fact(self, instr: MenaiCFGFreeVarInstr, info: _FunctionInfo) -> TypeFact:
        """
        Derive a free variable's fact from the parent value it captures.

        A free variable is a value captured from the enclosing function.  Its
        type is exactly the type of the parent value that fills its capture
        slot, so the fact is read from the parent's cached per-value facts.

        A free variable's index is a position in the child function's free_vars
        list, which is ordered sibling free vars first, then outer free vars.
        The make_closure instruction's captures hold only the outer captures,
        so captures[i] corresponds to free_vars[len(free_vars) - len(captures)
        + i].  A sibling free var is not in the captures list; it is installed
        by the PATCH_CLOSURE whose capture_index equals the free var's index.

        Returns BOTTOM when the parent is unknown, the capture cannot be
        located, or the parent's fact for the captured value is not yet known.
        """
        parent = info.parent
        parent_closure = info.parent_closure
        if parent is None or parent_closure is None:
            return BOTTOM

        captured = self._captured_parent_value(instr, parent_closure, parent)
        if captured is None:
            return BOTTOM

        return parent.value_facts.get(captured.id, BOTTOM)

    @staticmethod
    def _captured_parent_value(
        instr: MenaiCFGFreeVarInstr,
        parent_closure: MenaiCFGMakeClosureInstr,
        parent: _FunctionInfo,
    ) -> MenaiCFGValue | None:
        """
        Return the parent SSA value that fills a free variable's capture slot.

        Outer free vars are read from the make_closure's captures list.  A
        sibling free var is read from the PATCH_CLOSURE that installs it, which
        is identified by the closure value and the free var's index.
        """
        free_vars = parent_closure.function.free_vars
        captures = parent_closure.captures
        outer_start = len(free_vars) - len(captures)

        if instr.index >= outer_start:
            return captures[instr.index - outer_start]

        for block in parent.func.blocks:
            for patch in block.patch_instrs:
                if patch.closure.id == parent_closure.result.id and patch.capture_index == instr.index:
                    return patch.value

            for patch_instr in block.instrs:
                if (
                    isinstance(patch_instr, MenaiCFGPatchClosureInstr)
                    and patch_instr.closure.id == parent_closure.result.id
                    and patch_instr.capture_index == instr.index
                ):
                    return patch_instr.value

        return None

    def _struct_type_of_value(
        self,
        value_id: int,
        value_defs: dict[int, object],
        info: _FunctionInfo,
    ) -> MenaiStructType | None:
        """
        Resolve an SSA value to the MenaiStructType it names, where it can be
        resolved.

        The structtype argument of a struct-is-instance? test is a name
        reference, so it is either a constant (a struct declared in the same
        function) or a free variable (a struct declared in an enclosing
        function or imported).  A free variable is resolved through the parent
        value it captures, mirroring `_free_var_fact`; a phi whose incoming
        values all name the same struct type is resolved to that type.

        Returns None when the value does not name a single struct type.
        """
        instr = value_defs.get(value_id)
        if isinstance(instr, MenaiCFGConstInstr) and isinstance(instr.value, MenaiStructType):
            return instr.value

        if isinstance(instr, MenaiCFGFreeVarInstr):
            parent = info.parent
            parent_closure = info.parent_closure
            if parent is None or parent_closure is None:
                return None

            captured = self._captured_parent_value(instr, parent_closure, parent)
            if captured is None:
                return None

            return self._struct_type_of_value(captured.id, _value_defs(parent.func), parent)

        if isinstance(instr, MenaiCFGPhiInstr):
            result: MenaiStructType | None = None
            for incoming_val, _ in instr.incoming:
                resolved = self._struct_type_of_value(incoming_val.id, value_defs, info)
                if resolved is None:
                    return None

                if result is None:
                    result = resolved

                elif result is not resolved:
                    return None

            return result

        return None

    @staticmethod
    def _phi_fact(instr: MenaiCFGPhiInstr, facts: dict[int, TypeFact]) -> TypeFact:
        """Join the facts of a phi node's incoming values."""
        result = BOTTOM
        for incoming_val, _ in instr.incoming:
            result = join(result, facts.get(incoming_val.id, BOTTOM))

        return result

    @staticmethod
    def _builtin_fact(instr: MenaiCFGBuiltinInstr, facts: dict[int, TypeFact]) -> TypeFact:
        """
        Determine a builtin's result fact from its type signature.

        For struct-preserving operations the result carries the receiver's
        struct type, which the signature alone cannot express.
        """
        if instr.op in _STRUCT_PRESERVING_OPS and instr.args:
            receiver = facts.get(instr.args[0].id, BOTTOM)
            if receiver.kind == 'struct':
                return receiver

        sig = BUILTIN_TYPE_SIGNATURES.get(instr.op)
        if sig is not None and sig[1] is not None:
            return TypeFact(kind=sig[1])

        # The result type is genuinely unknown (e.g. dict-get,
        # list-first return a value whose type depends on the input), so the
        # result could be anything: ANY, not BOTTOM.
        return ANY

    def _rewrite_field_access(
        self,
        info: _FunctionInfo,
        facts: dict[int, TypeFact],
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Rewrite struct-get/struct-with calls to their index-based forms where the
        receiver's struct type and the field index are both known.

        The receiver's type is read from the block-local facts, not the global
        per-value facts: a receiver whose type is proven only by a
        struct-is-instance? branch refinement has no definition-site fact, so
        the refinement must be visible here for the rewrite to fire.

        Returns the (possibly new) function and whether any instruction was
        rewritten.
        """
        func = info.func
        changed = False
        value_defs = _value_defs(func)
        preds_by_block = predecessors_by_block(func)
        orphaned_symbols: set[int] = set()
        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            # Merge the global per-value facts with this block's incoming facts,
            # block-local winning: a value defined in this block has only a
            # global fact, while a value refined by a struct-is-instance? branch
            # has a refined fact that exists only in the block-local facts.
            block_facts = dict(facts)
            block_facts.update(self._block_incoming(
                block, preds_by_block, facts, info.param_facts, value_defs, info,
            ))
            new_instrs: list = []
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGBuiltinInstr):
                    new_instrs.append(instr)
                    continue

                if instr.op not in _FIELD_BY_SYMBOL_OPS:
                    new_instrs.append(instr)
                    continue

                index = self._resolve_field_index(instr, block_facts, value_defs)
                if index is None:
                    new_instrs.append(instr)
                    continue

                # The symbol argument is replaced by the resolved index, so the
                # constant instruction that defined it may become dead.  Record
                # it; it is removed below if no other instruction still uses it.
                orphaned_symbols.add(instr.args[1].id)

                if instr.op == 'struct-get':
                    new_instrs.append(MenaiCFGStructGetIndexedInstr(
                        result=instr.result,
                        struct=instr.args[0],
                        index=index,
                    ))

                else:
                    new_instrs.append(MenaiCFGStructWithIndexedInstr(
                        result=instr.result,
                        struct=instr.args[0],
                        index=index,
                        value=instr.args[2],
                    ))

                changed = True

            new_blocks.append(replace(block, instrs=tuple(new_instrs)))

        if not changed:
            return func, False

        func = replace(func, blocks=tuple(new_blocks))
        if orphaned_symbols:
            func = self._remove_dead_consts(func, orphaned_symbols)

        return func, True

    @staticmethod
    def _remove_dead_consts(
        func: MenaiCFGFunction,
        candidates: set[int],
    ) -> MenaiCFGFunction:
        """
        Remove constant instructions defining an orphaned value that has no
        remaining uses anywhere in the function.

        A candidate is only removed when nothing else references its value id,
        so a constant that is still live (or shared) is left in place.  This is
        a pure optimisation: the removed instructions are constant loads whose
        result is never observed.
        """
        referenced = _referenced_value_ids(func)
        dead = candidates - referenced
        if not dead:
            return func

        return replace(
            func,
            blocks=tuple(
                replace(
                    block,
                    instrs=tuple(
                        instr for instr in block.instrs
                        if not (
                            isinstance(instr, MenaiCFGConstInstr)
                            and instr.result.id in dead
                        )
                    ),
                )
                for block in func.blocks
            ),
        )

    @staticmethod
    def _resolve_field_index(
        instr: MenaiCFGBuiltinInstr,
        facts: dict[int, TypeFact],
        value_defs: dict[int, object],
    ) -> int | None:
        """
        Return the constant field index for a struct-get/struct-with call, or
        None if the receiver's struct type or the field name is not known.
        """
        if len(instr.args) < 2:
            return None

        receiver_fact = facts.get(instr.args[0].id, BOTTOM)
        if receiver_fact.kind != 'struct' or receiver_fact.struct_type is None:
            return None

        field_instr = value_defs.get(instr.args[1].id)
        if not isinstance(field_instr, MenaiCFGConstInstr) or not isinstance(field_instr.value, MenaiSymbol):
            return None

        try:
            return receiver_fact.struct_type.field_index(field_instr.value.name)

        except KeyError:
            return None


def _parent_map(
    root: MenaiCFGFunction,
) -> dict[int, tuple[MenaiCFGFunction, MenaiCFGMakeClosureInstr]]:
    """
    Map each function id to the function and make_closure instruction that
    create it.  The root has no entry.
    """
    result: dict[int, tuple[MenaiCFGFunction, MenaiCFGMakeClosureInstr]] = {}
    for func in collect_functions(root):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    result[id(instr.function)] = (func, instr)

    return result


def _rebuild_function_tree(
    root: MenaiCFGFunction,
    rewritten: dict[int, MenaiCFGFunction],
) -> MenaiCFGFunction:
    """
    Rebuild a function tree, substituting rewritten functions.

    `rewritten` maps the id of an original function to its rewritten
    replacement.  Each function's blocks are rebuilt with any MakeClosure
    instruction's nested function replaced by its rewritten form, and the
    function itself is replaced by its rewritten form when present.  The
    rewrite only changes block contents, never the tree's shape, so the
    substitution is a straight walk of the original tree.
    """
    def rebuild(func: MenaiCFGFunction) -> MenaiCFGFunction:
        original_id = id(func)
        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            new_instrs = []
            block_changed = False
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    new_child = rebuild(instr.function)
                    if new_child is not instr.function:
                        instr = MenaiCFGMakeClosureInstr(
                            result=instr.result,
                            function=new_child,
                            captures=instr.captures,
                            needs_patching=instr.needs_patching,
                        )
                        block_changed = True

                new_instrs.append(instr)

            if block_changed:
                block = replace(block, instrs=tuple(new_instrs))

            new_blocks.append(block)

        func = replace(func, blocks=tuple(new_blocks))
        return rewritten.get(original_id, func)

    return rebuild(root)


def _free_var_callee(
    instr: MenaiCFGFreeVarInstr,
    parent_closure: MenaiCFGMakeClosureInstr,
    parent_callees: dict[int, set[int]],
    name_of: dict[int, str | None],
) -> set[int]:
    """
    Resolve a free variable to the functions its capture may denote.

    A free variable's index is a position in the child function's free_vars
    list.  The list is ordered sibling free vars first, then outer free vars.
    The make_closure instruction's captures are the outer captures, and the
    codegen places them in the tail of the slot range, so captures[i]
    corresponds to free_vars[len(free_vars) - len(captures) + i].

    Sibling free vars are not in the captures list; they are installed by
    PATCH_CLOSURE after all sibling closures exist.  A sibling free var is
    resolved by finding the parent value that may denote a function of the same
    name.  `name_of` maps a function id to its binding name for that lookup.
    """
    free_vars = parent_closure.function.free_vars
    captures = parent_closure.captures
    outer_start = len(free_vars) - len(captures)

    if instr.index >= outer_start:
        captured = captures[instr.index - outer_start]
        return set(parent_callees.get(captured.id, set()))

    result: set[int] = set()
    for callees in parent_callees.values():
        for func_id in callees:
            if name_of.get(func_id) == instr.var_name:
                result.add(func_id)

    return result


def _union_of_args(
    args: tuple[MenaiCFGValue, ...],
    callee_of_value: dict[int, set[int]],
) -> set[int]:
    """Return the union of the functions a sequence of values may denote."""
    result: set[int] = set()
    for arg in args:
        result |= callee_of_value.get(arg.id, set())

    return result


def _escaping_value_ids(
    func: MenaiCFGFunction,
    callee_of_value: dict[int, set[int]],
) -> set[int]:
    """
    Return the ids of the values in `func` that occupy an escaping position.

    A value escapes when it can be invoked by code the analysis cannot see.
    That is the case when it is:

      - the callee of a call or apply whose callee is not resolved: the value is
        invoked there, and the analysis cannot see the call site;
      - an argument to a call or apply whose callee is not resolved, because the
        unresolved callee may invoke it;
      - a returned value, because the caller may invoke it.  This includes the
        module body's result, which the host receives and may invoke.

    A resolved call is not an escape: the analysis sees that call site, so the
    callee is a known function whose parameters are propagated normally, and the
    arguments reach known parameters.  If a resolved callee itself escapes for
    some other reason, its parameters are degraded and the degradation reaches
    the arguments through the ordinary fixed point.

    A value installed into a closure's capture slot does not escape by itself:
    it becomes reachable from the closure, and escapes only if the closure does.
    That is handled by the transitive closure rule in `_escaping_functions`.

    `callee_of_value` is the function's resolved-callee map, used to tell a
    resolved call site from an unresolved one.
    """
    escaping: set[int] = set()

    def is_resolved(value_id: int) -> bool:
        """True if the value denotes at least one known function."""
        return bool(callee_of_value.get(value_id))

    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGCallInstr):
                if not is_resolved(instr.func.id):
                    escaping.add(instr.func.id)
                    for arg in instr.args:
                        escaping.add(arg.id)

            elif isinstance(instr, MenaiCFGApplyInstr):
                escaping.add(instr.func.id)
                escaping.add(instr.arg_list.id)

        term = block.terminator
        if isinstance(term, MenaiCFGReturnTerm):
            escaping.add(term.value.id)

        elif isinstance(term, MenaiCFGTailCallTerm):
            if not is_resolved(term.func.id):
                escaping.add(term.func.id)
                for arg in term.args:
                    escaping.add(arg.id)

        elif isinstance(term, MenaiCFGTailApplyTerm):
            escaping.add(term.func.id)
            escaping.add(term.arg_list.id)

    return escaping


def _join_param(target: list[TypeFact], index: int, fact: TypeFact) -> bool:
    """Join a fact into one of a parameter-fact list's entries; True if changed."""
    new_fact = join(target[index], fact)
    if new_fact != target[index]:
        target[index] = new_fact
        return True

    return False


def _value_defs(func: MenaiCFGFunction) -> dict[int, object]:
    """Map each defined SSA value id in a function to its defining instruction."""
    result: dict[int, object] = {}
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, _VALUE_INSTR_TYPES):
                result[instr.result.id] = instr

    return result


def _all_callee_ids(info: _FunctionInfo) -> set[int]:
    """
    Return every function id that some value in `info` may denote.

    This is the union over all values of the per-value callee sets, i.e. the
    functions whose return facts this function's call results depend on.
    """
    result: set[int] = set()
    for callees in info.callee_of_value.values():
        result |= callees

    return result


def _fact_key(fact: TypeFact) -> tuple[str, MenaiStructType | None]:
    """
    Return a hashable key for a TypeFact.

    TypeFact is a mutable dataclass and so is unhashable; its (kind,
    struct_type) pair identifies it exactly and both components are hashable.
    """
    return (fact.kind, fact.struct_type)


def _referenced_value_ids(func: MenaiCFGFunction) -> set[int]:
    """
    Return the set of every SSA value id referenced (used) anywhere in a
    function.

    This covers instruction operands, patch_closure operands, and terminator
    operands.  A value's defining instruction is not itself a reference.
    """
    referenced: set[int] = set()
    for block in func.blocks:
        for instr in block.instrs:
            referenced.update(_instr_value_uses(instr))

        for patch in block.patch_instrs:
            referenced.add(patch.closure.id)
            referenced.add(patch.value.id)

        if block.terminator is not None:
            referenced.update(_term_value_uses(block.terminator))

    return referenced


def _instr_value_uses(instr: object) -> list[int]:
    """Return the ids of every SSA value used as an operand by instr."""
    if isinstance(instr, MenaiCFGConstInstr):
        return []

    if isinstance(instr, MenaiCFGBuiltinInstr):
        return [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGCallInstr):
        return [instr.func.id] + [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGApplyInstr):
        return [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiCFGMakeClosureInstr):
        return [capture.id for capture in instr.captures]

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGMakeEnumInstr):
        return []

    if isinstance(instr, MenaiCFGMakeListInstr):
        return [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGMakeVectorInstr):
        return [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return [arg.id for arg in instr.args]

    if isinstance(instr, MenaiCFGMakeDictInstr):
        return [val.id for pair in instr.pairs for val in pair]

    if isinstance(instr, MenaiCFGGuardInstr):
        return [instr.value.id]

    if isinstance(instr, MenaiCFGPhiInstr):
        return [val.id for val, _ in instr.incoming]

    return []


def _term_value_uses(term: object) -> list[int]:
    """Return the ids of every SSA value used as an operand by a terminator."""
    if isinstance(term, MenaiCFGBranchTerm):
        return [term.cond.id]

    if isinstance(term, MenaiCFGSwitchIntegerTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGReturnTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGTailCallTerm):
        return [term.func.id] + [arg.id for arg in term.args]

    if isinstance(term, MenaiCFGTailApplyTerm):
        return [term.func.id, term.arg_list.id]

    if isinstance(term, MenaiCFGSelfLoopTerm):
        return [arg.id for arg in term.args]

    if isinstance(term, MenaiCFGRaiseTerm):
        return [term.message.id]

    return []


def _call_graph_sccs(infos: list[_FunctionInfo]) -> dict[int, int]:
    """
    Map each function id to the id of its strongly-connected component in the
    call graph.

    The call graph's edges are the resolved callees of each function.  Two
    functions share an SCC exactly when each can reach the other through calls,
    which is the condition under which parameter-fact propagation between them
    would be circular.  Tarjan's algorithm is used, iteratively so that a deep
    call graph cannot exhaust the Python recursion limit.
    """
    graph: dict[int, list[int]] = {
        id(info.func): [
            callee_id
            for callees in info.callee_of_value.values()
            for callee_id in callees
        ]
        for info in infos
    }

    index_counter = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    index: dict[int, int] = {}
    lowlink: dict[int, int] = {}
    scc_of: dict[int, int] = {}
    scc_id = 0

    for root in graph:
        if root in index:
            continue

        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            node, child_index = work[-1]
            if child_index == 0:
                index[node] = index_counter
                lowlink[node] = index_counter
                index_counter += 1
                stack.append(node)
                on_stack.add(node)

            successors = graph[node]
            if child_index < len(successors):
                work[-1] = (node, child_index + 1)
                successor = successors[child_index]
                if successor not in index:
                    work.append((successor, 0))

                elif successor in on_stack:
                    lowlink[node] = min(lowlink[node], index[successor])

                continue

            work.pop()
            if lowlink[node] == index[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc_of[member] = scc_id
                    if member == node:
                        break

                scc_id += 1

            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    return scc_of
