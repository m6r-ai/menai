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
