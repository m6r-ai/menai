"""
CFG pass: fold type predicates whose result is statically known.

A type-predicate builtin (none?, integer?, string?, ...) returns #t exactly
when its argument's type is the predicate's type.  When the argument's type is
proven, the predicate's result is known without a runtime check, and the branch
it feeds can be re-wired to the taken target.  The predicate instruction is
then dead (unless its result is used elsewhere) and is removed, and any
instruction that only fed the predicate is now dead too; the operand chain is
swept by prune_dead_definitions so the fold leaves no orphaned instructions
behind.

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

Soundness
---------
Any fact the interprocedural analysis reports may be consumed, including one
derived from a parameter, a free variable, or a call result.  The analysis
leaves the parameters of every function that can be reached from a call site it
cannot resolve unconstrained, so it never reports a proven type for a parameter
that can receive a value of another type.  Folding on such a fact therefore
cannot delete a branch that must be taken at runtime.

Scope
-----
Only the general type predicates are folded.  struct-is-instance? is a
type-identity test rather than a kind test and needs struct-type resolution;
it is not handled here.
"""

from dataclasses import replace

from menai.bytecode.menai_type_signatures import TYPE_PREDICATES
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
)
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    MenaiCFGPerFunctionPass,
    prune_dead_definitions,
    replace_block,
)
from menai.cfg.menai_cfg_type_fact import TypeFact


class MenaiCFGPredicateFold(MenaiCFGPerFunctionPass):
    """
    Fold type-predicate builtins whose argument's proven type fact determines
    the result, and re-wire the branch they feed.

    See the module docstring for the algorithm and the soundness argument.
    """

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """Fold predicates to a fixed point within the function."""
        changed_overall = False

        while True:
            func, round_changed = self._run_one_round(func, context)
            if not round_changed:
                break

            changed_overall = True

        return func, changed_overall

    def _run_one_round(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Fold every foldable predicate in the function once.

        Returns the (possibly new) function and whether any predicate was folded.
        """
        changed = False
        type_facts = context.facts_for(func)

        for block in func.blocks:
            term = block.terminator
            if not isinstance(term, MenaiCFGBranchTerm):
                continue

            pred_instr = _branch_predicate(block, term)
            if pred_instr is None:
                continue

            arg = pred_instr.args[0]
            fact = type_facts.get(arg.id)
            result = _evaluate_predicate(pred_instr.op, fact)
            if result is None:
                continue

            target = term.true_block if result else term.false_block

            new_block = replace(
                block,
                terminator=MenaiCFGJumpTerm(target=target),
            )
            func = replace_block(func, new_block)

            changed = True

        if changed:
            # The folded predicate's result is no longer read by the branch, and
            # the instructions that only fed the predicate are now dead too.
            # Sweep them so the fold does not leave an orphaned operand chain
            # for the bytecode to carry.
            func, _ = prune_dead_definitions(func)

        return func, changed


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
