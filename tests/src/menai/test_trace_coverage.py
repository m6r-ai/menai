"""
Tests for bytecode coverage.

Coverage is derived from the same per-instruction trace as profiling, so the
tests assert the property that distinguishes it: instructions with a zero count
are reported as uncovered, including every instruction of a code object that was
compiled but never entered.  Coverage is measured over the compiled tree, so a
function that optimisation removed is not part of the denominator.
"""

import re
from dataclasses import replace

from menai import Menai
from menai_trace.menai_trace_coverage import (
    CoverageResult,
    CumulativeCoverage,
    FunctionKey,
    coverage_from_counts,
    coverage_from_trace,
    function_key,
)
from menai_trace.menai_trace_data import resolve_trace
from menai_trace.menai_trace_render import render_coverage, render_cumulative_coverage


def _coverage(source: str) -> CoverageResult:
    """Compile and run source with tracing, then derive its coverage."""
    menai = Menai()
    code = menai.compile(source)
    menai.vm.enable_profiling()
    menai.execute_raw(code)
    instr_counts, call_counts = menai.vm.get_trace_data()
    return coverage_from_counts(code, instr_counts, call_counts)


_BRANCH_SOURCE = """
(let ((f (lambda (x) (if (integer>? x 0) 1 2))))
  (apply f (list 5)))
"""

_UNENTERED_SOURCE = """
(let ((f (lambda (x) (integer+ x 1)))
      (g (lambda (y) (integer* y 2))))
  (list f g))
"""

_LONG_NAME_SOURCE = """
(let ((a-very-long-function-name-that-is-over-forty-characters
       (lambda (x) (integer+ x 1))))
  (list a-very-long-function-name-that-is-over-forty-characters))
"""


class TestCoverageTotals:
    """Covered and uncovered instruction counts partition the compiled tree."""

    def test_counts_partition_the_tree(self):
        coverage = _coverage(_BRANCH_SOURCE)
        assert coverage.covered_instructions() + coverage.uncovered_instructions() == coverage.total_instructions()

    def test_coverage_percent_matches_counts(self):
        coverage = _coverage(_BRANCH_SOURCE)
        expected = coverage.covered_instructions() / coverage.total_instructions() * 100.0
        assert coverage.coverage_percent() == expected

    def test_instructions_cover_every_instruction_in_each_function(self):
        coverage = _coverage(_BRANCH_SOURCE)
        for function in coverage.functions:
            assert function.covered_instructions() + len(function.uncovered_instructions()) == function.total_instructions()


class TestBranchCoverage:
    """An untaken branch leaves its instructions uncovered."""

    def test_untaken_branch_leaves_instructions_uncovered(self):
        coverage = _coverage(_BRANCH_SOURCE)
        assert coverage.uncovered_instructions() > 0
        assert coverage.coverage_percent() < 100.0

    def test_partially_covered_function_reports_its_uncovered_instruction(self):
        coverage = _coverage(_BRANCH_SOURCE)
        function = next(fn for fn in coverage.functions if fn.coverage_percent() < 100.0)
        assert function.covered_instructions() > 0
        assert len(function.uncovered_instructions()) >= 1
        assert all(not instruction.covered() for instruction in function.uncovered_instructions())

    def test_module_is_fully_covered(self):
        coverage = _coverage(_BRANCH_SOURCE)
        module = coverage.functions[0]
        assert module.fully_covered()
        assert module.coverage_percent() == 100.0


class TestUnenteredFunctions:
    """A compiled function that is never called has no covered instructions."""

    def test_uncalled_functions_are_not_entered(self):
        coverage = _coverage(_UNENTERED_SOURCE)
        names = {function.code.name for function in coverage.never_entered()}
        assert any("f(" in (name or "") for name in names)
        assert any("g(" in (name or "") for name in names)

    def test_unentered_functions_report_zero_coverage(self):
        coverage = _coverage(_UNENTERED_SOURCE)
        for function in coverage.never_entered():
            assert function.coverage_percent() == 0.0
            assert function.calls == 0
            assert function.covered_instructions() == 0

    def test_only_the_module_is_entered(self):
        coverage = _coverage(_UNENTERED_SOURCE)
        assert coverage.entered_function_count() == 1
        assert coverage.function_count() > coverage.entered_function_count()


class TestCoverageFromTrace:
    """coverage_from_trace preserves the trace's per-instruction counts."""

    def test_counts_are_preserved(self):
        menai = Menai()
        code = menai.compile(_BRANCH_SOURCE)
        menai.vm.enable_profiling()
        menai.execute_raw(code)
        instr_counts, call_counts = menai.vm.get_trace_data()
        trace = resolve_trace(code, instr_counts, call_counts)
        coverage = coverage_from_trace(trace)

        for function, trace_function in zip(coverage.functions, trace.functions):
            assert [instruction.count for instruction in function.instructions] == [
                instruction.count for instruction in trace_function.instructions
            ]


class TestRenderCoverage:
    """The coverage renderer summarises the totals and names unexecuted bytecode."""

    def test_long_function_label_is_not_truncated_by_default(self):
        text = "\n".join(render_coverage(_coverage(_LONG_NAME_SOURCE), color=False))
        assert "a-very-long-function-name-that-is-over-forty-characters" in text
        assert "..." not in text

    def test_contains_totals(self):
        text = "\n".join(render_coverage(_coverage(_BRANCH_SOURCE), color=False))
        assert "BYTECODE COVERAGE" in text
        assert "Total instructions:" in text
        assert "Executed:" in text
        assert "Not executed:" in text
        assert "Code objects:" in text

    def test_contains_each_function(self):
        text = "\n".join(render_coverage(_coverage(_UNENTERED_SOURCE), color=False))
        assert "f [" in text
        assert "g [" in text

    def test_names_unexecuted_instruction_ranges(self):
        text = "\n".join(render_coverage(_coverage(_UNENTERED_SOURCE), color=False))
        assert "Unexecuted instructions" in text
        assert "0-2" in text

    def test_reports_no_unexecuted_instructions_when_fully_covered(self):
        coverage = _coverage("(integer+ 1 2)")
        text = "\n".join(render_coverage(coverage, color=False))
        assert "  None" in text

    def test_top_n_limits_the_function_table(self):
        coverage = _coverage(_UNENTERED_SOURCE)
        lines = render_coverage(coverage, color=False, top_n=1)
        table_rows = [
            line for line in lines
            if re.match(r"\s*[\d,]+ +[\d,]+ +[\d.]+% +[\d,]+", line)
        ]
        assert len(table_rows) == 1


def _named_coverage(source: str) -> CoverageResult:
    """Compile source with a source file name so cumulative keys are stable."""
    menai = Menai()
    code = menai.compiler.compile(source, name="coverage-test.menai")
    menai.vm.enable_profiling()
    menai.execute_raw(code)
    instr_counts, call_counts = menai.vm.get_trace_data()
    return coverage_from_counts(code, instr_counts, call_counts)


class TestFunctionKey:
    """Function identity is stable across independently compiled programs."""

    def test_strips_module_index_and_parameter_suffix(self):
        code = _named_coverage(_UNENTERED_SOURCE).functions[1].code
        renamed = replace(code, name="json#3#decode(1 param)", source_file="json.menai", source_line=12)
        assert function_key(renamed) == FunctionKey("json.menai", 12, "decode")

    def test_normalises_anonymous_lambda_number(self):
        code = _named_coverage(_UNENTERED_SOURCE).functions[1].code
        renamed = replace(code, name="<lambda-7>", source_file="x.menai", source_line=3)
        assert function_key(renamed).name == "<lambda>"


class TestCumulativeCoverage:
    """Cumulative coverage counts each function once across programs."""

    def test_merging_the_same_program_twice_is_idempotent(self):
        coverage = _named_coverage(_UNENTERED_SOURCE)
        cumulative = CumulativeCoverage()
        cumulative.add(coverage)

        total = cumulative.total_instructions()
        covered = cumulative.covered_instructions()

        cumulative.add(coverage)
        assert cumulative.total_instructions() == total
        assert cumulative.covered_instructions() == covered

    def test_functions_without_a_source_file_are_skipped(self):
        coverage = _named_coverage(_UNENTERED_SOURCE)
        cumulative = CumulativeCoverage()
        cumulative.add(coverage)

        assert all(function.key.source_file for function in cumulative.functions.values())
        assert cumulative.function_count() == sum(1 for function in coverage.functions if function.code.source_file)

    def test_merged_coverage_does_not_exceed_one_hundred_percent(self):
        coverage = _named_coverage(_UNENTERED_SOURCE)
        cumulative = CumulativeCoverage()
        cumulative.add(coverage)
        cumulative.add(coverage)

        assert 0.0 <= cumulative.coverage_percent() <= 100.0


class TestRenderCumulativeCoverage:
    """The cumulative renderer reports the merged totals and unexecuted code."""

    def test_contains_title_and_totals(self):
        cumulative = CumulativeCoverage()
        cumulative.add(_named_coverage(_UNENTERED_SOURCE))
        text = "\n".join(render_cumulative_coverage(cumulative, color=False))

        assert "CUMULATIVE BYTECODE COVERAGE" in text
        assert "Total instructions:" in text
        assert "Executed:" in text
        assert "Unexecuted instructions" in text

    def test_lists_unexecuted_function(self):
        cumulative = CumulativeCoverage()
        cumulative.add(_named_coverage(_UNENTERED_SOURCE))
        text = "\n".join(render_cumulative_coverage(cumulative, color=False))

        assert "f [" in text
        assert "0-2" in text
