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
    relink_predecessors,
)
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion
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
              (if (string=? (string-ref s n) "-")
                  (integer+ n 1)
                  (if (string=? (string-ref s n) "+")
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
              (if (string=? (string-ref s n) "-")
                  (integer+ n 1)
                  (if (string=? (string-ref s n) "+")
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

        entry = MenaiCFGBlock(id=0, label="entry")
        entry.instrs = [
            MenaiCFGParamInstr(result=lst, index=0, param_name="lst"),
            MenaiCFGBuiltinInstr(result=x, op="list-first", args=[lst]),
            MenaiCFGBuiltinInstr(result=one, op="integer+", args=[x, x]),
            MenaiCFGBuiltinInstr(result=y0, op="integer+", args=[one, one]),
        ]

        preheader = MenaiCFGBlock(id=1, label="preheader")

        header = MenaiCFGBlock(id=2, label="loop_entry")
        header.instrs = [
            MenaiCFGBuiltinInstr(result=y2, op="integer+", args=[x, one]),
        ]

        body = MenaiCFGBlock(id=3, label="body")
        body.instrs = [
            MenaiCFGBuiltinInstr(result=y3, op="integer+", args=[k, one]),
        ]

        exit_block = MenaiCFGBlock(id=4, label="exit")

        entry.terminator = MenaiCFGJumpTerm(target=preheader)
        preheader.terminator = MenaiCFGJumpTerm(target=header)
        header.terminator = MenaiCFGBranchTerm(
            cond=y2, true_block=exit_block, false_block=body,
        )
        body.terminator = MenaiCFGSelfLoopTerm(args=[], target=header)
        exit_block.terminator = MenaiCFGReturnTerm(value=y2)

        func = MenaiCFGFunction(params=["lst"], binding_name="loop")
        func.blocks = [entry, preheader, header, body, exit_block]
        relink_predecessors(func)
        return func

    def test_guard_proven_before_loop_not_repeated_at_header(self):
        """
        %x is guarded once in the entry block.  The loop header and the body
        use it, but neither may re-guard it: it is typed on every path into
        the header.  There must be exactly one guard on %x.
        """
        func = self._loop_with_header_use()
        MenaiCFGGuardInsertion()._optimize_function(func)
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
        MenaiCFGGuardInsertion()._optimize_function(func)
        assert _count_guards(func) == 5
