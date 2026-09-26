"""
Slot allocator for the Menai VM backend.

Takes a MenaiVCodeFunction with virtual registers (MenaiVCodeReg) and produces
a SlotMap — a mapping from register id to slot index — that the bytecode emitter
uses to assign concrete local variable slots.

Algorithm
---------
Params are pre-assigned to slots 0..P-1 and free vars to slots P..P+F-1 and
those slots are never reused — they remain live for the entire function body.
A linear scan is used for all other registers:

1. Scan the flat instruction list to compute per-definition lifetimes.  A
   register id can be defined multiple times in the VCode (e.g. a phi-elimination
   move source that is redefined in each match arm).  Each definition has its
   own independent lifetime: from the definition index to the last use before
   the next redefinition of the same register id (or end of list).

2. Walk the instruction list forward.  At each definition point assign the
   lowest slot not currently occupied by a live register.  When a register is
   redefined, its previous definition is killed first, freeing its slot for
   reuse.  At each use point, if this is the last use of the current
   definition, free its slot so it can be reused by a subsequent definition.

Because VCode is phi-free and already linearised in RPO order, this linear
scan produces correct results without full dataflow liveness analysis.
Registers defined before a forward jump are still live at the jump target if
they are used there, and the per-definition lifetime scan captures this
correctly by finding the actual last use index in the flat list regardless of
labels.

MenaiVCodeMove instructions where src and dst are assigned the same slot
are no-ops and will be eliminated by the peephole pass.

Call argument registers and make-* instruction element registers (make-list,
make-set, make-struct, make-dict) whose last use is the consuming instruction
itself, and whose definition has no intervening barrier, are reassigned
directly to the outgoing zone (local_count + outgoing_offset) in Phase 3.

Self-loop argument registers whose last use is the self-loop move itself,
and whose definition has no intervening barrier, are reassigned directly
to the target param slot (arg_index) in Phase 3b, eliminating the MOVE.

Param/free-var register ids
----------------------------
The CFG builder assigns SSA value ids to params and free vars first, in
order: params get ids 0..P-1, free vars get ids P..P+F-1.  The VCode
builder preserves these ids directly (MenaiCFGValue.id → MenaiVCodeReg.id).
The allocator relies on this invariant to pre-assign fixed slots to those
register ids without needing an explicit mapping.
"""

from dataclasses import dataclass

from menai.vcode.menai_vcode import (
    MenaiVCodeApply,
    MenaiVCodeBuiltin,
    MenaiVCodeCall,
    MenaiVCodeFunction,
    MenaiVCodeInstr,
    MenaiVCodeJump,
    MenaiVCodeJumpIfFalse,
    MenaiVCodeJumpIfTrue,
    MenaiVCodeLabel,
    MenaiVCodeLoadConst,
    MenaiVCodeMakeClosure,
    MenaiVCodeMove,
    MenaiVCodePatchClosure,
    MenaiVCodeMakeStruct,
    MenaiVCodeMakeList,
    MenaiVCodeMakeVector,
    MenaiVCodeMakeSet,
    MenaiVCodeMakeDict,
    MenaiVCodeStructGetIndexed,
    MenaiVCodeStructSetIndexed,
    MenaiVCodeReg,
    MenaiVCodeReturn,
    MenaiVCodeRaise,
    MenaiVCodeSwitch,
    MenaiVCodeTailApply,
    MenaiVCodeTailCall,
)


@dataclass
class SlotMap:
    """Result of slot allocation for one MenaiVCodeFunction."""
    slots: dict[int, int]   # register id → slot index
    slot_count: int         # total slots needed (= local_count in CodeObject)
    local_count: int = 0    # scratch + fixed slots before the outgoing zone
                            # (= slot_count when there are no outgoing slots)

    def slot_of(self, reg: MenaiVCodeReg) -> int:
        """Return the slot assigned to reg.  Asserts it exists."""
        assert reg.id in self.slots, (
            f"SlotMap: register {reg} has no assigned slot"
        )
        return self.slots[reg.id]


def allocate_slots(func: MenaiVCodeFunction) -> SlotMap:
    """
    Assign a slot index to every virtual register in func.

    Args:
        func: A MenaiVCodeFunction with virtual registers.

    Returns:
        A SlotMap mapping every register id to a slot index.
    """

    slots: dict[int, int] = {}
    next_new_slot = 0

    # Phase 1: scan the flat instruction list to compute per-definition
    # lifetimes.  A register id can be defined multiple times in the VCode
    # (e.g. a phi-elimination move source that is redefined in each match arm).
    # Each definition has its own independent lifetime: from the definition
    # index to the last use before the next redefinition of the same register
    # id (or end of list).
    #
    # We build:
    #   def_last_use: def_idx → last_use_idx for that definition
    #   reg_defs:     reg_id → sorted list of definition indices
    #
    # Phase 2 uses def_last_use via a current_def map that tracks which
    # definition is active for each register id as the scan progresses.
    # Phases 3 and 3b use reg_defs to find the active definition at a given
    # use index (the most recent def of the same reg_id at or before that
    # index), then check def_last_use for that definition.
    def_last_use: dict[int, int] = {}
    reg_defs: dict[int, list[int]] = {}

    for idx, instr in enumerate(func.instrs):
        defs, uses = _defs_uses(instr)
        for reg_id in defs:
            reg_defs.setdefault(reg_id, []).append(idx)

    # For each definition, find its last use: the last use of the same
    # register id at an index after this definition and before the next
    # definition of the same register id (or end of list).
    for reg_id, def_indices in reg_defs.items():
        for i, d in enumerate(def_indices):
            next_def = def_indices[i + 1] if i + 1 < len(def_indices) else len(func.instrs)
            last = d  # a definition with no uses dies immediately
            for scan_idx in range(d + 1, next_def):
                scan_instr = func.instrs[scan_idx]
                _, scan_uses = _defs_uses(scan_instr)
                if reg_id in scan_uses:
                    last = scan_idx

            def_last_use[d] = last

    # Pre-assign fixed slots for params and free vars.
    # The register IDs come from the VCode builder, which records them
    # explicitly from the CFG entry block's ParamInstr and FreeVarInstr.
    fixed_reg_ids: list[int] = (
        list(func.param_reg_ids) + list(func.free_var_reg_ids)
    )

    # Hoisted registers are permanently live (they survive across the
    # self-loop back-edge) but are not pre-assigned slots — the linear
    # scan allocates them when their defining instruction is reached.
    fixed_reg_id_set: set[int] = set(fixed_reg_ids) | set(func.hoisted_reg_ids)
    for slot_idx, reg_id in enumerate(fixed_reg_ids):
        slots[reg_id] = slot_idx

    # Phase 2: linear scan allocation for all other registers.  Fixed slots
    # (params and free vars) are permanently live and never released for reuse.
    live: set[int] = set(fixed_reg_ids)
    current_def: dict[int, int] = {}

    def _free_slot() -> int:
        """Return the lowest slot index not currently occupied by a live register."""
        nonlocal next_new_slot
        occupied = {slots[rid] for rid in live if rid in slots}
        slot = 0
        while slot in occupied:
            slot += 1

        if slot >= next_new_slot:
            next_new_slot = slot + 1

        return slot

    def _kill_if_dead(reg_id: int, current_idx: int) -> None:
        """
        Remove reg_id from the live set if its current definition's last
        use is at or before current_idx.
        """
        if reg_id in fixed_reg_id_set:
            return

        d = current_def.get(reg_id)
        if d is not None and def_last_use.get(d, d) <= current_idx:
            live.discard(reg_id)

    for idx, instr in enumerate(func.instrs):
        if isinstance(instr, MenaiVCodeLabel):
            continue

        defs, uses = _defs_uses(instr)

        # Kill any register being redefined — its previous definition's
        # lifetime ends here, freeing its slot for reuse.
        for reg_id in defs:
            if reg_id not in fixed_reg_id_set and reg_id in live:
                live.discard(reg_id)

        # MenaiVCodeMakeClosure: allocate the result first, then kill dead
        # inputs.  The bytecode emitter reads captures after writing the
        # closure slot (via PATCH_CLOSURE), so the closure slot must not
        # overlap with any capture register.
        if isinstance(instr, MenaiVCodeMakeClosure):
            dst_id = instr.dst.id
            if dst_id not in slots:
                slots[dst_id] = _free_slot()

            live.add(dst_id)
            current_def[dst_id] = idx
            for reg_id in uses:
                _kill_if_dead(reg_id, idx)

            _kill_if_dead(dst_id, idx)
            continue

        # All other instructions: kill dead inputs first so the result can
        # reuse their slots, then allocate the result.
        for reg_id in uses:
            _kill_if_dead(reg_id, idx)

        for reg_id in defs:
            if reg_id not in slots:
                slots[reg_id] = _free_slot()

            live.add(reg_id)
            current_def[reg_id] = idx

        for reg_id in defs:
            _kill_if_dead(reg_id, idx)

    # Ensure slot_count covers all assigned slots.
    slot_count = next_new_slot
    for s in slots.values():
        if s >= slot_count:
            slot_count = s + 1

    local_count = slot_count

    # Phase 3: back-propagate outgoing slot assignments.  For each argument of
    # a call, tail-call, or make-* instruction (make-list, make-set, make-struct,
    # make-dict) that meets all safety conditions, reassign its scratch slot to
    # local_count + outgoing_offset so the bytecode emitter needs no MOVE.
    #
    # The make-* instructions use the same outgoing-zone staging convention as
    # calls: the bytecode emitter moves each argument into local_count + offset
    # before emitting the opcode.  Back-propagating the slot assignment eliminates
    # those MOVEs, just as it does for call arguments.
    #
    # Safety conditions:
    #   1. Not a fixed register (param or free var) — those have fixed slots.
    #   2. The consuming instruction is the last use of the register's current
    #      definition — the outgoing zone is clobbered when the instruction
    #      executes, so no later read is safe.
    #   3. No call/apply/make barrier between the register's definition and
    #      this instruction — a prior call, apply, or make-* would have
    #      already written local_count + outgoing_offset.
    #      For call/apply result registers the defining call itself is not a
    #      barrier — the scan starts strictly after the definition index.
    #   4. No use of the register anywhere in its current definition's lifetime
    #      requires a local slot.  Branch conditions, switch scrutinees, call
    #      function registers, apply arg-list registers, and raise messages are
    #      validated against local_count, so a register used in any of those
    #      positions must not be moved into the outgoing zone even when its
    #      final use is a call argument.  The outgoing zone is transient
    #      argument-staging space; a value read as persistent state must live
    #      in the local region.
    #
    #   Closure and capture registers are NOT excluded: PATCH_CLOSURE reads its
    #   operands from whatever slot they are assigned to, including outgoing
    #   zone slots.  The barrier check (condition 3) ensures no call or make-*
    #   clobbers the outgoing zone between the register's definition and the
    #   consuming instruction, which covers any PATCH_CLOSURE in that range.

    # Barrier types: any instruction that writes into the outgoing zone and
    # therefore clobbers slots local_count..local_count+N.
    barrier_types = (
        MenaiVCodeCall, MenaiVCodeApply,
        MenaiVCodeTailCall, MenaiVCodeTailApply,
        MenaiVCodeMakeStruct, MenaiVCodeMakeList,
        MenaiVCodeMakeVector,
        MenaiVCodeMakeSet, MenaiVCodeMakeDict,
        MenaiVCodeStructGetIndexed, MenaiVCodeStructSetIndexed,
    )

    # Consuming types: instructions whose arguments are staged into the
    # outgoing zone by the bytecode emitter and are therefore eligible for
    # back-propagation.
    consuming_types = (
        MenaiVCodeCall, MenaiVCodeTailCall,
        MenaiVCodeMakeList, MenaiVCodeMakeSet,
        MenaiVCodeMakeVector,
        MenaiVCodeMakeStruct, MenaiVCodeMakeDict,
    )

    max_outgoing_index = -1
    for instr_idx, instr in enumerate(func.instrs):
        if not isinstance(instr, consuming_types):
            continue

        for arg, outgoing_offset in _outgoing_args(instr):
            reg_id = arg.id

            if reg_id in fixed_reg_id_set:
                continue

            reg_def = _active_def(reg_defs, reg_id, instr_idx)
            if reg_def is None or def_last_use.get(reg_def, reg_def) != instr_idx:
                continue

            barrier = False
            # Condition 4: scan for a barrier between def and use.
            for scan_idx in range(reg_def + 1, instr_idx):
                scan_instr = func.instrs[scan_idx]
                if isinstance(scan_instr, barrier_types):
                    barrier = True
                    break

            if barrier:
                continue

            # Condition 4: the register must not be used in any position that
            # requires a local slot anywhere in its current definition's
            # lifetime.  Such a use reads the register as persistent state,
            # which the outgoing zone cannot provide.
            local_required = False
            for scan_idx in range(reg_def, instr_idx + 1):
                scan_instr = func.instrs[scan_idx]
                if _use_requires_local_slot(scan_instr, reg_id):
                    local_required = True
                    break

            if local_required:
                continue

            slots[reg_id] = local_count + outgoing_offset
            max_outgoing_index = max(max_outgoing_index, outgoing_offset)

    if max_outgoing_index >= 0:
        slot_count = local_count + max_outgoing_index + 1

    # Phase 3b: back-propagate param slot assignments for self-loop moves.
    # A self-loop is emitted as a group of MenaiVCodeMove instructions
    # (one per param) immediately followed by a self-loop jump.  For each move
    # whose source register meets the same safety conditions as Phase 3, we
    # reassign the source register's slot directly to the target param slot
    # so the definition writes straight into the param slot and the MOVE
    # becomes a same-slot no-op, eliminated by the peephole pass.
    #
    # Safety conditions (parallel to Phase 3):
    #   1. Not a fixed register (param or free var).
    #   2. The self-loop move is the last use of the register's current definition.
    #   3. No call or apply between the register's definition and this move.
    #   4. No instruction between the definition and this move reads from param_slot.
    for jump_idx, instr in enumerate(func.instrs):
        if not isinstance(instr, MenaiVCodeJump) or not instr.is_self_loop:
            continue

        # Collect the contiguous MOVE group immediately before this JUMP.
        move_start = jump_idx - 1
        while move_start >= 0 and isinstance(func.instrs[move_start], MenaiVCodeMove):
            move_start -= 1

        move_start += 1

        for move_idx in range(move_start, jump_idx):
            move = func.instrs[move_idx]
            assert isinstance(move, MenaiVCodeMove)
            reg_id = move.src.id
            param_slot = slots[move.dst.id]

            if reg_id in fixed_reg_id_set:
                continue

            reg_def = _active_def(reg_defs, reg_id, move_idx)
            if reg_def is None or def_last_use.get(reg_def, reg_def) != move_idx:
                continue

            barrier = False
            for scan_idx in range(reg_def + 1, move_idx):
                scan_instr = func.instrs[scan_idx]
                if isinstance(scan_instr, barrier_types):
                    barrier = True
                    break

                _, scan_uses = _defs_uses(scan_instr)
                if any(slots.get(u, -1) == param_slot for u in scan_uses):
                    barrier = True
                    break

            if barrier:
                continue

            slots[reg_id] = param_slot

    # Phase 3c: back-propagate param slot assignments across call barriers.
    # Phase 3b treats any call/apply as a barrier because those instructions
    # clobber the outgoing zone.  A param slot, however, is never part of the
    # outgoing zone (which lives at local_count + offset, strictly above every
    # param, capture, and local slot), so a call cannot read or write the
    # caller's param slot.  The only way a param slot's value can be observed
    # between a temp's definition and the back-edge move is by an instruction
    # in this same function that reads it — which is exactly what condition 4
    # below checks.
    #
    # This lets a loop-carried temp whose definition is separated from the
    # back-edge move by a call (e.g. a predicate call in the loop body) still
    # be written straight into the param slot, eliminating the temp and the
    # move.  Example: filter-vector's loop computes `i - 1`, calls the
    # predicate, then moves the result back into `i`.
    #
    # Safety conditions:
    #   1. Not a fixed register (param or free var).
    #   2. The self-loop move is the last use of the register's current definition.
    #   3. No instruction between the definition and this move reads from
    #      param_slot (condition 4 of Phase 3b; the barrier check is dropped).
    for jump_idx, instr in enumerate(func.instrs):
        if not isinstance(instr, MenaiVCodeJump) or not instr.is_self_loop:
            continue

        move_start = jump_idx - 1
        while move_start >= 0 and isinstance(func.instrs[move_start], MenaiVCodeMove):
            move_start -= 1

        move_start += 1

        for move_idx in range(move_start, jump_idx):
            move = func.instrs[move_idx]
            assert isinstance(move, MenaiVCodeMove)
            reg_id = move.src.id
            param_slot = slots[move.dst.id]

            if reg_id in fixed_reg_id_set:
                continue

            if param_slot >= len(func.params):
                continue

            reg_def = _active_def(reg_defs, reg_id, move_idx)
            if reg_def is None or def_last_use.get(reg_def, reg_def) != move_idx:
                continue

            reads_param = False
            for scan_idx in range(reg_def + 1, move_idx):
                scan_instr = func.instrs[scan_idx]
                _, scan_uses = _defs_uses(scan_instr)
                if any(slots.get(u, -1) == param_slot for u in scan_uses):
                    reads_param = True
                    break

            if reads_param:
                continue

            slots[reg_id] = param_slot

    # Phase 4: coalesce single-use sources of phi-elimination moves.
    # Phi elimination emits one move per phi arm, copying the arm's incoming
    # value into the phi result register.  When an arm's incoming value is
    # produced solely to feed that arm (single definition, single use), the
    # producing instruction can write the phi result's slot directly, making
    # the move a same-slot no-op.
    #
    # This is what removes the residual move in a loop like filter-vector's,
    # where one phi arm feeds the phi from a computation (`list-prepend`) and
    # the phi result is itself coalesced into the loop parameter by Phase 3b.
    #
    # Safety conditions (parallel to Phase 3b):
    #   1. Not a fixed register (param or free var).
    #   2. The phi move is the last use of the register's current definition.
    #   3. No call/apply/make barrier between the definition and this move.
    #   4. No instruction between the definition and this move reads from the
    #      phi result's slot.
    for move_idx, move in enumerate(func.instrs):
        if not isinstance(move, MenaiVCodeMove) or not move.is_phi_move:
            continue

        reg_id = move.src.id
        dst_slot = slots.get(move.dst.id)

        if dst_slot is None:
            continue

        if reg_id in fixed_reg_id_set:
            continue

        reg_def = _active_def(reg_defs, reg_id, move_idx)
        if reg_def is None or def_last_use.get(reg_def, reg_def) != move_idx:
            continue

        barrier = False
        for scan_idx in range(reg_def + 1, move_idx):
            scan_instr = func.instrs[scan_idx]
            if isinstance(scan_instr, barrier_types):
                barrier = True
                break

            _, scan_uses = _defs_uses(scan_instr)
            if any(slots.get(u, -1) == dst_slot for u in scan_uses):
                barrier = True
                break

        if barrier:
            continue

        slots[reg_id] = dst_slot

    return SlotMap(slots=slots, slot_count=slot_count, local_count=local_count)


def _defs_uses(instr: MenaiVCodeInstr) -> tuple[list[int], list[int]]:
    """
    Return (defs, uses) — lists of register ids defined and used by instr.

    Labels define and use nothing.
    Instructions with a dst define that register.
    Jump instructions use their condition register (if any).
    """
    if isinstance(instr, MenaiVCodeLabel):
        return [], []

    if isinstance(instr, MenaiVCodeMove):
        return [instr.dst.id], [instr.src.id]

    if isinstance(instr, MenaiVCodeLoadConst):
        return [instr.dst.id], []

    if isinstance(instr, MenaiVCodeBuiltin):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeCall):
        return [instr.dst.id], [instr.func.id] + [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeTailCall):
        return [], [instr.func.id] + [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeApply):
        return [instr.dst.id], [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiVCodeTailApply):
        return [], [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiVCodeMakeClosure):
        return [instr.dst.id], [r.id for r in instr.captures]

    if isinstance(instr, MenaiVCodePatchClosure):
        return [], [instr.closure.id, instr.value.id]

    if isinstance(instr, MenaiVCodeMakeStruct):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeList):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeVector):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeSet):
        return [instr.dst.id], [r.id for r in instr.args]

    if isinstance(instr, MenaiVCodeMakeDict):
        return [instr.dst.id], [r.id for k, v in instr.pairs for r in (k, v)]

    if isinstance(instr, MenaiVCodeStructGetIndexed):
        return [instr.dst.id], [instr.struct.id]

    if isinstance(instr, MenaiVCodeStructSetIndexed):
        return [instr.dst.id], [instr.struct.id, instr.value.id]

    if isinstance(instr, MenaiVCodeJumpIfTrue):
        return [], [instr.cond.id]

    if isinstance(instr, MenaiVCodeJumpIfFalse):
        return [], [instr.cond.id]

    if isinstance(instr, MenaiVCodeSwitch):
        return [], [instr.src.id]

    if isinstance(instr, MenaiVCodeReturn):
        return [], [instr.value.id]

    # MenaiVCodeJump, MenaiVCodeRaise: no register references.
    return [], []


def _active_def(
    reg_defs: dict[int, list[int]],
    reg_id: int,
    use_idx: int,
) -> int | None:
    """
    Return the definition index of reg_id that is active at use_idx.

    The active definition is the most recent definition of reg_id at or
    before use_idx.  reg_defs[reg_id] is a sorted list of definition indices.
    Returns None if reg_id has no definition before use_idx.
    """
    defs = reg_defs.get(reg_id)
    if not defs:
        return None

    # Binary search for the largest def index <= use_idx.
    lo, hi = 0, len(defs) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if defs[mid] <= use_idx:
            lo = mid

        else:
            hi = mid - 1

    return defs[lo] if defs[lo] <= use_idx else None


def _use_requires_local_slot(instr: MenaiVCodeInstr, reg_id: int) -> bool:
    """
    Return True if instr reads reg_id from a position that the bytecode
    validator requires to be a local slot (index < local_count).

    The outgoing zone (slots local_count..local_count+N-1) is transient
    argument-staging space.  Instructions that read a register as persistent
    state rather than as a staged argument must therefore have that register
    in the local region:

      - JUMP_IF_TRUE / JUMP_IF_FALSE: condition register
      - SWITCH: scrutinee register
      - CALL / TAIL_CALL: function register
      - APPLY / TAIL_APPLY: function register and arg-list register
      - RAISE_ERROR: message register

    A register used in any of these positions cannot be back-propagated into
    the outgoing zone, even when its final use is a call argument.
    """
    if isinstance(instr, (MenaiVCodeJumpIfTrue, MenaiVCodeJumpIfFalse)):
        return instr.cond.id == reg_id

    if isinstance(instr, MenaiVCodeSwitch):
        return instr.src.id == reg_id

    if isinstance(instr, (MenaiVCodeCall, MenaiVCodeTailCall)):
        return instr.func.id == reg_id

    if isinstance(instr, (MenaiVCodeApply, MenaiVCodeTailApply)):
        return reg_id in (instr.func.id, instr.arg_list.id)

    if isinstance(instr, MenaiVCodeRaise):
        return instr.message.id == reg_id

    return False


def _outgoing_args(instr: MenaiVCodeInstr) -> list[tuple[MenaiVCodeReg, int]]:
    """
    Return (register, outgoing_offset) pairs for instructions that stage
    arguments into the outgoing zone.

    The outgoing_offset is the index relative to local_count where the
    bytecode emitter will place the value.  This mirrors the staging logic
    in menai_bytecode_builder._emit_vcode:

      Call / TailCall:  args[j]           -> local_count + j
      MakeList / Vector / Set: args[j]    -> local_count + j
      MakeStruct:       args[j]           -> local_count + 1 + j  (slot 0 = type)
      MakeDict:         pairs[j] = (k,v)  -> local_count + j*2, local_count + j*2 + 1
    """
    if isinstance(instr, (MenaiVCodeCall, MenaiVCodeTailCall, MenaiVCodeMakeList, MenaiVCodeMakeVector, MenaiVCodeMakeSet)):
        return [(arg, j) for j, arg in enumerate(instr.args)]

    if isinstance(instr, MenaiVCodeMakeStruct):
        return [(arg, j + 1) for j, arg in enumerate(instr.args)]

    if isinstance(instr, MenaiVCodeMakeDict):
        return [(reg, j * 2 + offset)
                for j, (k, v) in enumerate(instr.pairs)
                for offset, reg in enumerate((k, v))]

    return []
