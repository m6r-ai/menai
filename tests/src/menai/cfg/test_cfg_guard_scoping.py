"""
Tests for guard insertion scoping in the type propagation pass.

A guard inserted in one block must not suppress guards in sibling blocks
(reached via alternative branches).  A guard only proves the type on the
path through its own block; leaking that knowledge to sibling blocks is
unsound because the sibling may be reached via a path that never executed
the guard.

These tests compile Menai source and count ASSERT_* opcodes to verify
that guards are correctly scoped.
"""

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGGuardInstr,
    MenaiCFGJumpTerm,
    MenaiCFGParamInstr,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.menai_compiler import MenaiCompiler


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)

    return n


def _compile(src: str):
    """Compile Menai source and return the top-level CodeObject."""
    return MenaiCompiler().compile(src)


def _find_lambda(code):
    """Return the first nested code object."""
    assert code.code_objects, "expected at least one nested code object"
    return code.code_objects[0]


class TestGuardScoping:
    """Guards in one branch must not suppress guards in sibling branches."""

    def test_guard_in_then_does_not_suppress_guard_in_else(self):
        """
        (lambda (x) (if (boolean? x) ($list-length x) ($list-length x)))

        Both branches call list-length on x, which expects a list argument.
        Since x is a parameter (unknown type), each branch needs its own
        ASSERT_LIST guard.  A guard inserted in the then-branch must not
        suppress the guard in the else-branch.
        """
        src = '(lambda (x) (if (boolean? x) ($list-length x) ($list-length x)))'
        code = _find_lambda(_compile(src))
        assert _count_op(code, Opcode.ASSERT_LIST) == 2

    def test_guard_in_else_does_not_suppress_guard_in_then(self):
        """
        (lambda (x) (if (boolean? x) 0 ($list-length x)))

        Only the else-branch uses x as a list.  The then-branch returns a
        constant.  There should be exactly one ASSERT_LIST guard (in the
        else-branch), and the boolean? check in the condition should get
        an ASSERT_BOOLEAN guard.
        """
        src = '(lambda (x) (if (boolean? x) 0 ($list-length x)))'
        code = _find_lambda(_compile(src))
        assert _count_op(code, Opcode.ASSERT_LIST) == 1

    def test_guard_in_dominator_carries_to_dominated_block(self):
        """
        (lambda (x) (let ((y ($list-length x))) (if (boolean? x) y y)))

        The list-length call is in the entry block (before the if).  The
        guard on x is inserted there.  Both branches just return y, so no
        additional guard is needed.  There should be exactly one ASSERT_LIST.
        """
        src = '(lambda (x) (let ((y ($list-length x))) (if (boolean? x) y y)))'
        code = _find_lambda(_compile(src))
        assert _count_op(code, Opcode.ASSERT_LIST) == 1

    def test_single_predecessor_chain_inherits_guard(self):
        """
        (lambda (x) (if (boolean? x) ($list-length x) 0))

        The then-branch uses x as a list.  It has a single predecessor
        (the entry block).  There should be exactly one ASSERT_LIST guard
        in the then-branch.
        """
        src = '(lambda (x) (if (boolean? x) ($list-length x) 0))'
        code = _find_lambda(_compile(src))
        assert _count_op(code, Opcode.ASSERT_LIST) == 1

    def test_join_point_inherits_type_when_all_preds_agree(self):
        """
        (lambda (n s)
          (if (integer>=? n 0)
              (if (string=? (string-nth s n) "-")
                  (integer+ n 1)
                  (if (string=? (string-nth s n) "+")
                      (integer+ n 1)
                      n))
              n))

        The parameter n is guarded as integer in the entry block.  Both
        inner branches call integer+ on n, and both jump to the same join
        point (the block containing integer+).  Since all predecessors of
        the join point agree that n is integer, no redundant ASSERT_INTEGER
        guard should be inserted there.  There should be exactly one
        ASSERT_INTEGER guard (in the entry block).
        """
        src = """
        (lambda (n s)
          (if (integer>=? n 0)
              (if (string=? (string-nth s n) "-")
                  (integer+ n 1)
                  (if (string=? (string-nth s n) "+")
                      (integer+ n 1)
                      n))
              n))
        """
        code = _find_lambda(_compile(src))
        assert _count_op(code, Opcode.ASSERT_INTEGER) == 1


def _v(value_id: int, hint: str = "") -> MenaiCFGValue:
    """Return an SSA value with the given id."""
    return MenaiCFGValue(id=value_id, hint=hint)


def _count_guards(func: MenaiCFGFunction) -> int:
    """Count MenaiCFGGuardInstr instructions in a function."""
    return sum(
        1 for block in func.blocks for instr in block.instrs
        if isinstance(instr, MenaiCFGGuardInstr)
    )


def _guards_for(func: MenaiCFGFunction, value_id: int) -> int:
    """Count guards on a specific SSA value in a function."""
    return sum(
        1 for block in func.blocks for instr in block.instrs
        if isinstance(instr, MenaiCFGGuardInstr) and instr.value.id == value_id
    )


class TestLoopHeaderGuardScoping:
    """
    A guard proven before a loop must suppress guards at the loop header.

    The loop header is a join point: it is reached from the pre-header and
    from the loop's back-edge.  The back-edge predecessor's outgoing types
    depend on the header itself, so a single forward pass over the block list
    would not yet know them when it reaches the header, and would re-insert a
    guard for a value that is in fact typed on every path into the header.
    The pass computes block types to a fixed point to avoid this.
    """

    @staticmethod
    def _loop_with_header_use() -> MenaiCFGFunction:
        """
        Build a CFG shaped like a rotated tail-recursive loop:

          block 0 (entry):     %x = list-first %lst; integer+ %x %x  (guards %x)
          block 1 (preheader): jump to block 2
          block 2 (header):    integer+ %x %one; branch to block 4 / block 3
          block 3 (body):      integer+ %k %one; self-loop to block 2
          block 4 (exit):      return

        The header (block 2) is a join of the pre-header (block 1) and the
        back-edge (block 3), and uses %x, which is guarded in the entry block.
        The body (block 3) does not use %x, so it does not re-establish %x's
        type on the back-edge.
        """
        lst = _v(0, "lst")
        x = _v(1, "x")
        one = _v(2, "one")
        y0 = _v(3, "y0")
        y2 = _v(4, "y2")
        k = _v(5, "k")
        y3 = _v(6, "y3")

        entry = MenaiCFGBlock(
            id=0,
            label="entry",
            instrs=(
                MenaiCFGParamInstr(result=lst, index=0, param_name="lst"),
                MenaiCFGBuiltinInstr(result=x, op="list-first", args=[lst]),
                MenaiCFGBuiltinInstr(result=one, op="integer+", args=[x, x]),
                MenaiCFGBuiltinInstr(result=y0, op="integer+", args=[one, one]),
            ),
            terminator=MenaiCFGJumpTerm(target=1),
        )

        preheader = MenaiCFGBlock(
            id=1, label="preheader", terminator=MenaiCFGJumpTerm(target=2),
        )

        header = MenaiCFGBlock(
            id=2,
            label="loop_entry",
            instrs=(
                MenaiCFGBuiltinInstr(result=y2, op="integer+", args=[x, one]),
            ),
            terminator=MenaiCFGBranchTerm(
                cond=y2, true_block=4, false_block=3,
            ),
        )

        body = MenaiCFGBlock(
            id=3,
            label="body",
            instrs=(
                MenaiCFGBuiltinInstr(result=y3, op="integer+", args=[k, one]),
            ),
            terminator=MenaiCFGSelfLoopTerm(args=[], target=2),
        )

        exit_block = MenaiCFGBlock(
            id=4, label="exit", terminator=MenaiCFGReturnTerm(value=y2),
        )

        return MenaiCFGFunction(
            blocks=(entry, preheader, header, body, exit_block),
            params=("lst",),
            binding_name="loop",
        )

    def test_guard_proven_before_loop_not_repeated_at_header(self):
        """
        %x is guarded once in the entry block.  The loop header and the body
        use it, but neither may re-guard it: it is typed on every path into
        the header.  There must be exactly one guard on %x.
        """
        func = self._loop_with_header_use()
        func, _ = MenaiCFGGuardInsertion()._optimize_function(func, MenaiCFGContext())
        assert _guards_for(func, 1) == 1, (
            "the guard on %x must be inserted once, not repeated at the "
            "loop header or in the body"
        )

    def test_loop_header_guard_count(self):
        """
        The only guards needed are: %lst as list, %x as integer, %one as
        integer (all in the entry block), %y2 as boolean (the header branch
        condition), and %k as integer (the body).  No guard is re-inserted
        at the header for %x or %one.
        """
        func = self._loop_with_header_use()
        func, _ = MenaiCFGGuardInsertion()._optimize_function(func, MenaiCFGContext())
        assert _count_guards(func) == 5


def _guard_blocks(func: MenaiCFGFunction, value_id: int) -> list[int]:
    """Return the ids of the blocks holding a guard for `value_id`."""
    return [
        block.id for block in func.blocks
        if any(
            isinstance(instr, MenaiCFGGuardInstr) and instr.value.id == value_id
            for instr in block.instrs
        )
    ]


class TestSharedGuardPlacement:
    """
    A value used in several blocks is guarded once, in the dominating use block.

    The dominance propagation above suppresses a guard when the type reaches a
    block along a chain of single-predecessor blocks.  It cannot help when the
    using blocks meet at a join whose predecessors disagree, or when a loop's
    rotated test duplicates a use into a continuation block: each use then gets
    its own guard, and the copy in the continuation block runs once per
    iteration.  A guard in the using block that dominates the others covers
    them all, and is the guard that was already being paid there.
    """

    @staticmethod
    def _join_with_shared_use() -> MenaiCFGFunction:
        """
        Build a CFG where two blocks use %x and the entry block dominates both:

          block 0 (entry): %c = const #t; integer+ %x %x; branch -> 1 / 2
          block 1 (then):  jump -> 2
          block 2 (join):  integer- %x %k; branch -> 3 / 4
          block 3 (then):  jump -> 1      (the join is reached repeatedly)
          block 4 (exit):  return

        The join has two predecessors whose outgoing types for %x disagree, so
        the dominance propagation cannot establish %x there and would otherwise
        insert a second guard.
        """
        x = _v(0, "x")
        k = _v(1, "k")
        cond = _v(2, "c")
        y0 = _v(3, "y0")
        y2 = _v(4, "y2")
        y3 = _v(5, "y3")

        entry = MenaiCFGBlock(
            id=0,
            label="entry",
            instrs=(
                MenaiCFGParamInstr(result=x, index=0, param_name="x"),
                MenaiCFGParamInstr(result=k, index=1, param_name="k"),
                MenaiCFGBuiltinInstr(result=y0, op="integer+", args=[x, x]),
                MenaiCFGBuiltinInstr(result=cond, op="integer>?", args=[y0, k]),
            ),
            terminator=MenaiCFGBranchTerm(cond=cond, true_block=1, false_block=2),
        )

        then_block = MenaiCFGBlock(
            id=1, label="then", terminator=MenaiCFGJumpTerm(target=2),
        )

        join = MenaiCFGBlock(
            id=2,
            label="join",
            instrs=(
                MenaiCFGBuiltinInstr(result=y2, op="integer-", args=[x, k]),
            ),
            terminator=MenaiCFGBranchTerm(cond=cond, true_block=3, false_block=4),
        )

        back_edge = MenaiCFGBlock(
            id=3,
            label="then",
            instrs=(MenaiCFGBuiltinInstr(result=y3, op="integer+", args=[k, k]),),
            terminator=MenaiCFGJumpTerm(target=1),
        )

        exit_block = MenaiCFGBlock(
            id=4, label="exit", terminator=MenaiCFGReturnTerm(value=y2),
        )

        return MenaiCFGFunction(
            blocks=(entry, then_block, join, back_edge, exit_block),
            params=("x", "k"),
            binding_name="shared",
        )

    def test_shared_use_guarded_once_in_dominating_block(self):
        """
        %x is used in the entry block and again at the join.  The entry block
        dominates the join, so its guard covers both and no guard is emitted at
        the join.
        """
        func = self._join_with_shared_use()
        func, _ = MenaiCFGGuardInsertion()._optimize_function(func, MenaiCFGContext())
        assert _guard_blocks(func, 0) == [0], (
            "the guard on %x must be emitted once, in the block that dominates "
            "every use, not again at the join"
        )

    def test_guard_stays_in_loop_body_when_header_does_not_use_value(self):
        """
        A value used only inside a loop keeps its guard inside the loop.  Moving
        it to the header would make the program raise on a path that never
        reached the use, so a block is only a guard anchor if it uses the value
        itself.
        """
        x = _v(10, "x")
        k = _v(11, "k")
        cond = _v(12, "c")
        header_work = _v(13, "h")
        body_work = _v(14, "b")
        exit_value = _v(15, "e")

        entry = MenaiCFGBlock(
            id=0,
            label="entry",
            instrs=(
                MenaiCFGParamInstr(result=x, index=0, param_name="x"),
                MenaiCFGParamInstr(result=k, index=1, param_name="k"),
                MenaiCFGBuiltinInstr(result=cond, op="integer>?", args=[k, k]),
            ),
            terminator=MenaiCFGJumpTerm(target=1),
        )

        header = MenaiCFGBlock(
            id=1,
            label="loop_entry",
            instrs=(MenaiCFGBuiltinInstr(result=header_work, op="integer+", args=[k, k]),),
            terminator=MenaiCFGBranchTerm(cond=header_work, true_block=2, false_block=3),
        )

        body = MenaiCFGBlock(
            id=2,
            label="body",
            instrs=(
                MenaiCFGBuiltinInstr(result=body_work, op="integer%", args=[x, x]),
            ),
            terminator=MenaiCFGJumpTerm(target=1),
        )

        exit_block = MenaiCFGBlock(
            id=3, label="exit", terminator=MenaiCFGReturnTerm(value=exit_value),
        )

        func = MenaiCFGFunction(
            blocks=(entry, header, body, exit_block),
            params=("x", "k"),
            binding_name="inner-use",
        )

        func, _ = MenaiCFGGuardInsertion()._optimize_function(func, MenaiCFGContext())
        assert _guard_blocks(func, 10) == [2], (
            "the guard on %x must stay in the body that uses it, not move to the "
            "loop header"
        )
