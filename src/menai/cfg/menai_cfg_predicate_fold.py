"""
CFG pass: fold type predicates whose result is statically known.

A type-predicate builtin (none?, integer?, string?, ...) returns #t exactly
when its argument's type is the predicate's type.  When the argument's type is
proven, the predicate's result is known without a runtime check, and the branch
it feeds can be re-wired to the taken target.  The predicate instruction is
then dead (unless its result is used elsewhere) and is removed.

This pass consumes the per-value type facts computed by
MenaiCFGInterprocTypeAnalysis (stored on MenaiCFGFunction.type_facts) and runs
after it.  It is a pure optimisation: where the argument's type is not proven
the predicate and branch are left untouched, so behaviour is never changed.

Example — the deflate prefix-key pattern:

    (let ((key (if (i+3 > len) #none (bytes-read-u24-be b i))))
      (if (none? key) table ...))

After inlining and MenaiCFGBranchConstProp the #none arm is folded, leaving a
single-arm phi whose value is the bytes-read-u24-be result.  The type analysis
proves that result is an integer, so (none? <integer>) is #f and the branch is
re-wired to the false edge; the none? check disappears.

Locally-proven arguments only
-----------------------------
The pass folds a predicate only when its argument's fact is *locally proven* —
derived solely from constants, value constructors, and builtins with a fixed
result type within this function, combined through phi nodes.  A fact derived
from a parameter, a free variable, or a call result is not used: the
interprocedural analysis can compute such a fact more precisely than is sound
when a function value escapes into a call it cannot resolve (for example a
function passed to a higher-order prelude function that calls it with values of
mixed type).  Restricting to locally-proven facts keeps the fold sound without
depending on the precision of the interprocedural analysis.

Scope
-----
Only the general type predicates are folded.  struct-is-instance? is a
type-identity test rather than a kind test and needs struct-type resolution;
it is not handled here.
"""

from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES, TYPE_PREDICATES
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGMakeDictInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGPhiInstr,
    MenaiCFGStructSetIndexedInstr,
    relink_predecessors,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGPerFunctionPass
from menai.cfg.menai_cfg_type_fact import TypeFact


class MenaiCFGPredicateFold(MenaiCFGPerFunctionPass):
    """
    Fold type-predicate builtins whose argument's locally-proven type fact
    determines the result, and re-wire the branch they feed.

    See the module docstring for the algorithm and the locally-proven rule.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """Fold predicates to a fixed point within the function."""
        changed_overall = False

        while True:
            if not self._run_one_round(func):
                break

            changed_overall = True
            relink_predecessors(func)

        return func, changed_overall

    def _run_one_round(self, func: MenaiCFGFunction) -> bool:
        """
        Fold every foldable predicate in the function once.

        Returns True if any predicate was folded.
        """
        changed = False
        value_defs = _value_defs(func)
        local = _local_values(value_defs)
        used = _referenced_value_ids(func)

        for block in func.blocks:
            term = block.terminator
            if not isinstance(term, MenaiCFGBranchTerm):
                continue

            pred_instr = _branch_predicate(block, term)
            if pred_instr is None:
                continue

            arg = pred_instr.args[0]
            if arg.id not in local:
                continue

            fact = func.type_facts.get(arg.id)
            result = _evaluate_predicate(pred_instr.op, fact)
            if result is None:
                continue

            target = term.true_block if result else term.false_block
            block.terminator = MenaiCFGJumpTerm(target=target)

            # The predicate result is no longer consumed by the branch.  Drop
            # the instruction when nothing else uses its value.
            if used.get(pred_instr.result.id, 0) <= 1:
                block.instrs.remove(pred_instr)

            changed = True

        return changed


def _branch_predicate(
    block: MenaiCFGBlock,
    term: MenaiCFGBranchTerm,
) -> MenaiCFGBuiltinInstr | None:
    """
    Return the type-predicate builtin whose result is the branch condition, or
    None if the branch condition is not a type predicate.
    """
    for instr in block.instrs:
        if (
            isinstance(instr, MenaiCFGBuiltinInstr)
            and instr.op in TYPE_PREDICATES
            and instr.result.id == term.cond.id
            and len(instr.args) == 1
        ):
            return instr

    return None


def _evaluate_predicate(op: str, fact: TypeFact | None) -> bool | None:
    """
    Evaluate a type predicate against a proven type fact.

    Returns True when the fact's kind is the predicate's type, False when the
    fact proves a different type, and None when the fact is absent, BOTTOM, or
    ANY (nothing is proven).
    """
    if fact is None or not fact.is_known():
        return None

    return fact.kind == TYPE_PREDICATES[op]


def _value_defs(func: MenaiCFGFunction) -> dict[int, object]:
    """Map each defined SSA value id in a function to its defining instruction."""
    result: dict[int, object] = {}
    for block in func.blocks:
        for instr in block.instrs:
            result_id = getattr(instr, 'result', None)
            if result_id is not None:
                result[result_id.id] = instr

    return result


def _local_values(value_defs: dict[int, object]) -> set[int]:
    """
    Return the ids of values whose type fact is locally proven.

    A value is locally proven when its type is fixed by its own definition and
    the definitions it depends on, without reference to a parameter, a free
    variable, or a call result:

      - a constant, a list/vector/set/dict/struct constructor, or a builtin
        whose signature fixes its result type;
      - a struct-set-indexed whose receiver is locally proven (it preserves the
        receiver's struct type);
      - a phi whose incoming values are all locally proven.

    Every other value — a parameter, a free variable, a call or apply result, a
    builtin whose result type is unknown, or a phi with any non-local incoming —
    is not locally proven.
    """
    local: set[int] = set()
    changed = True
    while changed:
        changed = False
        for val_id, instr in value_defs.items():
            if val_id in local:
                continue

            if _definition_is_local(instr, local):
                local.add(val_id)
                changed = True

    return local


def _definition_is_local(instr: object, local: set[int]) -> bool:
    """
    Return True if an instruction's result is locally proven, given the set of
    value ids already known to be locally proven.
    """
    if isinstance(instr, MenaiCFGConstInstr):
        return True

    if isinstance(
        instr,
        (
            MenaiCFGMakeListInstr,
            MenaiCFGMakeVectorInstr,
            MenaiCFGMakeSetInstr,
            MenaiCFGMakeDictInstr,
            MenaiCFGMakeStructInstr,
        ),
    ):
        return True

    if isinstance(instr, MenaiCFGBuiltinInstr):
        sig = BUILTIN_TYPE_SIGNATURES.get(instr.op)
        return sig is not None and sig[1] is not None

    if isinstance(instr, MenaiCFGStructSetIndexedInstr):
        return instr.struct.id in local

    if isinstance(instr, MenaiCFGPhiInstr):
        return all(val.id in local for val, _ in instr.incoming)

    return False


def _referenced_value_ids(func: MenaiCFGFunction) -> dict[int, int]:
    """
    Return a count of how many times each SSA value is referenced.

    Covers instruction operands, patch_closure operands, and terminator
    operands.  A value's defining instruction is not itself a reference.
    """
    counts: dict[int, int] = {}

    def add(val_id: int) -> None:
        counts[val_id] = counts.get(val_id, 0) + 1

    for block in func.blocks:
        for instr in block.instrs:
            for val_id in value_ids_in_instr(instr):
                add(val_id)

        for patch in block.patch_instrs:
            add(patch.closure.id)
            add(patch.value.id)

        if block.terminator is not None:
            for val_id in value_ids_in_term(block.terminator):
                add(val_id)

    return counts
