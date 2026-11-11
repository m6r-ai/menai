"""
Tests for coverage restricted to one source file and split by reachability.

Coverage alone cannot tell an instruction the tests did not reach from one no
execution could reach.  These tests assert that the two are separated, that a
function's outcome follows from that separation, and that filtering to the
module under test excludes the prelude, the runner's own module and the test
module.
"""

import textwrap
from pathlib import Path

import pytest

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai_render.menai_render_instruction import instructions
from menai_test.test_run import run_file
from menai_trace.menai_trace_coverage import (
    CoverageAccumulator,
    FunctionCoverage,
    InstructionCoverage,
)
from menai_trace.menai_trace_reachability import reachable_indices
from menai_trace.menai_trace_render_source import render_cumulative_source_coverage
from menai_trace.menai_trace_source_coverage import (
    STATUS_FULL,
    STATUS_GAP,
    STATUS_REACHABLE_COMPLETE,
    CumulativeSourceCoverage,
    FunctionCoverageOutcome,
    SourceFileCoverage,
    coverage_for_source,
)

_SUBJECT = textwrap.dedent(
    """
    (let ((alpha (lambda (n) (integer+ n 1)))
          (beta (lambda (n) (if (integer>? n 0) "positive" "non-positive")))
          (gamma (lambda (n) (integer* n 2))))
      (export alpha beta gamma))
    """
)

_SUITE = textwrap.dedent(
    """
    (let ((t (import "menai-test"))
          (m (import "probe")))
      (let ((assert-equal (:: t assert-equal))
            (expect-pass (:: t expect-pass))
            (alpha (:: m alpha))
            (beta (:: m beta))
            (gamma (:: m gamma)))
        (let ((tests
               (list
                 (list "values"
                       (list
                         (expect-pass "alpha" (lambda () (assert-equal (alpha 1) 2)))
                         (expect-pass "beta" (lambda () (assert-equal (beta 1) "positive")))
                         (expect-pass "gamma" (lambda () (assert-equal (gamma 2) 4))))))))
          (export tests))))
    """
)


@pytest.fixture
def probe_dir(tmp_path: Path) -> Path:
    """Write a module under test and its suite, returning the directory."""
    (tmp_path / "probe.menai").write_text(_SUBJECT)
    (tmp_path / "probe.test.menai").write_text(_SUITE)
    return tmp_path


def _report(probe_dir: Path, name_filter: str | None = None) -> SourceFileCoverage:
    """Run the probe suite and return the coverage of the module under test."""
    accumulator = CoverageAccumulator()
    run_file(probe_dir / "probe.test.menai", name_filter, coverage=accumulator)
    return coverage_for_source(accumulator.result(), "probe.menai")


def _by_name(report: SourceFileCoverage) -> dict[str, FunctionCoverageOutcome]:
    """Index a report's outcomes by short function name."""
    return {
        outcome.function.code.name.rsplit("#", 1)[-1].split("(")[0]: outcome
        for outcome in report.outcomes
    }


def _instruction(opcode: Opcode, src0: int = 0, src1: int = 0) -> Instruction:
    """Build an instruction with the given opcode and operands."""
    return Instruction(opcode=opcode.value, src0=src0, src1=src1)


def _outcome(code: CodeObject, covered: set[int]) -> FunctionCoverageOutcome:
    """Build an outcome for a code object where the given indices executed."""
    unpacked = tuple(instructions(code))
    function = FunctionCoverage(
        path=(0,),
        ordinal=0,
        code=code,
        calls=1,
        instructions=tuple(
            InstructionCoverage(
                index=index,
                instruction=instruction,
                count=1 if index in covered else 0,
            )
            for index, instruction in enumerate(unpacked)
        ),
    )
    return FunctionCoverageOutcome(function=function, reachable=reachable_indices(code))


def _straight_line() -> CodeObject:
    """Build a three-instruction code object with no branches."""
    return CodeObject(
        instructions=[
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.LOAD_TRUE),
            _instruction(Opcode.RETURN),
        ],
        constants=(),
        code_objects=(),
    )


def _with_dead_code() -> CodeObject:
    """Build a code object whose final instruction follows an unconditional return."""
    return CodeObject(
        instructions=[
            _instruction(Opcode.LOAD_NONE),
            _instruction(Opcode.RETURN),
            _instruction(Opcode.LOAD_NONE),
        ],
        constants=(),
        code_objects=(),
    )


class TestOutcomeStatus:
    """A function's outcome follows from coverage and reachability together."""

    def test_every_instruction_executed_is_full(self):
        outcome = _outcome(_straight_line(), {0, 1, 2})
        assert outcome.status() == STATUS_FULL
        assert outcome.fully_covered()
        assert outcome.reachable_complete()

    def test_unexecuted_reachable_instruction_is_a_gap(self):
        outcome = _outcome(_straight_line(), {0, 2})
        assert outcome.status() == STATUS_GAP
        assert not outcome.fully_covered()
        assert not outcome.reachable_complete()
        assert [instruction.index for instruction in outcome.missed_instructions()] == [1]
        assert outcome.unreachable_instructions() == ()

    def test_unexecuted_unreachable_instruction_is_not_a_gap(self):
        outcome = _outcome(_with_dead_code(), {0, 1})
        assert outcome.status() == STATUS_REACHABLE_COMPLETE
        assert not outcome.fully_covered()
        assert outcome.reachable_complete()
        assert outcome.missed_instructions() == ()
        assert [instruction.index for instruction in outcome.unreachable_instructions()] == [2]

    def test_function_that_never_ran_is_a_gap(self):
        outcome = _outcome(_straight_line(), set())
        assert outcome.status() == STATUS_GAP
        assert [instruction.index for instruction in outcome.missed_instructions()] == [0, 1, 2]

    def test_reachable_coverage_excludes_unreachable_instructions(self):
        outcome = _outcome(_with_dead_code(), {0, 1})
        assert outcome.reachable_instruction_count() == 2
        assert outcome.covered_reachable_instructions() == 2
        assert outcome.reachable_coverage_percent() == 100.0
        assert outcome.function.coverage_percent() < 100.0

    def test_reachable_coverage_counts_missed_instructions(self):
        outcome = _outcome(_straight_line(), {0, 1})
        assert outcome.reachable_coverage_percent() == pytest.approx(200.0 / 3.0)


class TestSourceFileTotals:
    """A source file aggregates its functions' counts."""

    def test_counts_sum_over_functions(self):
        outcomes = (
            _outcome(_straight_line(), {0, 1, 2}),
            _outcome(_straight_line(), {0, 1}),
        )
        report = SourceFileCoverage(source_file="probe.menai", outcomes=outcomes)

        assert report.function_count() == 2
        assert report.total_instructions() == 6
        assert report.covered_instructions() == 5
        assert report.coverage_percent() == pytest.approx(500.0 / 6.0)

    def test_outcomes_partition_the_functions(self):
        outcomes = (
            _outcome(_straight_line(), {0, 1, 2}),
            _outcome(_with_dead_code(), {0, 1}),
            _outcome(_straight_line(), {0, 1}),
        )
        report = SourceFileCoverage(source_file="probe.menai", outcomes=outcomes)

        assert len(report.fully_covered()) == 1
        assert len(report.reachable_complete()) == 1
        assert len(report.gaps()) == 1
        assert not report.all_fully_covered()

    def test_all_fully_covered_when_every_function_is_full(self):
        outcomes = (_outcome(_straight_line(), {0, 1, 2}),)
        report = SourceFileCoverage(source_file="probe.menai", outcomes=outcomes)

        assert report.all_fully_covered()

    def test_unreachable_instruction_count_sums_over_functions(self):
        outcomes = (_outcome(_with_dead_code(), {0, 1}), _outcome(_with_dead_code(), {0, 1}))
        report = SourceFileCoverage(source_file="probe.menai", outcomes=outcomes)

        assert report.unreachable_instruction_count() == 2

    def test_ordering_puts_the_least_covered_first(self):
        outcomes = (
            _outcome(_straight_line(), {0, 1, 2}),
            _outcome(_straight_line(), {0, 1}),
            _outcome(_straight_line(), set()),
        )
        report = SourceFileCoverage(source_file="probe.menai", outcomes=outcomes)

        assert [outcome.covered_reachable_instructions() for outcome in report.ordered()] == [0, 2, 3]

    def test_empty_report_is_fully_covered(self):
        report = SourceFileCoverage(source_file="probe.menai", outcomes=())

        assert report.function_count() == 0
        assert report.coverage_percent() == 100.0
        assert report.reachable_coverage_percent() == 100.0
        assert report.all_fully_covered()


class TestCoverageForSource:
    """Filtering to one source file isolates the module under test."""

    def test_keeps_only_functions_from_the_named_file(self, probe_dir: Path):
        report = _report(probe_dir)

        assert report.source_file == "probe.menai"
        assert report.function_count() > 0
        assert all(
            outcome.function.code.source_file == "probe.menai" for outcome in report.outcomes
        )

    def test_other_sources_are_present_in_the_unfiltered_coverage(self, probe_dir: Path):
        accumulator = CoverageAccumulator()
        run_file(probe_dir / "probe.test.menai", None, coverage=accumulator)

        sources = {function.code.source_file for function in accumulator.result().functions}
        assert "probe.menai" in sources
        assert len(sources) > 1

    def test_reports_full_partial_and_fully_covered_functions(self, probe_dir: Path):
        report = _by_name(_report(probe_dir))

        assert report["alpha"].status() == STATUS_FULL
        assert report["beta"].status() == STATUS_GAP
        assert report["gamma"].status() == STATUS_FULL

    def test_untaken_branch_is_a_reachable_gap(self, probe_dir: Path):
        beta = _by_name(_report(probe_dir))["beta"]

        assert len(beta.missed_instructions()) == 1
        assert beta.unreachable_instructions() == ()
        assert beta.function.calls == 1

    def test_unselected_tests_leave_functions_never_entered(self, probe_dir: Path):
        report = _by_name(_report(probe_dir, "alpha"))

        assert report["alpha"].status() == STATUS_FULL
        for name in ("beta", "gamma"):
            assert report["beta"].status() == STATUS_GAP
            assert report[name].function.calls == 0

    def test_compiled_module_has_no_dead_bytecode(self, probe_dir: Path):
        report = _report(probe_dir)

        assert report.unreachable_instruction_count() == 0

class TestCumulativeSourceCoverage:
    """Merged coverage across suites reports each source file once."""

    def _merged(self) -> CumulativeSourceCoverage:
        """Build merged coverage from a full, a partial and a dead-code-only report."""
        merged = CumulativeSourceCoverage()
        merged.add(
            SourceFileCoverage(source_file="alpha.menai", outcomes=(_outcome(_straight_line(), {0, 1, 2}),))
        )
        merged.add(
            SourceFileCoverage(source_file="beta.menai", outcomes=(_outcome(_straight_line(), {0, 1}),))
        )
        merged.add(
            SourceFileCoverage(source_file="gamma.menai", outcomes=(_outcome(_with_dead_code(), {0, 1}),))
        )
        return merged

    def test_counts_sum_over_reports(self):
        merged = self._merged()

        assert merged.report_count() == 3
        assert merged.function_count() == 3
        assert merged.fully_covered_count() == 1
        assert merged.reachable_complete_count() == 1
        assert merged.gap_count() == 1

    def test_instruction_totals_sum_over_reports(self):
        merged = self._merged()

        assert merged.total_instructions() == 9
        assert merged.covered_instructions() == 7
        assert merged.coverage_percent() == pytest.approx(700.0 / 9.0)
        assert merged.reachable_instruction_count() == 8
        assert merged.covered_reachable_instructions() == 7
        assert merged.reachable_coverage_percent() == pytest.approx(87.5)
        assert merged.unreachable_instruction_count() == 1

    def test_a_file_reported_twice_keeps_the_later_report(self):
        merged = self._merged()
        merged.add(
            SourceFileCoverage(source_file="beta.menai", outcomes=(_outcome(_straight_line(), {0, 1, 2}),))
        )

        assert merged.report_count() == 3
        assert merged.gap_count() == 0

    def test_ordering_puts_files_with_gaps_first(self):
        ordered = self._merged().ordered_reports()

        assert [report.source_file for report in ordered][0] == "beta.menai"

    def test_ordered_gaps_lists_the_worst_function_first(self):
        merged = self._merged()
        merged.add(
            SourceFileCoverage(source_file="delta.menai", outcomes=(_outcome(_straight_line(), set()),))
        )

        assert [len(outcome.missed_instructions()) for outcome in merged.ordered_gaps()] == [3, 1]

    def test_empty_merged_coverage_is_fully_covered(self):
        merged = CumulativeSourceCoverage()

        assert merged.coverage_percent() == 100.0
        assert merged.reachable_coverage_percent() == 100.0
        assert merged.ordered_gaps() == ()


class TestRenderCumulativeSourceCoverage:
    """The roll-up names every module and the worst functions."""

    def _merged(self) -> CumulativeSourceCoverage:
        """Build merged coverage with two modules that each contain a gap."""
        merged = CumulativeSourceCoverage()
        merged.add(
            SourceFileCoverage(source_file="alpha.menai", outcomes=(_outcome(_straight_line(), {0, 1}),))
        )
        merged.add(
            SourceFileCoverage(source_file="beta.menai", outcomes=(_outcome(_straight_line(), set()),))
        )
        return merged

    def test_contains_totals_and_the_module_table(self):
        text = "\n".join(render_cumulative_source_coverage(self._merged(), color=False))

        assert "CUMULATIVE FUNCTION COVERAGE" in text
        assert "Source files:" in text
        assert "With reachable gaps:" in text
        assert "alpha" in text
        assert "beta" in text

    def test_lists_gap_functions_with_their_ranges(self):
        text = "\n".join(render_cumulative_source_coverage(self._merged(), color=False))

        assert "Functions with reachable gaps" in text
        assert "0-2" in text

    def test_top_n_limits_the_gap_list(self):
        text = "\n".join(render_cumulative_source_coverage(self._merged(), color=False, top_n=1))

        assert "worst 1 of 2" in text

    def test_no_gap_heading_when_every_function_is_full(self):
        merged = CumulativeSourceCoverage()
        merged.add(
            SourceFileCoverage(source_file="alpha.menai", outcomes=(_outcome(_straight_line(), {0, 1, 2}),))
        )
        text = "\n".join(render_cumulative_source_coverage(merged, color=False))

        assert "Functions with reachable gaps" not in text
