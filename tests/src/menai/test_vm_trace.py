"""
Tests for VM instruction and call tracing.

The C VM assigns each instruction a global ordinal and each code object a code
ordinal, in the canonical walk order defined by menai_render.menai_render_walk.
Execution counts are reported against those ordinals.  These tests verify that
the ordinals the C VM uses agree with the Python walk, and that the counts
attributed to them are correct.
"""

from menai import Menai
from menai_render.menai_render_walk import walk_code_objects
from menai.bytecode.menai_bytecode import Opcode
from menai_render.menai_render_instruction import instructions
from menai_trace.menai_trace_data import resolve_trace


def _run_traced(source: str) -> tuple[Menai, object, list[int], list[int]]:
    """Compile and run source with tracing, returning the instance, code, and raw counts."""
    menai = Menai()
    code = menai.compile(source)
    menai.vm.enable_profiling()
    menai.execute_raw(code)
    instr_counts, call_counts = menai.vm.get_trace_data()
    return menai, code, instr_counts, call_counts


class TestOrdinalAgreement:
    """The C VM's ordinals agree with the Python walk."""

    def test_instruction_count_matches_walk(self):
        _, code, instr_counts, _ = _run_traced("(integer+ 1 2)")
        expected = sum(len(c.instructions) for _, c in _iter_tree(code))
        assert len(instr_counts) == expected

    def test_code_object_count_matches_walk(self):
        _, code, _, call_counts = _run_traced("(integer+ 1 2)")
        assert len(call_counts) == len(list(walk_code_objects(code)))

    def test_counts_land_on_plausible_instructions(self):
        """Every instruction with a non-zero count has an opcode that could execute."""
        _, code, instr_counts, _ = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 3))"
        )
        visits = list(walk_code_objects(code))
        for visit in visits:
            for index, instr in enumerate(instructions(visit.code)):
                count = instr_counts[visit.instr_base + index]
                if count > 0:
                    assert instr.opcode in {op.value for op in Opcode}


def _iter_tree(root):
    """Yield (path, code) pairs for a code tree."""
    for visit in walk_code_objects(root):
        yield visit.path, visit.code


class TestCallCounting:
    """Call counts are attributed to the correct code object."""

    def test_recursive_function_call_count(self):
        """A function recursing from 5 down to 1 is called 5 times."""
        _, code, _, call_counts = _run_traced(
            "(letrec ((fact (lambda (n) (if (integer<=? n 1) 1 (integer* n (fact (integer- n 1))))))) (fact 5))"
        )
        visits = list(walk_code_objects(code))
        by_name = {v.code.name: call_counts[v.code_ordinal] for v in visits}
        assert by_name["fact(1 param)"] == 5

    def test_module_is_called_once(self):
        """The top-level code object reports one call for the module entry."""
        _, code, _, call_counts = _run_traced("(integer+ 1 2)")
        root = next(iter(walk_code_objects(code)))
        assert call_counts[root.code_ordinal] == 1

    def test_uncalled_function_has_zero_calls(self):
        """A function that is never called reports zero calls."""
        _, code, _, call_counts = _run_traced(
            "(let ((unused (lambda (x) x)) (used (lambda (y) y))) (used 1))"
        )
        visits = list(walk_code_objects(code))
        for visit in visits:
            if visit.code.name == "unused(1 param)":
                assert call_counts[visit.code_ordinal] == 0

    def test_module_is_called_exactly_once_for_a_program_calling_functions(self):
        """
        The module reports one call even when the program calls functions.

        A function value is reached through a constant, and the constant's
        code object must be the same native object as the corresponding child
        of the tree.  If the bridge builds a second native instance for the
        constant, that instance is stamped from a fresh counter and every count
        collapses onto ordinal 0, inflating the module's call count.
        """
        _, code, _, call_counts = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 4))"
        )
        root = next(iter(walk_code_objects(code)))
        assert call_counts[root.code_ordinal] == 1

    def test_function_calls_do_not_land_on_the_module(self):
        """
        A function's calls are attributed to it, not to the module.

        The function is not tail-recursive, so each recursive step is a real
        call rather than a loop the compiler folds the tail call into.
        """
        _, code, _, call_counts = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (integer+ n (f (integer- n 1))))))) (f 4))"
        )
        visits = list(walk_code_objects(code))
        root = visits[0]
        assert call_counts[root.code_ordinal] == 1
        assert sum(call_counts) == 6

    def test_module_instructions_execute_once_each(self):
        """The module's own instructions each execute exactly once."""
        _, code, instr_counts, _ = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (integer+ n (f (integer- n 1))))))) (f 4))"
        )
        root = next(iter(walk_code_objects(code)))
        counts = instr_counts[root.instr_base:root.instr_base + root.instruction_count]
        assert all(count == 1 for count in counts)

    def test_self_tail_call_is_folded_into_a_loop(self):
        """
        A self-tail-call is compiled to a loop, so it is one call, not many.

        The compiler folds the recursive tail call into a backward jump, so the
        function is entered once and loops internally.  The call count reflects
        that: one entry, many iterations.
        """
        _, code, _, call_counts = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 4))"
        )
        visits = list(walk_code_objects(code))
        by_name = {v.code.name: call_counts[v.code_ordinal] for v in visits}
        assert by_name["f(1 param)"] == 1


class TestInstructionCounting:
    """Instruction counts are attributed to the correct instruction."""

    def test_straight_line_executes_each_instruction_once(self):
        """A module with no branches executes each of its instructions once."""
        _, code, instr_counts, _ = _run_traced("(integer+ 1 2)")
        root = next(iter(walk_code_objects(code)))
        counts = instr_counts[root.instr_base:root.instr_base + root.instruction_count]
        assert all(count == 1 for count in counts)

    def test_total_matches_sum(self):
        """The sum of instruction counts is the total instructions executed."""
        _, _, instr_counts, _ = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 4))"
        )
        assert sum(instr_counts) > 0


class TestTraceDisabled:
    """Tracing is off unless explicitly enabled."""

    def test_no_trace_without_enabling(self):
        menai = Menai()
        code = menai.compile("(integer+ 1 2)")
        menai.execute_raw(code)
        instr_counts, call_counts = menai.vm.get_trace_data()
        assert instr_counts == []
        assert call_counts == []

    def test_trace_reflects_single_run(self):
        """Enabling and running once produces counts for that run only."""
        menai = Menai()
        code = menai.compile("(integer+ 1 2)")
        menai.vm.enable_profiling()
        menai.execute_raw(code)
        first, _ = menai.vm.get_trace_data()
        menai.execute_raw(code)
        second, _ = menai.vm.get_trace_data()
        assert first == second


class TestResolveTrace:
    """The Python resolver turns ordinals into per-instruction records."""

    def test_resolve_produces_one_function_per_code_object(self):
        _, code, instr_counts, call_counts = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 2))"
        )
        result = resolve_trace(code, instr_counts, call_counts)
        assert len(result.functions) == len(list(walk_code_objects(code)))

    def test_resolve_totals_match_raw_counts(self):
        _, code, instr_counts, call_counts = _run_traced(
            "(letrec ((f (lambda (n) (if (integer<=? n 0) 0 (f (integer- n 1)))))) (f 2))"
        )
        result = resolve_trace(code, instr_counts, call_counts)
        assert result.total_instructions() == sum(instr_counts)
        assert result.total_calls() == sum(call_counts)

    def test_resolve_rejects_mismatched_instruction_array(self):
        _, code, instr_counts, call_counts = _run_traced("(integer+ 1 2)")
        try:
            resolve_trace(code, instr_counts[:-1], call_counts)

        except ValueError:
            return

        raise AssertionError("expected ValueError for mismatched instruction array")

    def test_resolve_rejects_mismatched_call_array(self):
        _, code, instr_counts, call_counts = _run_traced("(integer+ 1 2)")
        try:
            resolve_trace(code, instr_counts, call_counts[:-1])

        except ValueError:
            return

        raise AssertionError("expected ValueError for mismatched call array")
