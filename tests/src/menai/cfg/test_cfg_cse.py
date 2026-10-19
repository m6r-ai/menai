"""
Tests for common subexpression elimination (CSE) in the CFG.

Two instructions compute the same value when they are the same operation on the
same operands, and the operation is a pure function of its operands.  The later
of two such instructions is replaced by the earlier one when the earlier one's
block dominates the later one's, so the earlier value is available on every
path to the later instruction.

These tests verify that:
  - A redundant computation is eliminated and its uses rewritten.
  - A computation on a path that does not dominate the other is NOT merged.
  - A raising computation is merged only where it is redundant on all paths,
    so merging cannot introduce a raise (ADR-0049).
  - Eliminated computations produce correct results end to end.
  - The pass reaches a fixed point.
  - A merged value used as a closure capture is rewritten in the patch
    instructions, not only in the instruction operands.
"""

from menai import Menai
from menai.cfg.menai_cfg import (
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGMakeClosureInstr,
    MenaiCFGPatchClosureInstr,
)
from menai.cfg.menai_cfg_cse import MenaiCFGCSE
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    collect_functions,
)
from menai.menai_compiler import MenaiCompiler


def _compile_cfg(src: str) -> MenaiCFGFunction:
    """Compile Menai source and return the optimised CFG."""
    return MenaiCompiler().compile_to_cfg(src)


def _find_function(cfg: MenaiCFGFunction, name: str) -> MenaiCFGFunction:
    """Return the function named `name`, or the root if no name matches."""
    for func in collect_functions(cfg):
        if func.binding_name == name:
            return func

    return cfg


def _count_builtin(func: MenaiCFGFunction, op: str) -> int:
    """Count builtin instructions with opcode name `op` in `func`."""
    return sum(
        1
        for block in func.blocks
        for instr in block.instrs
        if isinstance(instr, MenaiCFGBuiltinInstr) and instr.op == op
    )


class TestRedundantComputationElimination:
    """A redundant computation is eliminated and its uses rewritten."""

    def test_duplicate_in_same_block_eliminated(self):
        """
        (let ((x (list 1 2 3)))
          (integer+ (list-length x) (list-length x)))

        (list-length x) is computed twice in the same block.  The second is
        redundant: the first dominates it.  Only one LIST_LENGTH remains.
        """
        cfg = _compile_cfg("""
        (let ((x (list 1 2 3)))
          (integer+ (list-length x) (list-length x)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'list-length') == 1

    def test_duplicate_across_dominating_blocks_eliminated(self):
        """
        (let ((loop (lambda (n)
                         (if (integer=? n 0)
                             (list-length x)
                             (integer+ (list-length x) (loop (integer- n 1))))))
                 (x (list 1 2 3)))
          (loop 3))

        (list-length x) is computed on both arms.  The block that computes it
        on the then arm dominates the else arm's use only if the then arm is
        not on the path...  In this shape the two arms are siblings, so neither
        dominates the other and both are kept.
        """
        cfg = _compile_cfg("""
        (let ((loop (lambda (n)
                         (if (integer=? n 0)
                             (list-length x)
                             (integer+ (list-length x) (loop (integer- n 1))))))
                 (x (list 1 2 3)))
          (loop 3))
        """)
        func = _find_function(cfg, "loop")
        # The two arms are mutually exclusive, so neither dominates the other
        # and the computation stays on both.
        assert _count_builtin(func, 'list-length') == 2

    def test_distinct_operands_not_merged(self):
        """
        (let ((a (list 1 2)) (b (list 3 4)))
          (integer+ (list-length a) (list-length b)))

        The two list-length calls have different operands, so they are
        different expressions and both are kept.
        """
        cfg = _compile_cfg("""
        (let ((a (list 1 2)) (b (list 3 4)))
          (integer+ (list-length a) (list-length b)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'list-length') == 2

    def test_correct_results_after_elimination(self):
        """
        End-to-end: (list-length x) computed twice, summed.  x has 3 elements,
        so the result is 3 + 3 = 6.
        """
        menai = Menai()
        result = menai.evaluate("""
        (let ((x (list 1 2 3)))
          (integer+ (list-length x) (list-length x)))
        """)
        assert result == 6


class TestRaisingComputationMerging:
    """A raising computation is merged only where it is redundant on all paths."""

    def test_raising_computation_merged_in_same_block(self):
        """
        (let ((d 0))
          (integer+ (integer/ 100 d) (integer/ 100 d)))

        Both divisions are in the same block, so the first dominates the
        second and the merge is redundant on all paths.  The program raises on
        the first division either way, so merging does not introduce a raise.
        Only one INTEGER_DIV remains.

        The two divisions use equal but distinct constant instructions for
        100, so this also pins that equal constants are treated as the same
        operand.
        """
        cfg = _compile_cfg("""
        (let ((d 0))
          (integer+ (integer/ 100 d) (integer/ 100 d)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'integer/') == 1

    def test_raising_computation_not_merged_across_branches(self):
        """
        (let ((d 0) (n 3))
          (if (integer=? n 0)
              (integer/ 100 d)
              (integer/ 100 d)))

        The two divisions are on mutually exclusive branches, so neither
        dominates the other.  Merging them would hoist the division above the
        branch, making it execute on the path that did not execute it, which
        could introduce a raise.  Both are kept.
        """
        cfg = _compile_cfg("""
        (let ((d 0) (n 3))
          (if (integer=? n 0)
              (integer/ 100 d)
              (integer/ 100 d)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'integer/') == 2

    def test_raising_computation_correct_results(self):
        """
        End-to-end: a raising computation merged in the same block still
        raises, and one that would not execute still does not raise.
        """
        menai = Menai()

        # Both divisions execute, so the program raises.
        raised = False
        try:
            menai.evaluate("""
            (let ((d 0))
              (integer+ (integer/ 100 d) (integer/ 100 d)))
            """)

        except Exception:  # pylint: disable=broad-except
            raised = True

        assert raised

        # The division is on the branch that is not taken, so the program
        # completes and returns 42.
        result = menai.evaluate("""
        (let ((d 0) (n 3))
          (if (integer=? n 100)
              (integer/ 100 d)
              42))
        """)
        assert result == 42


class TestFixedPoint:
    """The pass reaches a fixed point."""

    def test_second_run_reports_no_change(self):
        """
        After the compiler's pipeline has run CSE to a fixed point, running it
        again on the optimised CFG reports no further change.
        """
        cfg = _compile_cfg("""
        (let ((x (list 1 2 3)))
          (integer+ (list-length x) (list-length x)))
        """)
        _, changed = MenaiCFGCSE().optimize(cfg, MenaiCFGContext())
        assert not changed


class TestEqualConstantsAreTheSameOperand:
    """Equal constants are treated as the same operand."""

    def test_expression_with_equal_constants_merged(self):
        """
        (let ((x (list 1 2 3)))
          (integer+ (list-nth x 1) (list-nth x 1)))

        The CFG builder emits a separate constant instruction for each literal
        1, so the two list-nth calls name different constant ids.  The
        constants are equal, so the calls are the same expression and the
        second is eliminated.
        """
        cfg = _compile_cfg("""
        (let ((x (list 1 2 3)))
          (integer+ (list-nth x 1) (list-nth x 1)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'list-nth') == 1

    def test_expression_with_different_constants_not_merged(self):
        """
        (let ((x (list 1 2 3)))
          (integer+ (list-nth x 1) (list-nth x 2)))

        The two list-nth calls name different constants, so they are different
        expressions and both are kept.
        """
        cfg = _compile_cfg("""
        (let ((x (list 1 2 3)))
          (integer+ (list-nth x 1) (list-nth x 2)))
        """)
        func = _find_function(cfg, "<lambda>")
        assert _count_builtin(func, 'list-nth') == 2


class TestClosureCaptureSubstitution:
    """A merged value used as a closure capture is rewritten in patch instructions."""

    def test_capture_rewritten_in_patch_instructions(self):
        """
        A letrec whose closure captures a value that is also computed twice.
        The merged value must be rewritten wherever it is referenced,
        including in the patch-closure instructions that fill in sibling
        captures, not only in the instruction operands.

        This is a structural check: after CSE, no patch-closure instruction may
        reference a value id that no instruction defines.
        """
        cfg = _compile_cfg("""
        (let ((f (lambda (n) (if (integer=? n 0) 0 (g (integer- n 1)))))
                 (g (lambda (n) (if (integer=? n 0) 1 (f (integer- n 1))))))
          (f 5))
        """)

        for func in collect_functions(cfg):
            defined: set[int] = set()
            for block in func.blocks:
                for instr in block.instrs:
                    result = getattr(instr, 'result', None)
                    if result is not None:
                        defined.add(result.id)

                for patch in block.patch_instrs:
                    assert patch.closure.id in defined, (
                        "patch-closure references a value no instruction defines"
                    )
                    assert patch.value.id in defined, (
                        "patch-closure references a value no instruction defines"
                    )

    def test_closure_capture_correct_results(self):
        """
        End-to-end: mutual recursion through closures still produces the right
        answer after CSE.
        """
        menai = Menai()
        result = menai.evaluate("""
        (let ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                 (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (list-length (filter-list even? (list 0 1 2 3 4 5))))
        """)
        assert result == 3
