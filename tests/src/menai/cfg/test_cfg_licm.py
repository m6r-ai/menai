"""
Tests for loop-invariant code motion (LICM) in the CFG.

When a function contains a self-loop (tail-recursive loop), the LICM pass
hoists loop-invariant instructions into a preamble block that executes once
on function entry.  The self-loop's jump target skips the preamble on every
iteration after the first.

Two rules govern what may be hoisted:

  - An instruction is hoisted only if it is loop-invariant: all of its operands
    are invariant, where constants, free vars, and params the loop does not
    reassign are invariant.
  - An instruction that can raise is hoisted only from a block that executes on
    every iteration of the loop, i.e. one that dominates every back-edge block.
    Hoisting a raising instruction from a block that does not execute on every
    iteration would make it execute on iterations that previously skipped it,
    which can make a program raise where it previously completed (ADR-0049).

These tests verify that:
  - Loop-invariant computations (builtins, constants) used by the loop are hoisted.
  - Loop-variant computations remain in the loop body.
  - Hoisted values survive across the back-edge (correct results).
  - Functions without self-loops are unaffected.
  - Guards on params whose type is preserved across the back-edge are hoisted.
  - Guards on params whose type is not preserved are kept in the loop body.
  - Raising instructions in blocks that do not execute on every iteration are
    NOT hoisted.
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


def _loop_function(code, name: str):
    """
    Return the code object holding the self-loop named `name`.

    A self-recursive letrec whose recursion is a tail call is lowered to a
    loop and then inlined into its caller, so the loop may not exist as a
    separate code object.  When no code object is named `name`, the code
    object that actually contains the loop (a backward jump) is returned.
    """
    queue = [code]
    while queue:
        co = queue.pop(0)
        if name in co.name:
            return co
        queue.extend(co.code_objects)

    queue = [code]
    while queue:
        co = queue.pop(0)
        if _self_loop_target(co) is not None:
            return co
        queue.extend(co.code_objects)

    return code


def _self_loop_target(code) -> int | None:
    """
    Return the instruction index of the loop entry point (the target of the
    back-edge), or None if there is no loop in the function.

    Handles both loop shapes: an unrotated loop ends with an unconditional
    backward JUMP, while a rotated loop ends with a conditional backward
    jump (the rotated test) back into the body.
    """
    for i, instr in enumerate(code.instructions):
        op = unpack_instruction(instr)
        if op.opcode == int(Opcode.JUMP) and op.src0 < i:
            return op.src0

        if op.opcode in (int(Opcode.JUMP_IF_FALSE), int(Opcode.JUMP_IF_TRUE)) and op.src1 < i:
            return op.src1

    return None


class TestLoopInvariantComputationHoisting:
    """Loop-invariant computations used by the loop should be hoisted."""

    def test_loop_invariant_builtin_hoisted(self):
        """
        (let ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1)
                              (integer+ acc (vector-length v))))))
                 (v (vector 1 2 3)))
          (loop 0 0))

        (vector-length v) is loop-invariant — v is a free var that never
        changes.  Its result is used by (integer+ acc ...) which is a
        self-loop arg, so it passes the use-def safety check.  It should
        be hoisted to the preamble.

        The builtin must be one that is never constant-folded.  A vector has
        no literal form, so (vector-length ...) always remains an instruction;
        a foldable builtin such as (string-length "hello") is folded away and
        leaves nothing for this test to observe.
        """
        src = """
        (let ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1)
                              (integer+ acc (vector-length v))))))
                 (v (vector 1 2 3)))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
        assert _count_op(loop_fn, Opcode.VECTOR_LENGTH) == 1
        target = _self_loop_target(loop_fn)
        assert target is not None
        sl_idx = next(
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.VECTOR_LENGTH)
        )
        assert target > sl_idx, (
            "self-loop must skip the hoisted VECTOR_LENGTH"
        )

    def test_loop_invariant_constant_hoisted(self):
        """
        (let ((loop
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
        (let ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc factor)))))
                 (factor 42))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
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
        (let ((loop
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
        (let ((loop
                  (lambda (i acc)
                    (if (integer>=? i 10)
                        acc
                        (loop (integer+ i 1) (integer+ acc i))))))
          (loop 0 0))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
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

    def test_loop_invariant_builtin_on_outer_value_hoisted(self):
        """
        (lambda (b)
          (let ((loop (lambda (i acc)
                           (if (integer>=? i (bytes-length b))
                               acc
                               (loop (integer+ i 1)
                                     (integer+ acc (bytes-nth b i)))))))
            (loop 0 0)))

        The loop is lowered to a loop-entry block with a pre-header.  `b` is
        a param the loop does not reassign, so `(bytes-length b)` is
        loop-invariant and must be hoisted out of the loop header (only the
        INTEGER_GTE_P test should remain in the loop).  In particular, loop
        rotation must not duplicate the hoisted computation into the rotated
        test.
        """
        src = """
        (lambda (b)
          (let ((loop (lambda (i acc)
                           (if (integer>=? i (bytes-length b))
                               acc
                               (loop (integer+ i 1)
                                     (integer+ acc (bytes-nth b i)))))))
            (loop 0 0)))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        # BYTES_LENGTH appears once, before the loop entry, and the loop does
        # not re-execute it (the rotated test does not duplicate it).
        length_indices = [
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.BYTES_LENGTH)
        ]
        assert len(length_indices) == 1, (
            "loop-invariant BYTES_LENGTH must be hoisted exactly once"
        )
        assert target > length_indices[0], (
            "self-loop must skip the hoisted BYTES_LENGTH"
        )

    def test_function_without_self_loop_unaffected(self):
        """
        A function without a self-loop should not be modified by LICM.
        """
        src = """
        (let ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
                 (odd? (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (even? 10))
        """
        code = _compile(src)
        even_fn = _loop_function(code, "even?")
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
        (let ((loop
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
        (let ((loop
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
        (let ((loop
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

        (let ((loop
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
        (let ((loop
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
        A guard on a param reassigned by the back-edge is not hoisted.

        The back-edge type proves the guard holds from the second iteration
        onwards, but the first iteration's value is whatever the caller
        passed, which the back-edge says nothing about.  The guard therefore
        stays in the loop entry, where it checks the first iteration.

        'moves' is fed by 'flip', whose return type is ambiguous (it can
        return an integer or a list), so the parameter's type is not provable
        and the ASSERT_LIST guard is inserted.
        """
        src = """
        (let ((apply-moves
                  (lambda (cube moves)
                    (if (list-null? moves)
                        cube
                        (apply-moves (apply-move cube (list-first moves))
                                     (list-rest moves)))))
                 (apply-move (lambda (cube move) move))
                 (flip (lambda (n) (if (integer<=? n 0) 0 (flop (integer- n 1)))))
                 (flop (lambda (n) (if (integer<=? n 0) (list 1) (flip (integer- n 1))))))
          (apply-moves 0 (flip 3)))
        """
        code = _compile(src)
        am = _loop_function(code, "apply-moves")
        target = _self_loop_target(am)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(am.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_LIST)
        ]
        assert guard_indices, "the ASSERT_LIST guard must be present"
        assert any(gi >= target for gi in guard_indices), (
            "a guard on a reassigned param must be re-checked inside the "
            "loop, because the back-edge says nothing about the first "
            "iteration's value"
        )

    def test_non_type_preserving_param_guard_kept_in_loop(self):
        """
        A guard on a param whose back-edge type is unknown should NOT be
        hoisted — it must be re-executed on every iteration.
        """
        src = """
        (let ((result-type (struct (value)))
                 (make-result (lambda (v) (result-type v)))
                 (search-loop
                  (lambda (bound)
                    (if (integer>? bound 100)
                        (list 1)
                        (search-loop (struct-get (make-result bound) 'value))))))
          (search-loop 0))
        """
        code = _compile(src)
        sl = _loop_function(code, "search-loop")
        # The guard is not hoisted out of the loop.  The loop is rotated, so
        # the guard appears twice: once in the entry test and once in the
        # rotated test at the bottom of the loop.  Both execute inside the
        # loop, so the guard is re-checked on every iteration.
        assert _count_op(sl, Opcode.ASSERT_INTEGER) == 2
        target = _self_loop_target(sl)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(sl.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        assert any(gi >= target for gi in guard_indices), (
            "the non-loop-invariant ASSERT_INTEGER guard must be re-executed "
            "inside the loop"
        )

    def test_free_var_guard_hoisted(self):
        """
        A guard on a param the self-loop passes through unchanged is
        loop-invariant and is hoisted into the preamble.

        'max-depth' is handed back to itself on the back-edge, so its value
        never changes.  'bound' is reassigned, so its guard cannot be hoisted
        and stays in the loop.

        Both params are fed by 'flip', whose return type is ambiguous, so
        their types are not provable and the ASSERT_INTEGER guards are
        inserted.
        """
        src = """
        (let ((search-loop
                  (lambda (bound max-depth)
                    (if (integer>? bound max-depth)
                        (list 1)
                        (search-loop (integer+ bound 1) max-depth))))
                 (flip (lambda (n) (if (integer<=? n 0) 0 (flop (integer- n 1)))))
                 (flop (lambda (n) (if (integer<=? n 0) (list 1) (flip (integer- n 1))))))
          (search-loop (flip 3) (flip 4)))
        """
        code = _compile(src)
        sl = _loop_function(code, "search-loop")
        target = _self_loop_target(sl)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(sl.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        hoisted = [gi for gi in guard_indices if gi < target]
        in_loop = [gi for gi in guard_indices if gi >= target]
        assert hoisted, "the max-depth guard must be hoisted into the preamble"
        assert in_loop, (
            "the bound guard must stay inside the loop, because the "
            "back-edge says nothing about the first iteration's value"
        )

    def test_correct_results_after_guard_hoisting(self):
        """End-to-end: guard hoisting should not break correctness."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (let ((sum-list
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
        (let ((loop
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
        (let ((loop
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
        (let ((loop
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
    def test_guard_in_non_dominating_block_not_hoisted(self):
        """
        A guard that does not execute on every iteration is not hoisted.

        The guard is inserted before `list-prepend`, which sits in the `then`
        branch of the inner if.  The `else` branch reaches the loop back-edge
        without passing through that block, so the guard does not dominate
        every back-edge and does not execute on every iteration.

        A guard can raise, so hoisting it would make it execute on iterations
        that previously skipped it, which can make a program raise where it
        previously completed (ADR-0049).  The guard must therefore stay in the
        loop body.

        The guard is on `acc`, which is fed by `flip`, whose return type is
        ambiguous, so `acc`'s type is not provable and the ASSERT_LIST guard is
        inserted.  A second ASSERT_LIST guard is emitted on the exit path
        before list->vector; it is outside the loop and is not hoisted either.
        """
        src = """
        (let ((loop
                  (lambda (i acc)
                    (if (integer<? i 0)
                        (list->vector acc)
                        (let ((elem (vector-nth v i)))
                          (loop (integer- i 1)
                                (if (integer=? (integer% i 2) 0)
                                    (list-prepend acc elem)
                                    acc))))))
                 (v (vector 10 20 30 40 50))
                 (flip (lambda (n) (if (integer<=? n 0) 0 (flop (integer- n 1)))))
                 (flop (lambda (n) (if (integer<=? n 0) (list 1) (flip (integer- n 1))))))
          (loop (integer- (vector-length v) 1) (flip 3)))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_LIST)
        ]
        assert guard_indices, "the ASSERT_LIST guard must be present"
        hoisted = [gi for gi in guard_indices if gi < target]
        assert not hoisted, (
            "a guard that does not execute on every iteration must not be "
            "hoisted into the preamble"
        )

    def test_phi_back_edge_guard_correct_results(self):
        """
        End-to-end: a loop with a phi-based back-edge for acc should
        produce correct results after guard hoisting.

        Elements at even indices (0, 2, 4) are 10, 30, 50.
        The loop goes from index 4 down to 0, prepending even-index elements.
        Result: [10, 30, 50].
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (let ((loop
                  (lambda (i acc)
                    (if (integer<? i 0)
                        (list->vector acc)
                        (let ((elem (vector-nth v i)))
                          (loop (integer- i 1)
                                (if (integer=? (integer% i 2) 0)
                                    (list-prepend acc elem)
                                    acc))))))
                 (v (vector 10 20 30 40 50)))
          (loop (integer- (vector-length v) 1) (list)))
        """)
        assert str(result) == "[10, 30, 50]"


class TestRaisingInstructionHoisting:
    """
    A raising instruction is hoisted only from a block that executes on every
    iteration.  Hoisting one from a block that does not would make it execute
    on iterations that previously skipped it, which can make a program raise
    where it previously completed (ADR-0049).
    """

    def test_raising_instruction_in_non_dominating_block_not_hoisted(self):
        """
        The division sits behind (integer>? n 100), which is never true for
        n in 0..3, so it never executes.  Its operands are loop-invariant
        (100 is a constant and d is a free var), so it is a candidate for
        hoisting, but its block does not execute on every iteration.  It must
        stay in the loop body.
        """
        src = """
        (let ((d 0))
          (let ((loop (lambda (n)
                           (if (integer=? n 0)
                               42
                               (if (integer>? n 100)
                                   (loop (integer- n (integer/ 100 d)))
                                   (loop (integer- n 1)))))))
            (loop 3)))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        div_indices = [
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.INTEGER_DIV)
        ]
        assert div_indices, "the INTEGER_DIV must be present"
        hoisted = [di for di in div_indices if di < target]
        assert not hoisted, (
            "a raising instruction in a block that does not execute on every "
            "iteration must not be hoisted into the preamble"
        )

    def test_raising_instruction_not_executed_correct_results(self):
        """
        End-to-end: the guarded division never executes, so the program
        returns 42.  Before the fix, LICM hoisted the division into the
        preamble, where it executed unconditionally on entry and raised
        division by zero.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (let ((d 0))
          (let ((loop (lambda (n)
                           (if (integer=? n 0)
                               42
                               (if (integer>? n 100)
                                   (loop (integer- n (integer/ 100 d)))
                                   (loop (integer- n 1)))))))
            (loop 3)))
        """)
        assert result == 42

    def test_raising_instruction_on_every_iteration_hoisted(self):
        """
        A raising instruction whose block executes on every iteration may be
        hoisted: it already ran on the first iteration, so hoisting it to the
        preamble cannot introduce a raise on an iteration that did not
        previously execute it.

        (integer/ 100 d) is an operand of the self-loop argument, so its block
        is on the back-edge path and executes on every iteration.
        """
        src = """
        (let ((d 1))
          (let ((loop (lambda (n acc)
                           (if (integer=? n 0)
                               acc
                               (loop (integer- n 1) (integer+ acc (integer/ 100 d)))))))
            (loop 3 0)))
        """
        code = _compile(src)
        loop_fn = _loop_function(code, "loop")
        target = _self_loop_target(loop_fn)
        assert target is not None
        div_indices = [
            i for i, instr in enumerate(loop_fn.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.INTEGER_DIV)
        ]
        assert len(div_indices) == 1, "the INTEGER_DIV must be hoisted exactly once"
        assert target > div_indices[0], (
            "self-loop must skip the hoisted INTEGER_DIV"
        )

    def test_raising_instruction_on_every_iteration_correct_results(self):
        """
        End-to-end: hoisting the division is sound here because it executes on
        every iteration.  100 / 1 = 100, accumulated 3 times = 300.
        """
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (let ((d 1))
          (let ((loop (lambda (n acc)
                           (if (integer=? n 0)
                               acc
                               (loop (integer- n 1) (integer+ acc (integer/ 100 d)))))))
            (loop 3 0)))
        """)
        assert result == 300


class TestLoopCarriedValueGuardHoisting:
    """
    A guard on a loop-carried value is rewritten, not moved.

    A MenaiIRLoop carries its variables in phi nodes in the loop header, not in
    the function's params.  A guard on such a phi cannot be hoisted: the phi is
    assigned in the header, so any block the guard could move to precedes that
    assignment and would read the phi's register before it is written.

    The guard is redundant instead.  The phi holds its initial value on the
    first iteration and the back-edge value on every later one, so when the
    back-edge value already has the guarded type, only the first iteration
    needs checking.  Guarding the phi's initial value in the preamble does
    that once, and the guard on the phi is dropped.
    """

    def test_guard_on_loop_carried_phi_rewritten_into_preamble(self):
        """
        The guard on the loop-carried `pos` is replaced by a guard on `start`,
        the value `pos` holds on the first iteration, placed in the preamble.

        `start` is fed by `struct-get`, whose result type is not provable, so
        the parameter's type is unknown and the ASSERT_INTEGER guard is
        inserted.
        """
        src = """
        (let ((scan
                  (lambda (s start)
                    (let ((loop
                           (lambda (pos acc)
                             (if (integer>=? pos (string-length s))
                                 acc
                                 (loop (integer+ pos 1)
                                       (integer+ acc 1))))))
                      (loop start 0))))
              (result-type (struct (value)))
              (make-result (lambda (v) (result-type v))))
          (scan "abc" (struct-get (make-result 0) 'value)))
        """
        code = _compile(src)
        scan = _loop_function(code, "scan")
        target = _self_loop_target(scan)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(scan.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        assert len(guard_indices) == 1, (
            "the guard on the loop-carried value must be replaced by a single "
            "guard in the preamble"
        )
        assert guard_indices[0] < target, (
            "the rewritten guard must sit in the preamble, before the loop "
            "entry, so the back-edge skips it"
        )

    def test_guard_on_loop_carried_phi_correct_results(self):
        """End-to-end: rewriting the guard must not change the result."""
        from menai import Menai
        menai = Menai()

        result = menai.evaluate("""
        (let ((scan
                  (lambda (s start)
                    (let ((loop
                           (lambda (pos acc)
                             (if (integer>=? pos (string-length s))
                                 acc
                                 (loop (integer+ pos 1)
                                       (integer+ acc 1))))))
                      (loop start 0))))
              (result-type (struct (value)))
              (make-result (lambda (v) (result-type v))))
          (scan "abc" (struct-get (make-result 0) 'value)))
        """)
        assert result == 3

    def test_guard_in_header_not_hoisted_above_phi(self):
        """
        A guard whose value's back-edge type is unknown is neither hoisted nor
        rewritten, so it stays in the loop and is re-checked each iteration.

        `bound` is reassigned by the back-edge to the result of a `struct-get`,
        whose type is not provable.  The back-edge therefore does not preserve
        the guarded type, so the guard cannot be reduced to a single check on
        the first iteration.
        """
        src = """
        (let ((search-loop
                  (lambda (bound)
                    (if (integer>? bound 100)
                        (list 1)
                        (search-loop (struct-get (make-result bound) 'value)))))
              (result-type (struct (value)))
              (make-result (lambda (v) (result-type v))))
          (search-loop 0))
        """
        code = _compile(src)
        sl = _loop_function(code, "search-loop")
        target = _self_loop_target(sl)
        assert target is not None
        guard_indices = [
            i for i, instr in enumerate(sl.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        assert guard_indices, "the ASSERT_INTEGER guard must be present"
        assert any(gi >= target for gi in guard_indices), (
            "a guard on a value the loop header defines must not be hoisted "
            "above its definition"
        )


class TestInvarianceThroughIndexedStructGet:
    """
    Invariance must propagate through `struct-get` lowered to an indexed read.

    The interprocedural type analysis rewrites a name-based `struct-get` whose
    field index it has resolved into `MenaiCFGStructGetIndexedInstr`.  That
    instruction is emitted by the analysis rather than by the CFG builder, so
    it appears only in functions compiled with the analysis enabled.

    If it is missing from the hoistable set, invariance stops at it: the
    struct-get is treated as loop-variant, so every value computed from it is
    loop-variant too, and a guard on such a value is left in the loop even
    though the value it guards is loop-invariant.  The guard then re-executes
    on every iteration.
    """

    def test_guard_on_value_derived_from_indexed_struct_get_hoisted(self):
        """
        A guard on a value read out of a struct field is hoisted.

        The rubiks-cube benchmark reads a face out of the cube struct and then
        reads the face's centre sticker.  Both reads are loop-invariant, so the
        guard on the centre sticker belongs in the preamble, above the loop
        entry, and must not re-execute on every iteration.

        The benchmark module is used rather than a hand-written equivalent
        because the rewrite to an indexed read only fires when the analysis can
        prove the struct's type at the call site, which a synthetic program
        does not reproduce reliably.
        """
        from pathlib import Path

        from menai import Menai

        suite_dir = (
            Path(__file__).resolve().parents[4]
            / "src" / "menai_benchmark" / "suites" / "rubiks_vector"
        )
        menai = Menai(module_path=[str(suite_dir)])
        code = menai.compile(
            '(let ((rubiks (import "rubiks-cube-vector")))'
            '  (let ((solved-cube-fn (:: rubiks solved-cube))'
            '        (ida-star-fn (:: rubiks ida-star)))'
            '    (ida-star-fn (solved-cube-fn) 3)))'
        )

        solved = _loop_function(code, "cube-solved?")
        target = _self_loop_target(solved)
        assert target is not None

        guard_indices = [
            i for i, instr in enumerate(solved.instructions)
            if unpack_instruction(instr).opcode == int(Opcode.ASSERT_INTEGER)
        ]
        assert guard_indices, "the ASSERT_INTEGER guard must be present"
        assert any(gi < target for gi in guard_indices), (
            "a guard on a value derived from a loop-invariant struct field "
            "must be hoisted into the preamble"
        )
