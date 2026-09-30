"""
Menai CFG optimization pass base classes.

Passes operate at module scope: a pass is handed the root MenaiCFGFunction
and a MenaiCFGContext, and returns a (possibly new) root together with a flag
indicating whether any changes were made.  The pass manager uses that flag to
drive fixed-point iteration.

The CFG is an immutable value (ADR-0033): a pass must not mutate the function
it is handed.  It returns a new function when it changes anything.

The context carries state that is not part of the program: the type facts
produced by the interprocedural analysis and consumed by the passes that
depend on it.  It is threaded through the pipeline alongside the CFG.

There is no separate module object.  The module is the root function plus
every function reachable from it through MenaiCFGMakeClosureInstr
instructions; collect_functions enumerates that tree.

Two pass contracts are provided:

- MenaiCFGPerFunctionPass — the transformation is defined per function.
  Subclasses implement _optimize_function and the base class handles the
  traversal of the function tree and the write-back of any replaced nested
  functions.

- MenaiCFGWholeProgramPass — the transformation needs a whole-program view
  (e.g. an interprocedural analysis over the call graph).  Subclasses
  implement _optimize_module and own their own traversal and write-back.
"""

from dataclasses import dataclass, field, replace

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGParamInstr,
    MenaiCFGTerminator,
    result_id_in_instr,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_type_fact import TypeFact
from menai.menai_value import MenaiStructType


@dataclass
class MenaiCFGContext:
    """
    Cross-pass state threaded through the CFG pipeline.

    The context is not part of the program value; it carries analysis output
    that passes produce and consume.  It is keyed by function identity so that
    a fact set computed for one function is not confused with another's.

    `type_facts` maps a function's `fact_key` to that function's per-value type
    facts.  It is written by the interprocedural type analysis and read by
    guard insertion and predicate folding.  The key is the function's stable
    identity, not `id()`: a pass rebuilds a function it changes, and `id()` is
    only valid while the original object is alive, so a rebuilt function would
    otherwise look up the wrong entry (or none).  `fact_key` is preserved by
    `dataclasses.replace`, so a rebuilt function keeps its facts.

    `struct_type_of_test` maps a function's `fact_key` to a map from the SSA
    value id of a struct-is-instance? test's structtype argument to the
    MenaiStructType that argument names.  It is written by the interprocedural
    type analysis, which resolves the argument through the enclosing lexical
    scope (a constant in the same function, or a free variable whose capture
    chain bottoms out in an ancestor's constant).  It is read by struct
    instance folding, which needs the resolved type to decide whether a test
    is statically true.  Recording it here keeps the resolution in one place:
    the analysis already performs it to refine the receiver's type on the true
    edge, and a consumer must not re-derive it and drift.
    """
    type_facts: dict[int, dict[int, TypeFact]] = field(default_factory=dict)
    struct_type_of_test: dict[int, dict[int, MenaiStructType]] = field(default_factory=dict)

    def facts_for(self, func: MenaiCFGFunction) -> dict[int, TypeFact]:
        """Return the type facts recorded for `func`, or an empty map."""
        if func.fact_key is None:
            return {}

        return self.type_facts.get(func.fact_key, {})

    def set_facts(self, func: MenaiCFGFunction, facts: dict[int, TypeFact]) -> None:
        """Record the type facts for `func`."""
        if func.fact_key is None:
            return

        self.type_facts[func.fact_key] = facts

    def record_struct_type_of_test(
        self,
        func: MenaiCFGFunction,
        value_id: int,
        struct_type: MenaiStructType,
    ) -> None:
        """Record the struct type named by a struct-is-instance? test's argument."""
        if func.fact_key is None:
            return

        self.struct_type_of_test.setdefault(func.fact_key, {})[value_id] = struct_type

    def struct_type_for_test(
        self,
        func: MenaiCFGFunction,
        value_id: int,
    ) -> MenaiStructType | None:
        """Return the struct type named by a test's structtype argument, or None."""
        if func.fact_key is None:
            return None

        return self.struct_type_of_test.get(func.fact_key, {}).get(value_id)


def replace_block_instrs(
    block: MenaiCFGBlock,
    instrs: tuple[MenaiCFGInstr, ...],
) -> MenaiCFGBlock:
    """Return a copy of `block` with a new instruction list."""
    return replace(block, instrs=instrs)


def replace_block_terminator(
    block: MenaiCFGBlock,
    terminator: MenaiCFGTerminator | None,
) -> MenaiCFGBlock:
    """Return a copy of `block` with a new terminator."""
    return replace(block, terminator=terminator)


def replace_blocks(
    func: MenaiCFGFunction,
    blocks: tuple[MenaiCFGBlock, ...],
) -> MenaiCFGFunction:
    """Return a copy of `func` with a new block list."""
    return replace(func, blocks=blocks)


def replace_block(
    func: MenaiCFGFunction,
    block: MenaiCFGBlock,
) -> MenaiCFGFunction:
    """Return a copy of `func` with `block` replacing the block of the same id."""
    return replace(
        func,
        blocks=tuple(block if b.id == block.id else b for b in func.blocks),
    )


def collect_functions(root: MenaiCFGFunction) -> list[MenaiCFGFunction]:
    """
    Enumerate every function reachable from `root`, root first.

    Nested lambdas are reached through the MenaiCFGMakeClosureInstr
    instructions embedded in each function's blocks.  The order is a
    depth-first pre-order walk of the function tree.
    """
    result: list[MenaiCFGFunction] = []
    _collect(root, result)
    return result


def _collect(func: MenaiCFGFunction, result: list[MenaiCFGFunction]) -> None:
    """Recursively collect `func` and all functions nested within it."""
    result.append(func)
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGMakeClosureInstr):
                _collect(instr.function, result)


def value_reference_counts(func: MenaiCFGFunction) -> dict[int, int]:
    """
    Return a count of how many times each SSA value is referenced in `func`.

    Covers instruction operands, patch_closure operands, and terminator
    operands.  A value's defining instruction is not itself a reference.

    Counts are per function, not per block: a value defined in one block may be
    referenced by a phi or a terminator in another, and a removal decision must
    see every such reference.
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


def prune_dead_definitions(
    func: MenaiCFGFunction,
) -> tuple[MenaiCFGFunction, bool]:
    """
    Remove instructions whose defined value is never referenced, transitively.

    A pass that deletes an instruction (e.g. a folded type test) leaves the
    instructions that only fed it with one fewer use, and they may now be dead.
    This removes those, and then any instructions that only fed them, to a
    fixed point, so a fold does not leave an orphaned operand chain behind for
    the bytecode to carry.

    Removal is safe unconditionally because Menai is pure (ADR-0007): an
    instruction whose result is never read has no observable effect.

    MenaiCFGParamInstr and MenaiCFGFreeVarInstr are never removed even when
    their result is unreferenced: they establish a parameter slot or a capture
    load in the entry block, and the VM codegen depends on them even when the
    value is unused (an unused parameter, or a capture the body never reads).
    MenaiCFGGuardInstr and MenaiCFGPatchClosureInstr define no result, so they
    are not candidates: a guard raises at runtime and a patch mutates a closure
    during letrec fixup, and neither is dead code.

    The counts are recomputed each round so that the decrements from one
    removal are seen by the next; the loop terminates because each round either
    removes at least one instruction or stops.
    """
    changed_overall = False

    while True:
        counts = value_reference_counts(func)
        removed_any = False
        new_blocks: list[MenaiCFGBlock] = []

        for block in func.blocks:
            kept: list[MenaiCFGInstr] = []
            for instr in block.instrs:
                result_id = result_id_in_instr(instr)
                if (
                    result_id is not None
                    and counts.get(result_id, 0) == 0
                    and not isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr))
                ):
                    removed_any = True
                    continue

                kept.append(instr)

            if len(kept) != len(block.instrs):
                block = replace(block, instrs=tuple(kept))

            new_blocks.append(block)

        if not removed_any:
            break

        func = replace(func, blocks=tuple(new_blocks))
        changed_overall = True

    return func, changed_overall


class MenaiCFGOptimizationPass:
    """
    Common base for CFG optimization passes.

    Defines the module-level contract: optimize is handed the root function
    and returns the (possibly new) root function plus a changed flag.  The
    two concrete contracts (per-function and whole-program) are provided by
    MenaiCFGPerFunctionPass and MenaiCFGWholeProgramPass.
    """

    def optimize(
        self,
        root: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform the module rooted at `root`, returning the new root and a
        flag indicating whether any changes were made.
        """
        raise NotImplementedError


class MenaiCFGPerFunctionPass(MenaiCFGOptimizationPass):
    """
    Base class for CFG optimization passes whose transformation is defined
    per function.

    Subclasses implement _optimize_function to transform a single flat
    function.  This class handles the traversal of the function tree and the
    write-back of any nested function that a pass replaces.
    """

    def optimize(
        self,
        root: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform `root` and all nested lambda functions it contains.

        Subclasses implement `_optimize_function` to apply their transformation
        to a single flat function.  This method handles recursion into nested
        lambdas embedded in MenaiCFGMakeClosureInstr instructions automatically.

        Args:
            root: The root function to optimize.
            context: Cross-pass state.

        Returns:
            A tuple of (new_root, changed) where changed is True if the pass
            made at least one transformation anywhere in the function tree.
        """
        root, changed = self._optimize_function(root, context)
        root, nested_changed = self._optimize_nested(root, context)
        return root, changed or nested_changed

    def _optimize_function(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform a single flat CFG function, returning an optimized version.

        The pass must not assume it is the only pass being run; the pass
        manager may run multiple passes to a fixed point.

        Args:
            func: The CFG function to optimize.
            context: Cross-pass state.

        Returns:
            A tuple of (new_func, changed) where changed is True if the pass
            made at least one transformation.  Returning the original function
            unchanged with changed=False signals the pass manager that this
            pass has reached a fixed point and need not be re-run.
        """
        raise NotImplementedError

    def _optimize_nested(
        self,
        func: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Recursively optimize all MenaiCFGFunction objects embedded in
        MenaiCFGMakeClosureInstr instructions anywhere in `func`.

        When a nested lambda is optimized and returns a new function object,
        the containing block is rebuilt with a new MakeClosure instruction.

        Returns `func` (possibly with rebuilt blocks) and a flag indicating
        whether any nested function was changed.
        """
        changed = False
        new_blocks: list[MenaiCFGBlock] = []
        for block in func.blocks:
            new_instrs: list[MenaiCFGInstr] = []
            block_changed = False
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    new_child, child_changed = self.optimize(instr.function, context)
                    if child_changed:
                        instr = MenaiCFGMakeClosureInstr(
                            result=instr.result,
                            function=new_child,
                            captures=instr.captures,
                            needs_patching=instr.needs_patching,
                        )
                        changed = True
                        block_changed = True

                new_instrs.append(instr)

            if block_changed:
                block = replace_block_instrs(block, tuple(new_instrs))

            new_blocks.append(block)

        if changed:
            func = replace_blocks(func, tuple(new_blocks))

        return func, changed


class MenaiCFGWholeProgramPass(MenaiCFGOptimizationPass):
    """
    Base class for CFG optimization passes that need a whole-program view.

    Subclasses implement _optimize_module, which is handed the root function
    and may traverse the entire function tree (see collect_functions) and
    rewrite nested functions through their MenaiCFGMakeClosureInstr
    instructions.
    """

    def optimize(
        self,
        root: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform the module rooted at `root`, delegating to _optimize_module.
        """
        return self._optimize_module(root, context)

    def _optimize_module(
        self,
        root: MenaiCFGFunction,
        context: MenaiCFGContext,
    ) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform the module rooted at `root`, returning the new root and a
        flag indicating whether any changes were made.
        """
        raise NotImplementedError
