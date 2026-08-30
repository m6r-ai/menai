"""
CFG pass: dead capture elimination.

After all other CFG optimizations have run, some captured free variables may
no longer be referenced anywhere in the function body.  The most common case
is a self-recursive ``letrec`` lambda whose only self-reference is a tail call
that the CFG builder has already converted to a ``SelfLoopTerm`` (back-edge
jump).  The self-capture slot is allocated by the ``letrec`` lowering but never
read, because the self-loop reuses the function's own registers instead of
loading the closure from the capture.

This pass detects and removes such dead captures.  For each
``MakeClosureInstr`` in the parent function, it scans the child
``MenaiCFGFunction`` for ``FreeVarInstr`` instances whose result SSA value is
never referenced by any instruction or terminator.  Dead captures are removed
from the child, and the parent's ``PatchClosureInstr`` instances and
``MakeClosureInstr`` are updated to match.  When ``needs_patching`` becomes
``False`` and ``captures`` becomes empty, the bytecode builder will emit
``LOAD_CONST`` (a pre-built function) instead of ``MAKE_CLOSURE``, eliminating
the closure object and capture slot entirely.

The pass must run last in the CFG pass list so that it sees the final state
after all other optimizations (branch constant propagation, block
simplification, type propagation with guard insertion and hoisting).
"""


from menai.cfg.menai_cfg import (
    MenaiCFGInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGMakeClosureInstr,
    MenaiCFGPatchClosureInstr,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGOptimizationPass


class MenaiCFGDeadCaptures(MenaiCFGOptimizationPass):
    """
    Eliminate dead captures from CFG functions.

    For each ``MakeClosureInstr``, scans the child function for
    ``FreeVarInstr`` instances whose result is never referenced.  Removes them
    and updates the parent's closure-creation instructions to match.
    """

    def _optimize_function(self, func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
        changed = False

        for block in func.blocks:
            # Build the new instruction list, processing MakeClosureInstrs
            # and filtering/renumbering PatchClosureInstrs in a single pass.
            new_instrs: list[MenaiCFGInstr] = []
            # Track dead capture indices per closure, accumulated as we
            # encounter MakeClosureInstrs.  PatchClosureInstrs come after
            # their corresponding MakeClosureInstr in the same block.
            dead_patches: dict[int, set[int]] = {}
            old_to_new_map: dict[int, dict[int, int]] = {}

            for instr in block.instrs:
                if isinstance(instr, MenaiCFGMakeClosureInstr):
                    new_mc, did_change, dead_indices, o2n = self._process_closure(instr)
                    if did_change:
                        changed = True
                        dead_patches[instr.result.id] = dead_indices
                        old_to_new_map[instr.result.id] = o2n

                    new_instrs.append(new_mc)

                elif isinstance(instr, MenaiCFGPatchClosureInstr):
                    closure_id = instr.closure.id
                    if closure_id not in dead_patches:
                        new_instrs.append(instr)
                        continue

                    dead_indices = dead_patches[closure_id]
                    if instr.capture_index in dead_indices:
                        continue

                    o2n = old_to_new_map[closure_id]
                    new_idx = o2n[instr.capture_index]
                    new_instrs.append(MenaiCFGPatchClosureInstr(
                        closure=instr.closure,
                        capture_index=new_idx,
                        value=instr.value,
                    ))

                else:
                    new_instrs.append(instr)

            block.instrs = new_instrs

        return func, changed

    def _process_closure(
        self, mc: MenaiCFGMakeClosureInstr,
    ) -> tuple[MenaiCFGMakeClosureInstr, bool, set[int], dict[int, int]]:
        """
        Check whether the child function of *mc* has dead captures.

        Returns a tuple of (new_mc, changed, dead_indices, old_to_new).
        dead_indices is the set of old free var indices that were removed.
        old_to_new maps old free var indices to new indices for survivors.
        """
        child = mc.function

        # Collect all value IDs referenced anywhere in the child function.
        used_ids: set[int] = set()
        for child_block in child.blocks:
            for child_instr in child_block.instrs:
                used_ids.update(value_ids_in_instr(child_instr))

            if child_block.terminator is not None:
                used_ids.update(value_ids_in_term(child_block.terminator))

        # Find dead FreeVarInstrs (those whose result is never referenced).
        dead_indices: set[int] = set()
        for child_instr in child.blocks[0].instrs:
            if isinstance(child_instr, MenaiCFGFreeVarInstr):
                if child_instr.result.id not in used_ids:
                    dead_indices.add(child_instr.index)

        if not dead_indices:
            return mc, False, set(), {}

        # Build old-to-new index mapping for surviving free vars.
        old_to_new: dict[int, int] = {}
        new_index = 0
        for i in range(len(child.free_vars)):
            if i not in dead_indices:
                old_to_new[i] = new_index
                new_index += 1

        # Update child: rebuild entry-block instrs, removing dead FreeVarInstrs
        # and renumbering survivors.  Update free_vars list.
        new_entry_instrs: list[MenaiCFGInstr] = []
        for child_instr in child.blocks[0].instrs:
            if not isinstance(child_instr, MenaiCFGFreeVarInstr):
                new_entry_instrs.append(child_instr)
                continue

            if child_instr.index in dead_indices:
                continue

            new_idx = old_to_new[child_instr.index]
            new_entry_instrs.append(
                MenaiCFGFreeVarInstr(
                    result=child_instr.result,
                    index=new_idx,
                    var_name=child_instr.var_name,
                ),
            )

        child.blocks[0].instrs = new_entry_instrs
        child.free_vars = [
            name for i, name in enumerate(child.free_vars)
            if i not in dead_indices
        ]

        # Update MakeClosureInstr: remove dead outer captures.
        # Outer captures start at index (len(old_free_vars) - len(captures)).
        old_total = len(child.free_vars) + len(dead_indices)
        outer_start = old_total - len(mc.captures)
        new_captures = []
        for i, cap in enumerate(mc.captures):
            old_index = outer_start + i
            if old_index in dead_indices:
                continue

            new_captures.append(cap)

        # Update needs_patching: if no dead_indices are sibling captures
        # (i.e. in range 0..outer_start-1), needs_patching stays True.
        # If all sibling captures are dead, needs_patching becomes False.
        has_live_sibling = any(i not in dead_indices for i in range(outer_start))

        new_mc = MenaiCFGMakeClosureInstr(
            result=mc.result,
            function=child,
            captures=new_captures,
            needs_patching=has_live_sibling,
        )

        return new_mc, True, dead_indices, old_to_new
