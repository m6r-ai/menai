"""
Tests for loop rotation (loop inversion) in the CFG.

A self-loop (tail-recursive loop) is emitted with its test at the top:
each iteration pays for the unconditional back-edge jump plus the
conditional test.  The rotation pass moves the test to the bottom of the
loop, so the back-edge lands on the test and the unconditional jump is
replaced by a conditional branch back into the body.

These tests compile Menai source and inspect the resulting bytecode to
verify that:
  - A rotated loop has a conditional branch back into the body (a backward
    JUMP_IF_FALSE) instead of an unconditional backward JUMP.
  - The peeled entry test is retained so an empty input skips the loop.
  - The compiled code produces correct results.
  - Functions without a self-loop are unaffected.
"""

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.menai_compiler import MenaiCompiler


def _compile(src: str):
    """Compile Menai source and return the top-level CodeObject."""
    return MenaiCompiler().compile(src)


def _find_lambda(code, name: str):
    """Find a nested code object by name (BFS through all code objects)."""
    queue = [code]
    while queue:
        co = queue.pop(0)
        if name in co.name:
            return co
        queue.extend(co.code_objects)
    raise AssertionError(f"lambda {name!r} not found")


def _has_backward_conditional_jump(code) -> bool:
    """
    Return True if the function has a conditional jump whose target is
    earlier in the instruction list (a rotated back-edge).
    """
    for i, instr in enumerate(code.instructions):
        op = unpack_instruction(instr)
        if op.opcode in (int(Opcode.JUMP_IF_FALSE), int(Opcode.JUMP_IF_TRUE)):
            if op.src1 < i:
                return True

    return False


def _has_backward_unconditional_jump(code) -> bool:
    """
    Return True if the function has an unconditional jump whose target is
    earlier in the instruction list (a self-loop back-edge).
    """
    for i, instr in enumerate(code.instructions):
        op = unpack_instruction(instr)
        if op.opcode == int(Opcode.JUMP) and op.src0 < i:
            return True

    return False


def _count_op(code, opcode) -> int:
    """Count occurrences of `opcode` in `code` and all nested code objects."""
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)

    return n


class TestLoopRotation:
    """Self-loops with a top-of-loop test should be rotated."""

    def test_sum_list_loop_rotated(self):
        """
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list 1 2 3 4 5) 0))

        The loop test (list-null? lst) is at the top and the back-edge is
        unconditional.  After rotation the back-edge is a conditional branch
        back into the body.
        """
        src = """
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list 1 2 3 4 5) 0))
        """
        code = _compile(src)
        fn = _find_lambda(code, "sum-list")
        assert _has_backward_conditional_jump(fn), (
            "rotated loop must have a conditional back-edge"
        )
        assert not _has_backward_unconditional_jump(fn), (
            "rotated loop must not have an unconditional back-edge"
        )

    def test_rotated_loop_retains_entry_test(self):
        """
        The peeled entry test must be retained so an empty list skips the
        loop.  The loop test uses a builtin (LIST_NULL_P), so there should be
        two of them after rotation: one in the header, one at the bottom.
        """
        src = """
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list 1 2 3 4 5) 0))
        """
        code = _compile(src)
        fn = _find_lambda(code, "sum-list")
        assert _count_op(fn, Opcode.LIST_NULL_P) == 2, (
            "rotated loop must have both the entry test and the bottom test"
        )

    def test_correct_results(self):
        """End-to-end: a rotated loop must produce correct results."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list 1 2 3 4 5) 0))
        """)
        assert result == 15

    def test_empty_input_skips_loop(self):
        """
        End-to-end: an empty input must skip the loop entirely and return the
        accumulator unchanged, exercising the peeled entry test.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list) 42))
        """)
        assert result == 42

    def test_single_element_input(self):
        """
        End-to-end: a single-element input must run the body exactly once and
        then exit via the rotated test.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst)
                                  (integer+ acc (list-first lst)))))))
          (sum-list (list 7) 0))
        """)
        assert result == 7

    def test_count_down_loop_rotated(self):
        """
        (letrec ((count-down
                  (lambda (n)
                    (if (integer<=? n 0)
                        n
                        (count-down (integer- n 1))))))
          (count-down 10))

        A numeric self-loop should also be rotated.
        """
        src = """
        (letrec ((count-down
                  (lambda (n)
                    (if (integer<=? n 0)
                        n
                        (count-down (integer- n 1))))))
          (count-down 10))
        """
        code = _compile(src)
        fn = _find_lambda(code, "count-down")
        assert _has_backward_conditional_jump(fn)

    def test_count_down_correct_results(self):
        """End-to-end: a rotated numeric loop must produce correct results."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((count-down
                  (lambda (n)
                    (if (integer<=? n 0)
                        n
                        (count-down (integer- n 1))))))
          (count-down 10))
        """)
        assert result == 0

    def test_function_without_self_loop_unaffected(self):
        """
        A function without a self-loop must not be modified.  Uses a mutually
        recursive letrec so the functions survive to the CFG stage.
        """
        src = """
        (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                 (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (even? 10))
        """
        code = _compile(src)
        even_fn = _find_lambda(code, "even?")
        assert not _has_backward_conditional_jump(even_fn)
        assert not _has_backward_unconditional_jump(even_fn)

    def test_map_list_helper_rotated(self):
        """
        The prelude's map-list helper is a canonical self-loop.  It should be
        rotated.  (map-list (lambda (x) (integer* x 2)) (list 1 2 3)) = [2, 4, 6].
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (map-list (lambda (x) (integer* x 2)) (list 1 2 3))
        """)
        assert str(result) == "[2, 4, 6]"

    def test_nested_loops_both_rotated(self):
        """
        End-to-end: nested self-loops should both be rotated and produce
        correct results.  Builds a list of cumulative sums.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((outer
                  (lambda (n acc)
                    (if (integer<=? n 0)
                        acc
                        (outer (integer- n 1)
                               (list-prepend acc (inner n 0))))))
                 (inner
                  (lambda (n acc)
                    (if (integer<=? n 0)
                        acc
                        (inner (integer- n 1) (integer+ acc n))))))
          (outer 3 (list)))
        """)
        assert str(result) == "[1, 3, 6]"


class TestMultiBlockBodyRotation:
    """A self-loop whose body spans several blocks should be rotated."""

    def test_nested_branch_body_rotated(self):
        """
        (letrec ((count-mismatch
                  (lambda (i acc)
                    (if (integer>=? i 9)
                        acc
                        (count-mismatch
                          (integer+ i 1)
                          (if (integer!=? i 4)
                              (integer+ acc 1)
                              acc))))))
          (count-mismatch 0 0))

        The loop body contains a nested branch, so the body is a region of
        several blocks rather than a single block.  The loop test is at the
        top and the back-edge is unconditional; after rotation the back-edge
        is a conditional branch back into the body.
        """
        src = """
        (letrec ((count-mismatch
                  (lambda (i acc)
                    (if (integer>=? i 9)
                        acc
                        (count-mismatch
                          (integer+ i 1)
                          (if (integer!=? i 4)
                              (integer+ acc 1)
                              acc))))))
          (count-mismatch 0 0))
        """
        code = _compile(src)
        fn = _find_lambda(code, "count-mismatch")
        assert _has_backward_conditional_jump(fn), (
            "rotated loop must have a conditional back-edge"
        )
        assert not _has_backward_unconditional_jump(fn), (
            "rotated loop must not have an unconditional back-edge"
        )

    def test_nested_branch_body_retains_entry_test(self):
        """
        The peeled entry test must be retained so the loop is skipped when the
        entry condition is false.  The loop test uses INTEGER_GTE_P, so there
        should be two of them after rotation: one in the header, one at the
        bottom.
        """
        src = """
        (letrec ((count-mismatch
                  (lambda (i acc)
                    (if (integer>=? i 9)
                        acc
                        (count-mismatch
                          (integer+ i 1)
                          (if (integer!=? i 4)
                              (integer+ acc 1)
                              acc))))))
          (count-mismatch 0 0))
        """
        code = _compile(src)
        fn = _find_lambda(code, "count-mismatch")
        assert _count_op(fn, Opcode.INTEGER_GTE_P) == 2, (
            "rotated loop must have both the entry test and the bottom test"
        )

    def test_nested_branch_body_correct_results(self):
        """End-to-end: a rotated multi-block-body loop must produce correct results."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((count-mismatch
                  (lambda (i acc)
                    (if (integer>=? i 9)
                        acc
                        (count-mismatch
                          (integer+ i 1)
                          (if (integer!=? i 4)
                              (integer+ acc 1)
                              acc))))))
          (count-mismatch 0 0))
        """)
        assert result == 8

    def test_nested_branch_body_skips_loop_when_entry_false(self):
        """
        End-to-end: when the entry condition is already false, the peeled
        entry test must skip the loop entirely and return the accumulator.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((count-mismatch
                  (lambda (i acc)
                    (if (integer>=? i 9)
                        acc
                        (count-mismatch
                          (integer+ i 1)
                          (if (integer!=? i 4)
                              (integer+ acc 1)
                              acc))))))
          (count-mismatch 9 42))
        """)
        assert result == 42


class TestMultipleSelfLoopsNotRotated:
    """A function whose loop has more than one back-edge is not rotated."""

    def test_two_arms_tail_calling_same_loop_not_rotated(self):
        """
        (letrec ((scan
                  (lambda (i)
                    (if (integer>=? i 10)
                        i
                        (if (integer=? i 5)
                            (scan (integer+ i 1))
                            (scan (integer+ i 1)))))))
          (scan 0))

        Both arms of the inner branch tail-call `scan`, so the function has
        two self-loop terminators.  Rotating only one of them would leave the
        other jumping to the unrotated entry, so the loop must be left
        unrotated.
        """
        src = """
        (letrec ((scan
                  (lambda (i)
                    (if (integer>=? i 10)
                        i
                        (if (integer=? i 5)
                            (scan (integer+ i 1))
                            (scan (integer+ i 1)))))))
          (scan 0))
        """
        code = _compile(src)
        fn = _find_lambda(code, "scan")
        assert not _has_backward_conditional_jump(fn), (
            "a loop with multiple back-edges must not be rotated"
        )

    def test_two_arms_correct_results(self):
        """End-to-end: a multi-back-edge loop must still produce correct results."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((scan
                  (lambda (i)
                    (if (integer>=? i 10)
                        i
                        (if (integer=? i 5)
                            (scan (integer+ i 1))
                            (scan (integer+ i 1)))))))
          (scan 0))
        """)
        assert result == 10

    def test_prelude_find_vector_correct(self):
        """
        End-to-end: the prelude's find-vector has a nested branch in its loop
        body and must return the first matching element (or #none).
        """
        from menai import Menai
        menai = Menai()

        found = menai.evaluate(
            "(find-vector (lambda (x) (integer>? x 3)) (vector 1 2 3 4 5))"
        )
        assert found == 4
        missing = menai.evaluate(
            "(find-vector (lambda (x) (integer>? x 9)) (vector 1 2 3))"
        )
        assert missing is None
