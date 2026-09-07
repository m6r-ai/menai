"""
Tests for closure register back-propagation into the outgoing zone.

The slot allocator's Phase 3 back-propagates call argument registers into the
outgoing zone so the bytecode emitter does not need MOVE instructions to stage
them.  Closure registers (results of MAKE_CLOSURE that are read by
PATCH_CLOSURE) and capture registers (values read by PATCH_CLOSURE) are eligible
for this optimisation when the barrier check ensures no call or make-* clobbers
the outgoing zone between the register's definition and the consuming
instruction.
"""

import pytest
from menai import Menai
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.menai_compiler import MenaiCompiler


def _compile(src: str):
    return MenaiCompiler().compile(src)


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)
    return n


def _find_lambda(code):
    """Return the first nested code object (the compiled lambda body)."""
    assert code.code_objects, "expected at least one nested code object"
    return code.code_objects[0]


@pytest.fixture
def menai():
    return Menai()


class TestClosureMoveElimination:
    """Closure registers that are call arguments should be back-propagated
    into the outgoing zone, eliminating the MOVE that would otherwise stage
    them.
    """

    def test_closure_as_call_arg_no_move(self):
        """A closure created with a capture and immediately passed as a call
        argument should not require a MOVE to stage it.

        The closure register's last use is the consuming CALL, and no barrier
        exists between MAKE_CLOSURE and that CALL, so the register is assigned
        directly to its outgoing zone slot.
        """
        src = """
            (lambda (face)
              (let ((center (list-ref face 4)))
                (list-length
                  (filter-list (lambda (s) (integer!=? s center)) face))))
        """
        code = _find_lambda(_compile(src))
        # The closure is arg 0 and should be back-propagated.
        # face is arg 1 and is a fixed param — its MOVE is unavoidable.
        # So we expect exactly 1 MOVE (for face), not 2.
        assert _count_op(code, Opcode.MOVE) == 1

    def test_closure_as_call_arg_correct_result(self, menai):
        """Verify the optimised code produces the correct result."""
        result = menai.evaluate("""
            (let ((count-mismatched
                    (lambda (face)
                      (let ((center (list-ref face 4)))
                        (list-length
                          (filter-list (lambda (s) (integer!=? s center)) face))))))
              (count-mismatched (list 1 1 1 1 1 2 2 3)))
        """)
        assert result == 3

    def test_capture_reg_as_call_arg_no_move(self):
        """A capture register that is also used as a call argument should be
        back-propagated into the outgoing zone.

        The center value is captured by the lambda and also passed as a call
        argument.  When the consuming CALL is the last use and no barrier
        exists, the capture register is assigned directly to its outgoing
        zone slot.
        """
        src = """
            (lambda (face)
              (let ((center (list-ref face 4)))
                (list-length
                  (filter-list face (lambda (s) (integer!=? s center))))))
        """
        code = _find_lambda(_compile(src))
        # face is arg 0 (fixed param — MOVE unavoidable).
        # The closure is arg 1 and should be back-propagated.
        assert _count_op(code, Opcode.MOVE) == 1

    def test_barrier_between_def_and_call_keeps_move(self):
        """When a CALL exists between MAKE_CLOSURE and the consuming CALL,
        the closure register cannot be placed in the outgoing zone because
        the intermediate CALL would clobber it.  A MOVE must be preserved for
        the closure register.

        The pred closure captures a dynamic threshold, so it cannot be inlined.
        A map-list CALL between MAKE_CLOSURE and the filter-list CALL acts as
        a barrier, preventing the closure register from being back-propagated
        into the outgoing zone.
        """
        src = """
            (lambda (lst)
              (let ((threshold (list-ref lst 0)))
                (let ((pred (lambda (x) (integer<? x threshold))))
                  (let ((mapped (map-list (lambda (x) (integer+ x 1)) lst)))
                    (list-length (filter-list pred mapped))))))
        """
        code = _find_lambda(_compile(src))
        # The map-list CALL between MAKE_CLOSURE and the filter-list CALL
        # is a barrier, so the closure register stays in local_count and
        # a MOVE is needed to stage it.  The lst param also needs a MOVE.
        assert _count_op(code, Opcode.MAKE_CLOSURE) == 1
        assert _count_op(code, Opcode.PATCH_CLOSURE) == 1
        assert _count_op(code, Opcode.CALL) == 2
        assert _count_op(code, Opcode.MOVE) == 2

    def test_letrec_closure_as_call_arg(self):
        """A letrec closure (needs_patching=True) used as a call argument
        should also be back-propagated when no barrier exists.

        Mutual recursion forces needs_patching=True: even? and odd? capture
        each other.  When even? is passed to filter-list as a call argument
        and no barrier exists between MAKE_CLOSURE and the CALL, the closure
        register is assigned directly to its outgoing zone slot.
        """
        src = """
            (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                     (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
              (list-length (filter-list even? (list 0 1 2 3 4 5))))
        """
        code = _compile(src)
        # Both closures and the list argument are back-propagated into the
        # outgoing zone — no MOVEs needed.
        assert _count_op(code, Opcode.PATCH_CLOSURE) > 0
        assert _count_op(code, Opcode.MOVE) == 0

    def test_letrec_closure_correct_result(self, menai):
        """Verify the optimised letrec code produces correct results."""
        result = menai.evaluate("""
            (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                     (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
              (list-length (filter-list even? (list 0 1 2 3 4 5))))
        """)
        assert result == 3
