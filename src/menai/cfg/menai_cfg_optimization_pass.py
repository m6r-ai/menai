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
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGMakeClosureInstr,
    MenaiCFGTerminator,
)
from menai.cfg.menai_cfg_type_fact import TypeFact


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
    """
    type_facts: dict[int, dict[int, TypeFact]] = field(default_factory=dict)

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
