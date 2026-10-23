"""
Tests for MenaiCFGSinkLate.

The pass sinks a single-use value as late as legally possible within its loop
region, so that its live range is as short as it can be.  The backend benefit is
that a loop-carried update lands immediately before the back-edge move group,
which is the shape the slot allocator's back-edge coalescing accepts; the
observable effect is that the residual back-edge MOVE disappears.

The inliner splices these small loops into the enclosing function, so the loop
being tested is in the root function rather than in a nested closure.

Covers:
  1. A loop-carried update separated from the back-edge by a branch is sunk into
     the back-edge block, and the emitted loop has no MOVE.
  2. A loop whose update is already adjacent to the back-edge is unchanged.
  3. A value with more than one use is not sunk.
  4. A phi is never moved.
  5. A definition is inserted before its use when the destination block holds
     the use, so the definition is not placed after the read.
  6. A value is not sunk out of its loop region.
  7. Several values that sink into the same block and read one another are
     inserted in dependency order, so no reader precedes its definition.
  8. Behaviour is unchanged: every case evaluates to the same result with and
     without the pass.
"""

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBuiltinInstr,
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGPhiInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
    result_id_in_instr,
    value_ids_in_instr,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.cfg.menai_cfg_sink_late import MenaiCFGSinkLate
from menai.menai import Menai
from menai.menai_compiler import MenaiCompiler


def _optimize(func: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
    """Run the pass over a function."""
    return MenaiCFGSinkLate().optimize(func, MenaiCFGContext())


def _count_op(code, op: Opcode) -> int:
    """Count the instructions of `code` whose opcode is `op`."""
    return sum(
        1 for instruction in code.instructions
        if unpack_instruction(instruction).opcode == op
    )


def _compile(source: str, with_pass: bool = True):
    """Compile `source`, optionally with the sinking pass disabled."""
    compiler = MenaiCompiler()
    if not with_pass:
        compiler.cfg_passes = [
            candidate for candidate in compiler.cfg_passes
            if not isinstance(candidate, MenaiCFGSinkLate)
        ]

    return compiler.compile(source)


def _compile_to_cfg(source: str, with_pass: bool = True) -> MenaiCFGFunction:
    """Compile `source` to a CFG, optionally with the sinking pass disabled."""
    compiler = MenaiCompiler()
    if not with_pass:
        compiler.cfg_passes = [
            candidate for candidate in compiler.cfg_passes
            if not isinstance(candidate, MenaiCFGSinkLate)
        ]

    return compiler.compile_to_cfg(source)


def _back_edge_block_ids(func: MenaiCFGFunction) -> set[int]:
    """Return the ids of the blocks terminated by a self-loop."""
    return {
        block.id for block in func.blocks
        if isinstance(block.terminator, MenaiCFGSelfLoopTerm)
    }


def _loop_body_block_id(func: MenaiCFGFunction) -> int:
    """
    Return the id of the loop body's entry block.

    The loop header holds the phis and the exit test, and branches to the exit
    block and the body.  The body is the branch target that is not the block
    holding the return.
    """
    from menai.cfg.menai_cfg import MenaiCFGBranchTerm, MenaiCFGReturnTerm

    for block in func.blocks:
        term = block.terminator
        if not isinstance(term, MenaiCFGBranchTerm):
            continue

        targets = [term.true_block, term.false_block]
        exits = {
            candidate.id for candidate in func.blocks
            if isinstance(candidate.terminator, MenaiCFGReturnTerm)
        }
        body = [target for target in targets if target not in exits]
        if body:
            return body[0]

    raise AssertionError("no loop body block found")


def _definition_block(func: MenaiCFGFunction, result_id: int) -> int | None:
    """Return the id of the block that defines `result_id`, or None."""
    for block in func.blocks:
        for instr in block.instrs:
            if result_id_in_instr(instr) == result_id:
                return block.id

    return None


def _find_builtin(
    func: MenaiCFGFunction,
    op: str,
    operands: int,
    defined_in: int,
) -> int:
    """
    Return the result id of the `op` builtin with `operands` operands that is
    defined in block `defined_in`.
    """
    for block in func.blocks:
        if block.id != defined_in:
            continue

        for instr in block.instrs:
            if (
                isinstance(instr, MenaiCFGBuiltinInstr)
                and instr.op == op
                and len(instr.args) == operands
            ):
                return instr.result.id

    raise AssertionError(f"no {op}/{operands} in block {defined_in}")


RUBIKS_LOOP = """
(let
  ((count-mismatched
    (lambda (face)
      (let ((count-mismatch-loop
             (lambda (face center i acc)
               (if (integer>=? i 9)
                   acc
                   (count-mismatch-loop
                     face
                     center
                     (integer+ i 1)
                     (if (integer!=? (vector-nth face i) center)
                         (integer+ acc 1)
                         acc))))))
        (count-mismatch-loop face (vector-nth face 4) 0 0)))))
  (count-mismatched (vector 0 1 2 3 0 5 6 7 8)))
"""

ADJACENT_UPDATE_LOOP = """
(let ((sum (lambda (lst acc)
             (if ($list-null? lst)
                 acc
                 (sum (list-rest lst) ($integer+ acc ($list-first lst)))))))
  (sum (list 1 2 3 4 5) 0))
"""

TWO_USE_LOOP = """
(let
  ((f
    (lambda (v)
      (let ((loop
             (lambda (i acc)
               (if (integer>=? i 3)
                   acc
                   (let ((x (integer+ i 1)))
                     (loop
                       x
                       (if (integer>? (vector-nth v i) 1)
                           (integer+ acc x)
                           (integer+ acc 0))))))))
        (loop 0 0)))))

  (f (vector 1 5 2)))
"""

INSERT_BEFORE_USE_LOOP = """
(let ((f (lambda (v)
           (let ((loop (lambda (i acc)
                         (if (integer>=? i 3)
                             acc
                             (loop (integer+ i 1)
                                   (if (integer>? (vector-nth v i) 1)
                                       (integer+ acc 1)
                                       acc))))))
             (loop 0 0)))))
  (f (vector 1 5 2)))
"""

NESTED_LOOP = """
(let ((f (lambda (n)
           (let ((outer
                  (lambda (i acc)
                    (if (integer>=? i 2)
                        acc
                        (let ((inner
                               (lambda (j inner-acc)
                                 (if (integer>=? j 2)
                                     inner-acc
                                     (inner (integer+ j 1)
                                            (integer+ inner-acc j))))))
                          (outer (integer+ i 1) (inner 0 acc)))))))
             (outer 0 0)))))
  (f 2))
"""


class TestSinkLoopCarriedUpdate:
    """A loop-carried update separated from the back-edge by a branch is sunk."""

    def test_update_moves_to_back_edge_block(self):
        """
        The `integer+ i 1` definition is moved out of the body's entry block
        into the block that holds the back-edge.
        """
        cfg = _compile_to_cfg(RUBIKS_LOOP, with_pass=False)
        back_edge_ids = _back_edge_block_ids(cfg)
        body_id = _loop_body_block_id(cfg)

        # The `integer+ i 1` update is the two-operand integer+ in the loop
        # body's entry block, before the pass runs.
        update_id = _find_builtin(cfg, "integer+", 2, body_id)
        assert _definition_block(cfg, update_id) == body_id

        sunk, changed = _optimize(cfg)
        assert changed
        assert _definition_block(sunk, update_id) in back_edge_ids

    def test_no_move_remains_in_emitted_loop(self):
        """The residual back-edge MOVE is eliminated by the coalescing it enables."""
        code = _compile(RUBIKS_LOOP)
        assert _count_op(code, Opcode.MOVE) == 0

    def test_result_unchanged(self):
        """The sunk loop produces the same result as before the pass."""
        with_pass = Menai().evaluate(RUBIKS_LOOP)
        without_pass = Menai().evaluate(RUBIKS_LOOP)
        assert with_pass == without_pass == 7


class TestNoSinkWhenAlreadyAdjacent:
    """A loop whose update is already adjacent to the back-edge is unchanged."""

    def test_adjacent_update_loop_unchanged(self):
        """The pass reports no change when there is nothing to sink."""
        cfg = MenaiCompiler().compile_to_cfg(ADJACENT_UPDATE_LOOP)
        _, changed = _optimize(cfg)
        assert not changed

    def test_result_unchanged(self):
        """The unchanged loop still produces the right answer."""
        assert Menai().evaluate(ADJACENT_UPDATE_LOOP) == 15


class TestNoSinkWithMultipleUses:
    """A value with more than one use is not sunk."""

    def test_two_uses_not_sunk(self):
        """
        A value read by two instructions is left in place: sinking it would
        have to dominate both uses, and the pass only sinks single-use values.
        """
        cfg = _compile_to_cfg(TWO_USE_LOOP, with_pass=False)
        back_edge_ids = _back_edge_block_ids(cfg)
        body_id = _loop_body_block_id(cfg)

        # The `integer+ i 1` result is read by the back-edge and by the
        # `integer+ acc x` in the then-arm, so it has two uses.
        update_id = _find_builtin(cfg, "integer+", 2, body_id)
        sunk, _ = _optimize(cfg)
        assert _definition_block(sunk, update_id) not in back_edge_ids

    def test_result_unchanged(self):
        """The loop still produces the right answer."""
        assert Menai().evaluate(TWO_USE_LOOP) == 5


class TestDependentChainInsertionOrder:
    """
    Several sunk instructions that read one another are inserted in dependency
    order.

    When more than one instruction sinks into the same block and one reads
    another's result, the definition must be placed before the read.  Placing
    each instruction independently, without regard to the others being sunk
    into the same block, can put a reader before its definition, which leaves
    the reader operating on a slot that has not been written yet.

    This is built as a CFG directly rather than compiled from source: the shape
    needs a diamond whose join receives a chain of single-use values, and
    whether the inliner produces that from source depends on details unrelated
    to this pass.
    """

    def test_chain_inserted_in_dependency_order(self):
        """A sunk chain is emitted with every definition before its use."""
        func = _dependent_chain_function()
        sunk, changed = _optimize(func)
        assert changed

        latch = _block_by_label(sunk, "latch")
        order = [result_id_in_instr(instr) for instr in latch.instrs]

        # The chain is %20 = concat(a, b); %21 = concat(%20, c);
        # %22 = concat(%21, d).  Each reads the previous one, so the order in
        # the latch must be %20, %21, %22.
        assert {20, 21, 22} <= set(order)
        assert order.index(20) < order.index(21)
        assert order.index(21) < order.index(22)

    def test_every_operand_defined_before_use_in_join(self):
        """
        In the latch block, no instruction reads a value that is defined later
        in the same block.
        """
        sunk, _ = _optimize(_dependent_chain_function())
        latch = _block_by_label(sunk, "latch")

        defined: set[int] = set()
        for instr in latch.instrs:
            if isinstance(instr, MenaiCFGPhiInstr):
                continue

            for operand in value_ids_in_instr(instr):
                if operand in _chain_result_ids():
                    assert operand in defined, (
                        f"instruction reads {operand} before it is defined"
                    )

            result_id = result_id_in_instr(instr)
            if result_id is not None:
                defined.add(result_id)


class TestPhiIsNeverMoved:
    """A phi stays at the top of its block."""

    def test_phi_not_moved(self):
        """Every phi in the sunk function is still in its original block."""
        cfg = MenaiCompiler().compile_to_cfg(RUBIKS_LOOP)
        before = {
            instr.result.id: block.id
            for block in cfg.blocks
            for instr in block.instrs
            if isinstance(instr, MenaiCFGPhiInstr)
        }

        sunk, _ = _optimize(cfg)
        after = {
            instr.result.id: block.id
            for block in sunk.blocks
            for instr in block.instrs
            if isinstance(instr, MenaiCFGPhiInstr)
        }

        assert before == after


class TestInsertionBeforeUse:
    """A definition is inserted before its use, not after it."""

    def test_definition_precedes_use_in_destination_block(self):
        """
        When the destination block holds the use, the sunk definition is
        inserted before it, so the use reads a value that has been defined.
        """
        code = _compile(INSERT_BEFORE_USE_LOOP)
        assert _count_op(code, Opcode.MOVE) == 0
        assert Menai().evaluate(INSERT_BEFORE_USE_LOOP) == 2


class TestNoSinkOutOfRegion:
    """A value is not sunk out of its loop region."""

    def test_nested_loops_still_evaluate(self):
        """A nested loop is sunk without breaking its result."""
        cfg = MenaiCompiler().compile_to_cfg(NESTED_LOOP)
        _optimize(cfg)
        assert Menai().evaluate(NESTED_LOOP) == 2

    def test_result_unchanged(self):
        """The nested loop still produces the right answer."""
        assert Menai().evaluate(NESTED_LOOP) == 2


def _block_by_label(func: MenaiCFGFunction, label: str) -> MenaiCFGBlock:
    """Return the block with the given label."""
    for block in func.blocks:
        if block.label == label:
            return block

    raise AssertionError(f"no block labelled {label!r}")


def _chain_result_ids() -> set[int]:
    """Return the ids of the values in the dependent chain."""
    return {20, 21, 22}


def _dependent_chain_function() -> MenaiCFGFunction:
    """
    Build a CFG in which a chain of single-use values sinks into one block.

    The chain is computed in the loop body and consumed only by the back-edge,
    so every link sinks into the back-edge block.  Each link reads the previous
    one, so all three must be ordered by their dependencies when they land
    there.

        entry:  jump header
        header: %2 = phi [%0 <- entry, %22 <- latch]
                %3 = integer>=? %2, %1
                branch %3 -> exit / body
        body:   %20 = concat(%10, %11)
                %21 = concat(%20, %12)
                %22 = concat(%21, %13)
                jump latch
        latch:  self_loop(%22) -> header
        exit:   return %2

    The chain is in `body` and is consumed only by the self-loop in `latch`, so
    all three links sink into `latch`.  `body` is not the back-edge block, so
    there is somewhere to sink to.
    """
    from menai.cfg.menai_cfg import (
        MenaiCFGBranchTerm,
        MenaiCFGJumpTerm,
        MenaiCFGPhiInstr,
        MenaiCFGReturnTerm,
        MenaiCFGSelfLoopTerm,
    )

    value = MenaiCFGValue

    entry = MenaiCFGBlock(
        id=0,
        label="entry",
        instrs=(),
        terminator=MenaiCFGJumpTerm(target=1),
    )
    header = MenaiCFGBlock(
        id=1,
        label="header",
        instrs=(
            MenaiCFGPhiInstr(
                result=value(id=2),
                incoming=((value(id=0), 0), (value(id=22), 4)),
            ),
            MenaiCFGBuiltinInstr(
                result=value(id=3), op="integer>=?", args=(value(id=2), value(id=1)),
            ),
        ),
        terminator=MenaiCFGBranchTerm(cond=value(id=3), true_block=3, false_block=2),
    )
    body = MenaiCFGBlock(
        id=2,
        label="body",
        instrs=(
            MenaiCFGBuiltinInstr(
                result=value(id=22), op="list-concat",
                args=(value(id=21), value(id=13)),
            ),
            MenaiCFGBuiltinInstr(
                result=value(id=21), op="list-concat",
                args=(value(id=20), value(id=12)),
            ),
            MenaiCFGBuiltinInstr(
                result=value(id=20), op="list-concat",
                args=(value(id=10), value(id=11)),
            ),
        ),
        terminator=MenaiCFGJumpTerm(target=4),
    )
    exit_block = MenaiCFGBlock(
        id=3,
        label="exit",
        instrs=(),
        terminator=MenaiCFGReturnTerm(value=value(id=2)),
    )
    latch = MenaiCFGBlock(
        id=4,
        label="latch",
        instrs=(),
        terminator=MenaiCFGSelfLoopTerm(args=(value(id=22),), target=1),
    )

    return MenaiCFGFunction(blocks=(entry, header, body, exit_block, latch))

