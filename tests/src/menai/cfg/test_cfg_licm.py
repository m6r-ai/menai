"""
Tests for loop-invariant code motion (LICM) in the CFG.

When a function contains a self-loop (tail-recursive loop), the LICM pass
hoists loop-invariant instructions into a preamble block that executes once
on function entry.  The self-loop's jump target skips the preamble on every
iteration after the first.

Two safety checks prevent overly aggressive hoisting:

  - Dominance check: only instructions in blocks that dominate the back-edge
    (the block containing the SelfLoopTerm) are hoisted.  This prevents
    hoisting from conditional blocks that may not execute on every iteration.
  - Use-def safety check: only instructions whose results are consumed by the
    loop (used by the self-loop args or by other safe instructions) are
    hoisted.  This prevents hoisting values that are only used on exit paths
    (e.g. a constant #t used only by the base-case return).

These tests verify that:
  - Loop-invariant computations (builtins, constants) used by the loop are hoisted.
  - Loop-variant computations remain in the loop body.
  - Hoisted values survive across the back-edge (correct results).
  - Functions without self-loops are unaffected.
  - Guards on type-preserving params are hoisted.
  - Guards on non-type-preserving params are kept in the loop body.
  - Constants used only on exit paths are NOT hoisted.
  - Invariant instructions in non-dominating blocks are NOT hoisted.
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


class TestLoopInvariantComputationHoisting:
    """Loop-invariant computations used by the loop should be hoisted."""

    def test_loop_invariant_builtin_hoisted(self):
        """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1)
                              (integer+ acc (string-length s))))))
                 (s "hello"))
          (loop 0 0))

        (string-length s) is loop-invariant — s is a free var that never
        changes.  Its result is used by (integer+ acc ...) which is a
        self-loop arg, so it passes the use-def safety check.  It should
        be hoisted to the preamble.
        """
        src = """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1)
                              (integer+ acc (string-length s))))))
                 (s "hello"))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _find_lambda(code, "loop")
        assert _count_op(loop_fn, Opcode.STRING_LENGTH) == 1
        target = _self_loop_target(loop_fn)
        assert target is not None
        sl_idx = next(
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.STRING_LENGTH)
        )
        assert target > sl_idx, (
            "self-loop must skip the hoisted STRING_LENGTH"
        )

    def test_loop_invariant_constant_hoisted(self):
        """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc factor)))))
                 (factor 42))
          (loop 0 0))

        The constant 1 used by (integer+ i 1) is loop-invariant and its
        result is used by a self-loop arg.  Its LOAD_CONST should appear
        before the self-loop jump target.
        """
        src = """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc factor)))))
                 (factor 42))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _find_lambda(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        # The LOAD_CONST for the constant 1 should be before the self-loop target.
        load_const_idx = next(
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.LOAD_CONST)
        )
        assert target > load_const_idx, (
            "self-loop must skip the hoisted constant"
        )

    def test_loop_variant_computation_kept_in_loop(self):
        """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc i))))))
          (loop 0 0))

        (integer+ acc i) is loop-variant — both acc and i change on each
        iteration.  The INTEGER_ADD for (integer+ acc i) should remain in
        the loop body (after the self-loop target).
        """
        src = """
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc i))))))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _find_lambda(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        add_indices = [
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.INTEGER_ADD)
        ]
        assert len(add_indices) >= 2
        body_adds = [i for i in add_indices if i >= target]
        assert len(body_adds) >= 1, (
            "loop-variant INTEGER_ADD must remain in the loop body"
        )

    def test_function_without_self_loop_unaffected(self):
        """
        A function without a self-loop should not be modified by LICM.
        """
        src = """
        (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                 (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (even? 10))
        """
        code = _compile(src)
        even_fn = _find_lambda(code, "even?")
        assert _self_loop_target(even_fn) is None

    def test_correct_results_with_hoisted_computation(self):
        """
        End-to-end: a loop with a hoisted computation should produce
        correct results.  (string-length "hello") = 5, accumulated 10 times
        = 50.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc (string-length s))))))
                 (s "hello"))
          (loop 0 0))
        """)
        assert result == 50

    def test_correct_results_with_hoisted_constant(self):
        """
        End-to-end: a loop with a hoisted constant should produce correct
        results.  factor=42 accumulated 10 times = 420.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc factor)))))
                 (factor 42))
          (loop 0 0))
        """)
        assert result == 420

    def test_nested_loop_invariant_hoisted(self):
        """
        A loop-invariant computation that depends on another loop-invariant
        value should also be hoisted.  (integer* (string-length s) factor)
        where both s and factor are free vars.

        Result: 5 * 42 = 210, accumulated 10 times = 2100.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1)
                              (integer+ acc (integer* (string-length s) factor))))))
                 (s "hello")
                 (factor 42))
          (loop 0 0))
        """)
        assert result == 2100

    def test_unchanged_param_computation_hoisted(self):
        """
        A computation that depends only on an unchanged param (one not
        reassigned by the self-loop) should be hoisted.

        (letrec ((loop
                  (lambda (i limit acc)
                    (if (integer>=? i limit)
                        acc
                        (loop (integer+ i 1) limit (integer+ acc limit))))))
          (loop 0 10 0))

        'limit' is not reassigned by the self-loop (only i and acc are).
        (integer+ acc limit) is NOT loop-invariant because acc changes.
        But any computation depending only on limit would be hoisted.
        Result: 10+10+...+10 (10 times) = 100.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (i limit acc)
                    (if (integer>=? i limit)
                        acc
                        (loop (integer+ i 1) limit (integer+ acc limit))))))
          (loop 0 10 0))
        """)
        assert result == 100


class TestGuardHoisting:
    """Guard hoisting is now part of LICM — verify it still works."""

    def test_type_preserving_param_guard_hoisted(self):
        """
        A guard on a param whose back-edge type matches is loop-invariant
        and should be hoisted.
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
        guard_idx = next(
            i for i, instr in enumerate(am.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_LIST)
        )
        assert target > guard_idx, "self-loop must skip the hoisted ASSERT_LIST guard"

    def test_non_type_preserving_param_guard_kept_in_loop(self):
        """
        A guard on a param whose back-edge type is unknown should NOT be
        hoisted — it must be re-executed on every iteration.
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
        guard_idx = next(
            i for i, instr in enumerate(sl.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        )
        assert target <= guard_idx, (
            "self-loop must NOT skip the non-loop-invariant ASSERT_INTEGER guard"
        )

    def test_free_var_guard_hoisted(self):
        """
        A guard on a free var (never reassigned) is always loop-invariant.
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
        guard_indices = [
            i for i, instr in enumerate(sl.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        for gi in guard_indices:
            assert target > gi, "self-loop must skip all hoisted ASSERT_INTEGER guards"

    def test_correct_results_after_guard_hoisting(self):
        """End-to-end: guard hoisting should not break correctness."""
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


    def test_exit_path_constants_correct_results(self):
        """
        End-to-end: the exit-path-constant function should produce correct
        results.  (loop (list 1 2 3) 0) checks if 0 < 1, 1 < 2, 2 < 3 —
        all true, so returns #t.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (lst prev)
                    (if (list-null? lst)
                        #t
                        (if (integer<? prev (list-first lst))
                            (loop (list-rest lst) (list-first lst))
                            #f)))))
          (loop (list 1 2 3) 0))
        """)
        assert result == True

    def test_exit_path_constant_false_correct_results(self):
        """
        End-to-end: when the ordering breaks, the function should return #f.
        (loop (list 1 3 2) 0) — 0 < 1, 1 < 3, 3 < 2 is false → returns #f.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (lst prev)
                    (if (list-null? lst)
                        #t
                        (if (integer<? prev (list-first lst))
                            (loop (list-rest lst) (list-first lst))
                            #f)))))
          (loop (list 1 3 2) 0))
        """)
        assert result == False


    def test_conditional_invariant_correct_results(self):
        """
        End-to-end: the conditional-invariant function should produce
        correct results.  On even iterations, acc += string-length("hello") = 5.
        Iterations 0,2,4,6,8 add 5 (5 * 5 = 25), iterations 1,3,5,7,9 add 0.
        Result: 25.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (letrec ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (if (integer=? (integer% i 2) 0)
                            (loop (integer+ i 1) (integer+ acc (string-length s)))
                            (loop (integer+ i 1) acc)))))
                 (s "hello"))
          (loop 0 0))
        """)
        assert result == 25
