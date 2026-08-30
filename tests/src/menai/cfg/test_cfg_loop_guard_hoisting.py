"""
Tests for loop-invariant guard hoisting in the type propagation pass.

When a function contains a self-loop, loop-invariant guards (redundant on all
iterations after the first) from any block in the function are hoisted into a
preamble block.  The self-loop's jump target is set to the loop-entry block
(skipping the preamble), so the hoisted guards execute once on function entry
rather than on every iteration.

These tests compile Menai source and inspect the resulting bytecode to verify
that:
  - Loop-invariant guards are hoisted (the self-loop jump skips them).
  - Non-loop-invariant guards are kept in the loop body.
  - Functions without self-loops are unaffected.
  - The compiled code produces correct results.
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


def _find_lambda(code, name: str):
    """Find a nested code object by name (BFS through all code objects)."""
    queue = [code]
    while queue:
        co = queue.pop(0)
        if name in co.name:
            return co
        queue.extend(co.code_objects)
    raise AssertionError(f"lambda {name!r} not found")


def _self_loop_target(code) -> int | None:
    """
    Return the instruction index that the self-loop JUMP targets,
    or None if there is no self-loop in the function.
    """
    for i, instr in enumerate(code.instructions):
        op = unpack_instruction(instr)
        if op.opcode == int(Opcode.JUMP) and op.src0 < i:
            return op.src0

    return None


def _find_assert(code, opcode, slot) -> int | None:
    """
    Return the instruction index of the first ASSERT_* opcode checking
    the given slot, or None if not found.
    """
    for i, instr in enumerate(code.instructions):
        op = unpack_instruction(instr)
        if op.opcode == int(opcode) and op.src0 == slot:
            return i

    return None


class TestLoopInvariantGuardHoisting:
    """Loop-invariant guards in self-loop functions should be hoisted."""

    def test_type_preserving_param_guard_hoisted(self):
        """
        (letrec ((apply-moves
                  (lambda (cube moves)
                    (if (list-null? moves)
                        cube
                        (apply-moves (apply-move cube (list-first moves))
                                     (list-rest moves)))))
                 (apply-move (lambda (cube move) move)))
          (apply-moves 0 (list 1 2 3)))

        The 'moves' param is reassigned via list-rest (list -> list), so the
        ASSERT_LIST guard is loop-invariant.  It should be hoisted to the
        preamble, and the self-loop should skip it.
        """
        src = """
        (letrec ((apply-moves
                  (lambda (cube moves)
                    (if (list-null? moves)
                        cube
                        (apply-moves (apply-move cube (list-first moves))
                                     (list-rest moves)))))
                 (apply-move (lambda (cube move) move)))
          (apply-moves 0 (list 1 2 3)))
        """
        code = _compile(src)
        am = _find_lambda(code, "apply-moves")
        assert _count_op(am, Opcode.ASSERT_LIST) == 1
        target = _self_loop_target(am)
        assert target is not None
        guard_idx = _find_assert(am, Opcode.ASSERT_LIST, 1)
        assert guard_idx is not None
        assert target > guard_idx, "self-loop must skip the hoisted ASSERT_LIST guard"

    def test_free_var_guard_hoisted(self):
        """
        (letrec ((search-loop
                  (lambda (bound max-depth)
                    (if (integer>? bound max-depth)
                        (list 1)
                        (search-loop (integer+ bound 1))))))
          (search-loop 0 100))

        'max-depth' is a param not reassigned by the self-loop (the self-loop
        only provides one arg for 'bound').  Its ASSERT_INTEGER guard is
        loop-invariant and should be hoisted.
        """
        src = """
        (letrec ((search-loop
                  (lambda (bound max-depth)
                    (if (integer>? bound max-depth)
                        (list 1)
                        (search-loop (integer+ bound 1))))))
          (search-loop 0 100))
        """
        code = _compile(src)
        sl = _find_lambda(code, "search-loop")
        assert _count_op(sl, Opcode.ASSERT_INTEGER) == 2
        target = _self_loop_target(sl)
        assert target is not None
        # max-depth is slot 1 (param 0 = bound, param 1 = max-depth, free var = slot 2)
        # Actually: params get slots 0..P-1, free vars get P..P+F-1
        # search-loop has 2 params (bound=slot0, max-depth=slot1) and 1 free var (slot2)
        # The self-loop provides 1 arg (for bound), so max-depth is unchanged.
        # Both guards should be hoisted: max-depth (unchanged param) and
        # bound (back-edge type is integer from integer+).
        # The self-loop should skip both guards.
        guard0 = _find_assert(sl, Opcode.ASSERT_INTEGER, 0)
        guard1 = _find_assert(sl, Opcode.ASSERT_INTEGER, 1)
        assert guard0 is not None
        assert guard1 is not None
        assert target > guard0, "self-loop must skip the bound guard"
        assert target > guard1, "self-loop must skip the max-depth guard"

    def test_non_type_preserving_param_guard_kept_in_loop(self):
        """
        (letrec ((search-loop
                  (lambda (bound)
                    (if (integer>? bound 100)
                        (list 1)
                        (search-loop (struct-get (make-result bound) 'value)))))
                 (make-result (struct (value))
                   (lambda (v) (make-result v))))
          (search-loop 0))

        'bound' is reassigned via struct-get (unknown result type), so the
        ASSERT_INTEGER guard on 'bound' is NOT loop-invariant.  It should
        remain in the loop body and be re-executed on every iteration.
        """
        src = """
        (letrec ((result-type (struct (value)))
                 (make-result (lambda (v) (result-type v)))
                 (search-loop
                  (lambda (bound)
                    (if (integer>? bound 100)
                        (list 1)
                        (search-loop (struct-get (make-result bound) 'value))))))
          (search-loop 0))
        """
        code = _compile(src)
        sl = _find_lambda(code, "search-loop")
        assert _count_op(sl, Opcode.ASSERT_INTEGER) == 1
        target = _self_loop_target(sl)
        assert target is not None
        guard_idx = _find_assert(sl, Opcode.ASSERT_INTEGER, 0)
        assert guard_idx is not None
        assert target <= guard_idx, (
            "self-loop must NOT skip the non-loop-invariant ASSERT_INTEGER guard"
        )

    def test_function_without_self_loop_unaffected(self):
        """
        A function without a self-loop should not be modified by the
        hoisting pass.  Guards should remain in their original positions.

        Uses a mutually recursive letrec so that the functions cannot be
        inlined and survive to the CFG stage.  'even?' calls 'odd?' (not
        itself), so it has no self-loop.
        """
        src = """
        (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                 (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (even? 10))
        """
        code = _compile(src)
        even_fn = _find_lambda(code, "even?")
        assert _count_op(even_fn, Opcode.ASSERT_INTEGER) == 1
        assert _self_loop_target(even_fn) is None

    def test_correct_results_after_hoisting(self):
        """
        End-to-end: a self-recursive function that uses list operations
        should produce correct results after guard hoisting.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((sum-list
                  (lambda (lst acc)
                    (if (list-null? lst)
                        acc
                        (sum-list (list-rest lst) (integer+ acc (list-first lst)))))))
          (sum-list (list 1 2 3 4 5) 0))
        """)
        assert result == 15

    def test_correct_results_with_free_var(self):
        """
        End-to-end: a self-recursive function with a free var that is not
        reassigned should produce correct results after guard hoisting.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((count-down
                  (lambda (n limit)
                    (if (integer<=? n limit)
                        n
                        (count-down (integer- n 1) limit)))))
          (count-down 10 0))
        """)
        assert result == 0


class TestGuardHoistingFromNonEntryBlocks:
    """Loop-invariant guards in non-entry blocks should also be hoisted."""

    def test_free_var_guard_in_loop_body_hoisted(self):
        """
        A self-recursive function where a free var is first used inside a
        conditional branch (not the entry block).  The guard on that free var
        is loop-invariant and should be hoisted to the preamble even though it
        is not in the entry block.

        (letrec ((scan
                  (lambda (i)
                    (if (integer>=? i len)
                        i
                        (if (string=? (string-ref s i) "-")
                            (scan (integer+ i 1))
                            (scan (integer+ i 1))))))
                 (len 10)
                 (s "abc"))
          (scan 0))

        's' is a free var (captured), never reassigned.  Its ASSERT_STRING
        guard is inserted in the loop body (after the branch), not in the
        entry block.  It should still be hoisted.
        """
        src = """
        (letrec ((scan
                  (lambda (i)
                    (if (integer>=? i len)
                        i
                        (if (string=? (string-ref s i) "-")
                            (scan (integer+ i 1))
                            (scan (integer+ i 1))))))
                 (len 10)
                 (s "abc"))
          (scan 0))
        """
        code = _compile(src)
        scan = _find_lambda(code, "scan")
        assert _count_op(scan, Opcode.ASSERT_STRING) == 1
        target = _self_loop_target(scan)
        assert target is not None
        # The ASSERT_STRING guard should be before the self-loop target,
        # meaning it has been hoisted to the preamble.
        # scan has 1 param (i=slot0) and 2 free vars (len=slot1, s=slot2).
        # The scan self-capture was eliminated by the dead captures pass.
        guard_idx = _find_assert(scan, Opcode.ASSERT_STRING, 2)
        assert guard_idx is not None
        assert target > guard_idx, (
            "self-loop must skip the hoisted ASSERT_STRING guard on free var 's'"
        )

    def test_correct_results_with_hoisted_body_guard(self):
        """
        End-to-end: a self-recursive function with a loop-invariant guard
        in the loop body should produce correct results after hoisting.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((count-chars
                  (lambda (i)
                    (if (integer>=? i (string-length text))
                        i
                        (count-chars (integer+ i 1)))))
                 (text "hello world"))
          (count-chars 0))
        """)
        assert result == 11
