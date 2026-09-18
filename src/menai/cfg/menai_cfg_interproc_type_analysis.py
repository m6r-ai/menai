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
joins, and struct-is-instance? branch refinement.  Parameters start from an
assumption about their type.

Across functions the pass propagates the fact of each call argument into the
corresponding parameter of the callee.  Because a caller's argument facts
depend on the caller's parameter facts, which depend on the callers of the
caller, this is iterated to a fixed point.  The fact lattice is finite and the
join is monotone, so the fixed point is reached.

Call sites within a recursion cycle are handled specially.  A call inside a
cycle has its arguments computed from the very parameters the call would be
used to infer, so its argument facts describe a later iteration, not the first
invocation.  They may degrade an externally-grounded parameter but must not
ground one on their own: otherwise a parameter could be "proven" by a value
derived from itself even though the value the function is first called with is
unconstrained.  The pass therefore tracks, per parameter, the join over call
sites outside the function's recursion component (the external facts) and the
join over call sites inside it (the internal facts).  The effective parameter
fact is the external join degraded by the internal join, but only where an
external call site grounds it; with no external grounding the parameter stays
at BOTTOM and its runtime guards are retained.  A direct self-recursive tail
call is a MenaiCFGSelfLoopTerm; a call between mutually-recursive functions is
an ordinary call.  Both are internal when caller and callee share a
strongly-connected component of the call graph.  Return facts are still
propagated through cycles: a function's return value genuinely is the join
over every return path, recursive ones included.

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

    (struct-get p 'x)  ->  (struct-ref p <index>)
    (struct-set p 'x v) -> (struct-set-ref p <index> v)

The index is resolved from the MenaiStructType's field order.  A struct-get
whose receiver's type is not proven, or whose field argument is not a constant
symbol, is left unchanged and continues to use the runtime hash lookup.  The
rewrite is a pure optimisation: it never changes observable behaviour.

The pass mutates the CFG in place and returns the same root function.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGApplyInstr,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGlobalInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGParamInstr,
    MenaiCFGPhiInstr,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGWholeProgramPass, collect_functions
from menai.cfg.menai_cfg_type_fact import ANY, TypeFact, BOTTOM, fact_for_value, join
from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.menai_value import MenaiInteger, MenaiString, MenaiStructType, MenaiSymbol

# Builtins whose result is a struct of the same type as their first argument.
_STRUCT_PRESERVING_OPS = {'struct-set', 'struct-set-ref'}

# Builtins that read or write a struct field by symbol name.
_FIELD_BY_SYMBOL_OPS = {'struct-get', 'struct-set'}

# Field access builtins and the index-based opcode each rewrites to.
_INDEXED_OP = {
    'struct-get': 'struct-ref',
    'struct-set': 'struct-set-ref',
}

# Instruction types that define a result SSA value.  Guard and patch
# instructions are excluded: they have no result.
_VALUE_INSTR_TYPES = (
    MenaiCFGConstInstr,
    MenaiCFGParamInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGGlobalInstr,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGApplyInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGPhiInstr,
)


class _FunctionInfo:
    """
    Per-function analysis state: the resolved callee of each call, the SSA
    value that denotes each function, the current parameter facts, and the
    current return fact.

    Parameter facts are tracked from two sources.  `external_param_facts` is
    the join over call sites outside the function's recursion component; these
    describe the first invocation's arguments.  `internal_param_facts` is the
    join over call sites inside the component; these describe the arguments of
    later iterations.  `param_facts` is the effective fact: the external join,
    degraded by the internal join, but only when an external call site grounds
    it.  Without external grounding the first invocation's argument is
    unconstrained, so a recursive call site cannot prove the parameter's type.
    """

    def __init__(self, func: MenaiCFGFunction) -> None:
        self.func = func
        self.callee_of_value: dict[int, MenaiCFGFunction] = {}
        self.external_param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.internal_param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.param_facts: list[TypeFact] = [BOTTOM] * func.param_count()
        self.return_fact: TypeFact = BOTTOM

    def recompute_param_facts(self) -> None:
        """
        Derive the effective parameter facts from the external and internal
        joins.  A parameter with no external grounding stays BOTTOM: a
        recursive call site describes a later iteration, not the first.
        """
        self.param_facts = [
            external if external.is_bottom() else join(external, internal)
            for external, internal in zip(self.external_param_facts, self.internal_param_facts)
        ]


class MenaiCFGInterprocTypeAnalysis(MenaiCFGWholeProgramPass):
    """
    Whole-program type analysis that resolves struct field access to indices.

    See the module docstring for the algorithm.
    """

    def __init__(self) -> None:
        """Initialise the per-compilation strongly-connected-component map."""
        self._scc_of: dict[int, int] = {}

    def _optimize_module(self, root: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """Run the interprocedural analysis and rewrite struct field access."""
        functions = collect_functions(root)
        info_of: dict[int, _FunctionInfo] = {id(func): _FunctionInfo(func) for func in functions}
        infos = list(info_of.values())

        self._resolve_all_callees(root, functions, info_of)

        self._scc_of = _call_graph_sccs(infos)

        self._propagate_to_fixed_point(infos, info_of)

        changed = False
        for info in infos:
            facts = self._intra_propagate(info, info.param_facts, info_of)
            info.func.type_facts = facts
            if self._rewrite_field_access(info.func, facts):
                changed = True

        return root, changed

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
            )

    def _resolve_callees(
        self,
        info: _FunctionInfo,
        parent_closure: MenaiCFGMakeClosureInstr | None,
        parent_callees: dict[int, MenaiCFGFunction],
    ) -> None:
        """
        Determine which function each SSA value denotes, where it can be
        resolved.

        Four sources are followed:
          - a make_closure result denotes its function;
          - a phi whose incoming values all denote the same function denotes
            that function;
          - a dict-get of a constant key from a dict whose matching value
            denotes a function denotes that function.  This is how a module's
            exported functions are reached: a module is a dict of functions
            and callers fetch them with dict-get;
          - a free variable denotes whatever function the corresponding
            capture denotes in the parent function.  This is how a function
            reaches a letrec sibling: the sibling is captured, not created
            locally;
        """
        func = info.func
        callee_of_value = info.callee_of_value
        value_defs = _value_defs(func)

        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    callee_of_value[instr.result.id] = instr.function

        if parent_closure is not None:
            for block in func.blocks:
                for instr in block.instrs:
                    if isinstance(instr, MenaiCFGFreeVarInstr):
                        resolved = _free_var_callee(instr, parent_closure, parent_callees)
                        if resolved is not None:
                            callee_of_value[instr.result.id] = resolved

        changed = True
        while changed:
            changed = False
            for block in func.blocks:
                for instr in block.instrs:
                    if not isinstance(instr, _VALUE_INSTR_TYPES):
                        continue

                    resolved = self._instr_callee(instr, callee_of_value, value_defs)
                    if resolved is not None and callee_of_value.get(instr.result.id) is not resolved:
                        callee_of_value[instr.result.id] = resolved
                        changed = True

    def _instr_callee(
        self,
        instr: object,
        callee_of_value: dict[int, MenaiCFGFunction],
        value_defs: dict[int, object],
    ) -> MenaiCFGFunction | None:
        """Resolve the function an instruction's result denotes, if any."""
        if isinstance(instr, MenaiCFGPhiInstr):
            return self._phi_callee(instr, callee_of_value)

        if isinstance(instr, MenaiCFGBuiltinInstr) and instr.op == 'dict-get':
            return self._dict_get_callee(instr, callee_of_value, value_defs)

        return None

    @staticmethod
    def _dict_get_callee(
        instr: MenaiCFGBuiltinInstr,
        callee_of_value: dict[int, MenaiCFGFunction],
        value_defs: dict[int, object],
    ) -> MenaiCFGFunction | None:
        """
        Resolve (dict-get <dict> <key>) where the dict is a make_dict with a
        constant string key whose value denotes a function.

        The dict is last-wins for duplicate keys, so the resolved pair must be
        the last pair whose key could equal the lookup key at runtime.  Every
        following pair must therefore have a constant string key that differs
        from the lookup key; if any following key is not a constant string it
        could equal the lookup key at runtime, and the result is not resolved.
        """
        if len(instr.args) < 2:
            return None

        dict_instr = value_defs.get(instr.args[0].id)
        if not isinstance(dict_instr, MenaiCFGMakeDictInstr):
            return None

        key_name = _const_string(value_defs.get(instr.args[1].id))
        if key_name is None:
            return None

        resolved = None
        for key_val, entry_val in dict_instr.pairs:
            pair_key = _const_string(value_defs.get(key_val.id))
            if pair_key is None:
                # A non-constant key could equal the lookup key at runtime and,
                # being later, would override any match found so far.
                resolved = None

            elif pair_key == key_name:
                resolved = callee_of_value.get(entry_val.id)

        return resolved

    @staticmethod
    def _phi_callee(
        instr: MenaiCFGPhiInstr,
        callee_of_value: dict[int, MenaiCFGFunction],
    ) -> MenaiCFGFunction | None:
        """Return the common function denoted by all of a phi's incoming values."""
        result: MenaiCFGFunction | None = None
        for incoming_val, _ in instr.incoming:
            resolved = callee_of_value.get(incoming_val.id)
            if resolved is None:
                return None

            if result is None:
                result = resolved

            elif result is not resolved:
                return None

        return result

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
        """
        changed = True
        while changed:
            changed = False
            for info in infos:
                facts = self._intra_propagate(info, info.param_facts, info_of)
                if self._propagate_call_args(info, facts, info_of):
                    changed = True

                if self._propagate_return_fact(info, facts, info_of):
                    changed = True

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
                callee = info.callee_of_value.get(term.func.id)
                if callee is not None:
                    result = join(result, info_of[id(callee)].return_fact)

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
    ) -> bool:
        """
        Join the fact of each call argument into the callee's parameter facts.

        External call sites (outside the callee's recursion component) update
        the callee's external facts; intra-component call sites update its
        internal facts.  The callee's effective parameter facts are then
        recomputed from the two.

        Returns True if any parameter fact changed.
        """
        changed = False
        for callee_func, args, internal in self._call_sites(info):
            callee = info_of[id(callee_func)]
            if self._join_arg_facts(callee, args, facts, internal):
                callee.recompute_param_facts()
                changed = True

        return changed

    @staticmethod
    def _join_arg_facts(
        callee: _FunctionInfo,
        args: list,
        facts: dict[int, TypeFact],
        internal: bool,
    ) -> bool:
        """
        Join a call's argument facts into the callee's external or internal
        parameter facts.

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
            if _join_param(target, i, facts.get(args[i].id, BOTTOM)):
                changed = True

        if callee.func.is_variadic:
            if _join_param(target, param_count - 1, TypeFact(kind='list')):
                changed = True

        return changed

    def _call_sites(
        self,
        info: _FunctionInfo,
    ) -> list[tuple[MenaiCFGFunction, list, bool]]:
        """
        Enumerate the call sites that contribute to a callee's parameter facts,
        as (callee_function, argument_values, internal) triples.

        A direct self-recursive tail call (MenaiCFGSelfLoopTerm) targets the
        enclosing function itself.

        `internal` is True for a call site within the caller's own
        strongly-connected component of the call graph, i.e. inside a recursion
        cycle.  Such a call's arguments are computed from the very parameters
        the call would be used to infer, so its argument facts describe a later
        iteration, not the first invocation.  They may degrade an
        externally-grounded parameter but must not ground one on their own; the
        caller routes them to the callee's internal facts accordingly.
        Return-fact propagation is unaffected: a function's return value
        genuinely is the join over every path, recursive ones included.
        """
        result: list[tuple[MenaiCFGFunction, list, bool]] = []
        caller_scc = self._scc_of[id(info.func)]
        for block in info.func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGCallInstr):
                    callee = info.callee_of_value.get(instr.func.id)
                    if callee is not None:
                        result.append((callee, instr.args, self._scc_of[id(callee)] == caller_scc))

            term = block.terminator
            if isinstance(term, MenaiCFGTailCallTerm):
                callee = info.callee_of_value.get(term.func.id)
                if callee is not None:
                    result.append((callee, term.args, self._scc_of[id(callee)] == caller_scc))

            elif isinstance(term, MenaiCFGSelfLoopTerm):
                result.append((info.func, term.args, True))

        return result

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

        while True:
            changed = False
            for block in func.blocks:
                block_facts = dict(self._block_incoming(block, facts, param_facts, value_defs))
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
        facts: dict[int, TypeFact],
        param_facts: list[TypeFact],
        value_defs: dict[int, object],
    ) -> dict[int, TypeFact]:
        """
        Compute the incoming facts for a block: parameter facts for the entry
        block, and the meet of predecessor outgoing facts otherwise.

        A block reached from a struct-is-instance? true edge inherits the
        refined struct type of the predicate's argument.
        """
        if not block.predecessors:
            return self._entry_facts(block, param_facts)

        result: dict[int, TypeFact] = {}
        first = True
        for pred in block.predecessors:
            pred_facts = self._outgoing_facts(pred, facts)
            refinement = self._true_edge_refinement(pred, block, value_defs)
            if refinement is not None:
                val_id, refined = refinement
                pred_facts = dict(pred_facts)
                pred_facts[val_id] = refined

            if first:
                result = dict(pred_facts)
                first = False

            else:
                for val_id, fact in pred_facts.items():
                    if val_id in result:
                        result[val_id] = join(result[val_id], fact)

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

    @staticmethod
    def _true_edge_refinement(
        pred: MenaiCFGBlock,
        succ: MenaiCFGBlock,
        value_defs: dict[int, object],
    ) -> tuple[int, TypeFact] | None:
        """
        If pred branches to succ on the true edge and the condition is
        (struct-is-instance? v TypeName), return (v.id, Known('struct', type)).

        The struct type is found from the constant structtype argument.
        """
        term = pred.terminator
        if not isinstance(term, MenaiCFGBranchTerm) or term.true_block is not succ:
            return None

        for instr in pred.instrs:
            if (
                isinstance(instr, MenaiCFGBuiltinInstr)
                and instr.op == 'struct-is-instance?'
                and instr.result.id == term.cond.id
                and len(instr.args) == 2
            ):
                type_instr = value_defs.get(instr.args[1].id)
                if isinstance(type_instr, MenaiCFGConstInstr) and isinstance(type_instr.value, MenaiStructType):
                    return instr.args[0].id, TypeFact(kind='struct', struct_type=type_instr.value)

        return None

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

        if isinstance(instr, MenaiCFGCallInstr):
            callee = info.callee_of_value.get(instr.func.id)
            if callee is not None:
                return info_of[id(callee)].return_fact

            return BOTTOM

        if isinstance(instr, MenaiCFGBuiltinInstr):
            return self._builtin_fact(instr, facts)

        return BOTTOM

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

        # The result type is genuinely unknown (e.g. struct-ref, dict-get,
        # list-first return a value whose type depends on the input), so the
        # result could be anything: ANY, not BOTTOM.
        return ANY

    def _rewrite_field_access(
        self,
        func: MenaiCFGFunction,
        facts: dict[int, TypeFact],
    ) -> bool:
        """
        Rewrite struct-get/struct-set calls to their index-based forms where the
        receiver's struct type and the field index are both known.

        Returns True if any instruction was rewritten.
        """
        changed = False
        next_value_id = _max_value_id(func) + 1
        value_defs = _value_defs(func)
        for block in func.blocks:
            new_instrs: list = []
            for instr in block.instrs:
                if not isinstance(instr, MenaiCFGBuiltinInstr):
                    new_instrs.append(instr)
                    continue

                if instr.op not in _FIELD_BY_SYMBOL_OPS:
                    new_instrs.append(instr)
                    continue

                index = self._resolve_field_index(instr, facts, value_defs)
                if index is None:
                    new_instrs.append(instr)
                    continue

                index_value = MenaiCFGValue(id=next_value_id, hint="field_index")
                next_value_id += 1
                new_instrs.append(MenaiCFGConstInstr(result=index_value, value=MenaiInteger(index)))
                facts[index_value.id] = TypeFact(kind='integer')

                new_args = list(instr.args)
                new_args[1] = index_value
                new_instrs.append(MenaiCFGBuiltinInstr(
                    result=instr.result,
                    op=_INDEXED_OP[instr.op],
                    args=new_args,
                ))
                changed = True

            block.instrs = new_instrs

        return changed

    @staticmethod
    def _resolve_field_index(
        instr: MenaiCFGBuiltinInstr,
        facts: dict[int, TypeFact],
        value_defs: dict[int, object],
    ) -> int | None:
        """
        Return the constant field index for a struct-get/struct-set call, or
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


def _free_var_callee(
    instr: MenaiCFGFreeVarInstr,
    parent_closure: MenaiCFGMakeClosureInstr,
    parent_callees: dict[int, MenaiCFGFunction],
) -> MenaiCFGFunction | None:
    """
    Resolve a free variable to the function its capture denotes.

    A free variable's index is a position in the child function's free_vars
    list.  The list is ordered sibling free vars first, then outer free vars.
    The make_closure instruction's captures are the outer captures, and the
    codegen places them in the tail of the slot range, so captures[i]
    corresponds to free_vars[len(free_vars) - len(captures) + i].

    Sibling free vars are not in the captures list; they are installed by
    PATCH_CLOSURE after all sibling closures exist.  A sibling free var is
    resolved by finding the parent value that denotes the sibling function of
    the same name.
    """
    free_vars = parent_closure.function.free_vars
    captures = parent_closure.captures
    outer_start = len(free_vars) - len(captures)

    if instr.index >= outer_start:
        captured = captures[instr.index - outer_start]
        return parent_callees.get(captured.id)

    for func in parent_callees.values():
        if func.binding_name == instr.var_name:
            return func

    return None


def _const_string(instr: object) -> str | None:
    """Return the string value of a constant-string instruction, if it is one."""
    if isinstance(instr, MenaiCFGConstInstr) and isinstance(instr.value, MenaiString):
        return instr.value.value

    return None


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


def _max_value_id(func: MenaiCFGFunction) -> int:
    """Return the largest SSA value id defined anywhere in a function."""
    largest = -1
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, _VALUE_INSTR_TYPES) and instr.result.id > largest:
                largest = instr.result.id

    return largest


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
        id(info.func): [id(callee) for callee in info.callee_of_value.values()]
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
