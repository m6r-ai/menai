"""
Menai CFG optimization pass base classes.

Passes operate at module scope: a pass is handed the root MenaiCFGFunction
and returns a (possibly new) root together with a flag indicating whether any
changes were made.  The pass manager uses that flag to drive fixed-point
iteration.

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

from menai.cfg.menai_cfg import MenaiCFGFunction, MenaiCFGMakeClosureInstr


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

    def optimize(self, root: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
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

    def optimize(self, root: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform `root` and all nested lambda functions it contains.

        Subclasses implement `_optimize_function` to apply their transformation
        to a single flat function.  This method handles recursion into nested
        lambdas embedded in MenaiCFGMakeClosureInstr instructions automatically.

        Args:
            root: The root function to optimize.

        Returns:
            A tuple of (new_root, changed) where changed is True if the pass
            made at least one transformation anywhere in the function tree.
        """
        root, changed = self._optimize_function(root)
        root, nested_changed = self._optimize_nested(root)
        return root, changed or nested_changed

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform a single flat CFG function, returning an optimized version.

        The pass must not assume it is the only pass being run; the pass
        manager may run multiple passes to a fixed point.

        Args:
            func: The CFG function to optimize.

        Returns:
            A tuple of (new_func, changed) where changed is True if the pass
            made at least one transformation.  Returning the original function
            unchanged with changed=False signals the pass manager that this
            pass has reached a fixed point and need not be re-run.
        """
        raise NotImplementedError

    def _optimize_nested(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Recursively optimize all MenaiCFGFunction objects embedded in
        MenaiCFGMakeClosureInstr instructions anywhere in `func`.

        When a nested lambda is optimized and returns a new function object,
        the MakeClosure instruction is updated in place.

        Returns `func` (possibly with mutated MakeClosure instructions) and
        a flag indicating whether any nested function was changed.
        """
        changed = False
        for block in func.blocks:
            for i, instr in enumerate(block.instrs):
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    new_child, child_changed = self.optimize(instr.function)
                    if child_changed:
                        block.instrs[i] = MenaiCFGMakeClosureInstr(
                            result=instr.result,
                            function=new_child,
                            captures=instr.captures,
                            needs_patching=instr.needs_patching,
                        )
                        changed = True

        return func, changed


class MenaiCFGWholeProgramPass(MenaiCFGOptimizationPass):
    """
    Base class for CFG optimization passes that need a whole-program view.

    Subclasses implement _optimize_module, which is handed the root function
    and may traverse the entire function tree (see collect_functions) and
    rewrite nested functions through their MenaiCFGMakeClosureInstr
    instructions.
    """

    def optimize(self, root: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform the module rooted at `root`, delegating to _optimize_module.
        """
        return self._optimize_module(root)

    def _optimize_module(self, root: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        """
        Transform the module rooted at `root`, returning the new root and a
        flag indicating whether any changes were made.
        """
        raise NotImplementedError
