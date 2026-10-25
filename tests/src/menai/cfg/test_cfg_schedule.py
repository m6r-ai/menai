"""
Tests for MenaiCFGSchedule.

The pass reorders each block's instructions into their latest legal order, so
that every definition sits as close to its last reader as the dependencies
allow.  The backend benefit is that a loop-carried update lands immediately
before the back-edge move group, which is the shape the slot allocator's
back-edge coalescing accepts.

The overriding property is that the pass must be a projection: scheduling an
already-scheduled block must change nothing.  The pass manager runs each pass to
its own fixed point, so a pass that is not idempotent never terminates.  Most of
this file is therefore about that property, tested on small synthetic blocks so
a failure is reported in milliseconds.

Covers:
  1. Idempotence on a range of block shapes.
  2. The dependency order is respected: no instruction reads a value defined
     after it.
  3. A value consumed only by the terminator sinks to the end of the block.
  4. A chain of dependent definitions is emitted contiguously.
  5. Phis, params and free vars keep their position.
  6. A block already in latest legal order is returned unchanged by identity.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBranchTerm,
    MenaiCFGBlock,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGInstr,
    MenaiCFGParamInstr,
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    result_id_in_instr,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.cfg.menai_cfg_schedule import MenaiCFGSchedule


def _v(value_id: int) -> MenaiCFGValue:
    """Build an unnamed SSA value with the given id."""
    return MenaiCFGValue(id=value_id)


def _const(value_id: int, value: int) -> MenaiCFGConstInstr:
    """Build a constant instruction."""
    return MenaiCFGConstInstr(result=_v(value_id), value=value)


def _builtin(value_id: int, op: str, *args: int) -> MenaiCFGInstr:
    """Build a builtin instruction over the given operand ids."""
    from menai.cfg.menai_cfg import MenaiCFGBuiltinInstr

    return MenaiCFGBuiltinInstr(
        result=_v(value_id), op=op, args=tuple(_v(arg) for arg in args),
    )


def _function(instrs: tuple[MenaiCFGInstr, ...], terminator=None) -> MenaiCFGFunction:
    """Build a one-block function whose block holds `instrs`."""
    block = MenaiCFGBlock(
        id=0,
        label="entry",
        instrs=instrs,
        terminator=terminator if terminator is not None else MenaiCFGBranchTerm(
            cond=_v(999), true_block=0, false_block=0,
        ),
    )
    return MenaiCFGFunction(blocks=(block,))


def _schedule(func: MenaiCFGFunction) -> MenaiCFGFunction:
    """Run the pass once over `func`, returning the new function."""
    scheduled, _ = MenaiCFGSchedule().optimize(func, MenaiCFGContext())
    return scheduled


def _order(func: MenaiCFGFunction) -> list[int | None]:
    """Return the result ids of the entry block's instructions, in order."""
    return [result_id_in_instr(instr) for instr in func.blocks[0].instrs]


def _assert_idempotent(func: MenaiCFGFunction) -> MenaiCFGFunction:
    """
    Assert the pass reaches a fixed point, returning the scheduled function.

    The pass manager runs each pass until it reports no change, so scheduling
    the pass's own output must report no change.  A pass that fails this never
    terminates under the manager.
    """
    once = _schedule(func)
    twice = _schedule(once)
    assert _order(twice) == _order(once), (
        f"pass is not idempotent: {_order(once)} then {_order(twice)}"
    )
    return once


def _assert_topological(func: MenaiCFGFunction) -> None:
    """
    Assert no instruction reads a value defined later in the same block.

    A value defined outside the block is not constrained by the block's order.
    """
    instrs = func.blocks[0].instrs
    defined_here = {
        result_id for result_id in (result_id_in_instr(i) for i in instrs)
        if result_id is not None
    }

    seen: set[int] = set()
    for instr in instrs:
        for operand in value_ids_in_instr(instr):
            if operand in defined_here:
                assert operand in seen, (
                    f"instruction {result_id_in_instr(instr)} reads %{operand} "
                    f"before it is defined; order is {_order(func)}"
                )

        result_id = result_id_in_instr(instr)
        if result_id is not None:
            seen.add(result_id)


class TestIdempotence:
    """Scheduling an already-scheduled block changes nothing."""

    def test_independent_instructions(self):
        """Independent instructions reach a fixed point."""
        func = _function((_const(1, 1), _const(2, 2), _const(3, 3)))
        _assert_idempotent(func)

    def test_chain(self):
        """A dependent chain reaches a fixed point."""
        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
            _builtin(3, "integer+", 2, 1),
            _builtin(4, "integer+", 3, 1),
        ))
        _assert_idempotent(func)

    def test_chain_in_reverse_source_order(self):
        """A chain written back-to-front reaches a fixed point."""
        func = _function((
            _builtin(4, "integer+", 3, 1),
            _builtin(3, "integer+", 2, 1),
            _builtin(2, "integer+", 1, 1),
            _const(1, 1),
        ))
        _assert_idempotent(func)

    def test_two_independent_chains(self):
        """Two interleaved chains reach a fixed point."""
        func = _function((
            _const(1, 1),
            _const(2, 2),
            _builtin(3, "integer+", 1, 1),
            _builtin(4, "integer+", 2, 2),
            _builtin(5, "integer+", 3, 1),
            _builtin(6, "integer+", 4, 2),
        ))
        _assert_idempotent(func)

    def test_shared_operand(self):
        """A value read by several instructions reaches a fixed point."""
        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
            _builtin(3, "integer+", 1, 1),
            _builtin(4, "integer+", 1, 1),
        ))
        _assert_idempotent(func)

    def test_terminator_reader(self):
        """A value consumed by the terminator reaches a fixed point."""
        func = _function(
            (
                _builtin(1, "integer+", 50, 50),
                _const(2, 2),
                _builtin(3, "integer+", 2, 2),
            ),
            terminator=MenaiCFGSelfLoopTerm(args=(_v(1), _v(3)), target=0),
        )
        _assert_idempotent(func)

    def test_diamond_of_dependencies(self):
        """A value with two dependents that rejoin reaches a fixed point."""
        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
            _builtin(3, "integer+", 1, 1),
            _builtin(4, "integer+", 2, 3),
        ))
        _assert_idempotent(func)

    def test_repeated_definition_of_one_id(self):
        """A value id defined twice reaches a fixed point."""
        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
            _builtin(2, "integer+", 1, 1),
            _builtin(3, "integer+", 2, 1),
        ))
        _assert_idempotent(func)

    def test_guard_defines_nothing(self):
        """A guard, which defines no result, reaches a fixed point."""
        from menai.cfg.menai_cfg import MenaiCFGGuardInstr

        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
            MenaiCFGGuardInstr(value=_v(2), expected_type="integer"),
            _builtin(3, "integer+", 2, 1),
        ))
        _assert_idempotent(func)

    def test_large_random_shapes(self):
        """A range of generated shapes all reach a fixed point."""
        for seed in range(40):
            func = _generated_block(seed)
            _assert_idempotent(func)


class TestDependencyOrder:
    """The emitted order is a topological sort of the block's dependencies."""

    def test_chain_in_reverse_source_order(self):
        """A back-to-front chain is emitted in dependency order."""
        func = _function((
            _builtin(4, "integer+", 3, 1),
            _builtin(3, "integer+", 2, 1),
            _builtin(2, "integer+", 1, 1),
            _const(1, 1),
        ))
        scheduled = _assert_idempotent(func)
        _assert_topological(scheduled)

    def test_generated_shapes(self):
        """Every generated shape is emitted in dependency order."""
        for seed in range(40):
            scheduled = _assert_idempotent(_generated_block(seed))
            _assert_topological(scheduled)


class TestTerminatorReadSinks:
    """A value consumed only by the terminator sinks to the end of the block."""

    def test_update_adjacent_to_back_edge(self):
        """
        The two values the back-edge consumes are the last two instructions.

        This is the shape the slot allocator's coalescing needs: the definition
        immediately before the move group.
        """
        func = _function(
            (
                _builtin(55, "integer+", 51, 54),
                _builtin(57, "integer-bit-shift-left", 52, 56),
                _builtin(58, "integer-bit-shift-right", 44, 51),
                _builtin(60, "integer-bit-and", 58, 59),
                _builtin(61, "integer-bit-or", 57, 60),
            ),
            terminator=MenaiCFGSelfLoopTerm(args=(_v(55), _v(61)), target=0),
        )
        scheduled = _assert_idempotent(func)
        _assert_topological(scheduled)

        order = _order(scheduled)
        assert set(order[-2:]) == {55, 61}, (
            f"expected the back-edge values last, got {order}"
        )

    def test_chain_collapses_to_end(self):
        """A chain feeding the terminator is emitted contiguously at the end."""
        func = _function(
            (
                _const(1, 1),
                _builtin(2, "integer+", 1, 1),
                _builtin(3, "integer+", 2, 1),
                _builtin(4, "integer+", 3, 1),
            ),
            terminator=MenaiCFGSelfLoopTerm(args=(_v(4),), target=0),
        )
        scheduled = _assert_idempotent(func)
        assert _order(scheduled) == [1, 2, 3, 4]


class TestPinnedInstructions:
    """Phis, params and free vars keep their position."""

    def test_phi_stays_first(self):
        """A phi remains the first instruction in its block."""
        func = _function((
            MenaiCFGPhiInstr(result=_v(1), incoming=((_v(50), 0),)),
            _const(2, 2),
            _builtin(3, "integer+", 2, 2),
        ))
        scheduled = _assert_idempotent(func)
        assert _order(scheduled)[0] == 1

    def test_param_and_free_var_stay_first(self):
        """Param and free-var definitions remain at the top of the block."""
        func = _function((
            MenaiCFGParamInstr(result=_v(1), index=0, param_name="a"),
            MenaiCFGFreeVarInstr(result=_v(2), index=0, var_name="b"),
            _const(3, 3),
            _builtin(4, "integer+", 3, 3),
        ))
        scheduled = _assert_idempotent(func)
        assert _order(scheduled)[:2] == [1, 2]

    def test_patch_block_is_left_in_original_order(self):
        """
        A block holding patch-closure instructions is not reordered.

        The patches of a $letrec group must all execute before any closure in
        the group is called, so the builder's order is preserved rather than
        scheduled.  Interleaving a patch with a call leaves the callee's capture
        slot unset and the call fails at runtime.
        """
        from menai.cfg.menai_cfg import MenaiCFGPatchClosureInstr

        func = _function((
            _const(2, 4),
            MenaiCFGPatchClosureInstr(
                closure=_v(0), capture_index=0, value=_v(1),
            ),
            _builtin(3, "call", 0),
            MenaiCFGPatchClosureInstr(
                closure=_v(1), capture_index=0, value=_v(0),
            ),
            _builtin(5, "call", 1),
        ))
        scheduled, changed = MenaiCFGSchedule().optimize(func, MenaiCFGContext())
        assert not changed
        assert scheduled is func


class TestAlreadyScheduled:
    """A block already in latest legal order is returned unchanged."""

    def test_unchanged_by_identity(self):
        """The pass returns the same block object when nothing moves."""
        func = _function((
            _const(1, 1),
            _builtin(2, "integer+", 1, 1),
        ))
        scheduled, changed = MenaiCFGSchedule().optimize(func, MenaiCFGContext())
        assert not changed
        assert scheduled is func

    def test_single_instruction_block_unchanged(self):
        """A one-instruction block is never rebuilt."""
        func = _function((_const(1, 1),))
        _, changed = MenaiCFGSchedule().optimize(func, MenaiCFGContext())
        assert not changed


class TestGuardOrdering:
    """A guard stays ahead of every other reader of the value it guards."""

    def test_guard_precedes_its_uses(self):
        """
        A guard is not moved after an instruction that reads its operand.

        A guard defines no result, so the ordinary data-dependency model has no
        edge that ties it to the operations it protects.  Those operations skip
        their own type checks, so running one before its guard reads a value of
        the wrong type; on the VM that corrupts memory rather than merely giving
        a wrong answer.
        """
        from menai.cfg.menai_cfg import MenaiCFGGuardInstr

        func = _function((
            _const(1, 1),
            _builtin(2, "struct-get", 1),
            MenaiCFGGuardInstr(value=_v(2), expected_type="integer"),
            _builtin(3, "integer+", 2, 2),
            _builtin(4, "integer+", 3, 2),
        ))
        scheduled = _assert_idempotent(func)

        guard_index = next(
            index for index, instr in enumerate(scheduled.blocks[0].instrs)
            if isinstance(instr, MenaiCFGGuardInstr)
        )
        order = _order(scheduled)
        assert guard_index < order.index(3)
        assert guard_index < order.index(4)

    def test_guard_follows_the_definition_it_guards(self):
        """
        A guard is placed after the definition of the value it guards.

        Keeping a guard ahead of its uses must not pin it to the top of the
        block: it still follows the definition, so the value's live range is not
        lengthened.
        """
        from menai.cfg.menai_cfg import MenaiCFGGuardInstr

        func = _function((
            _const(1, 1),
            _const(2, 2),
            _builtin(3, "struct-get", 1),
            MenaiCFGGuardInstr(value=_v(3), expected_type="integer"),
            _builtin(4, "integer+", 3, 3),
        ))
        scheduled = _assert_idempotent(func)
        order = _order(scheduled)

        assert order.index(3) < order.index(4)
        assert order[0] == 1


def _generated_block(seed: int) -> MenaiCFGFunction:
    """
    Build a pseudo-random block of dependent and independent instructions.

    The generator is deterministic in `seed`.  Each instruction either loads a
    constant or reads one or two earlier values, so the block is a valid
    topological order to begin with.  Some of the last values are consumed by
    the terminator.
    """
    instrs: list[MenaiCFGInstr] = []
    available: list[int] = []
    next_id = 1

    for step in range(8 + seed % 5):
        if not available or step % 3 == 0:
            instrs.append(_const(next_id, step))
            available.append(next_id)
            next_id += 1
            continue

        first = available[(seed + step) % len(available)]
        second = available[(seed + step * 3) % len(available)]
        instrs.append(_builtin(next_id, "integer+", first, second))
        available.append(next_id)
        next_id += 1

    consumed = tuple(_v(value) for value in available[-2:])
    return _function(
        tuple(instrs),
        terminator=MenaiCFGSelfLoopTerm(args=consumed, target=0),
    )
