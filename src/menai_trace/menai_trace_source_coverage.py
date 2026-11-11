"""
Coverage of one source file's functions, split by reachability.

A compiled program contains code objects from many files: the module under
test, everything it imports, the test module itself and the prelude.  Coverage
of a test suite is only meaningful for the module that suite exists to test, so
this module isolates the functions compiled from one source file.

Coverage alone cannot distinguish an instruction the tests did not reach from
one no execution could reach, because both have a count of zero.  Comparing the
unexecuted instructions against a reachability walk over the bytecode separates
the two: the first is a test gap, the second is dead bytecode that the compiler
should not have emitted.  See `menai_trace_reachability` for the walk itself.
"""

from dataclasses import dataclass, field

from menai_trace.menai_trace_coverage import (
    CoverageResult,
    FunctionCoverage,
    InstructionCoverage,
)
from menai_trace.menai_trace_reachability import reachable_indices

STATUS_FULL = "full"
STATUS_REACHABLE_COMPLETE = "reachable-complete"
STATUS_GAP = "gap"


@dataclass(frozen=True, slots=True)
class FunctionCoverageOutcome:
    """
    Coverage of one function together with the reachability of its instructions.

    Each function has exactly one of three outcomes:

      `STATUS_FULL`                every instruction executed;
      `STATUS_REACHABLE_COMPLETE`  every reachable instruction executed, but some
                                  unreachable instructions did not, which is a
                                  compiler question rather than a test question;
      `STATUS_GAP`                 at least one reachable instruction never
                                  executed, which is a test gap.
    """

    function: FunctionCoverage
    reachable: frozenset[int]

    def status(self) -> str:
        """Return this function's outcome as one of the `STATUS_*` values."""
        if self.function.fully_covered():
            return STATUS_FULL

        if self.missed_instructions():
            return STATUS_GAP

        return STATUS_REACHABLE_COMPLETE

    def fully_covered(self) -> bool:
        """Return True if every instruction in this function executed."""
        return self.function.fully_covered()

    def reachable_complete(self) -> bool:
        """Return True if every reachable instruction in this function executed."""
        return not self.missed_instructions()

    def missed_instructions(self) -> tuple[InstructionCoverage, ...]:
        """Return the reachable instructions that never executed, in order."""
        return tuple(
            instruction
            for instruction in self.function.uncovered_instructions()
            if instruction.index in self.reachable
        )

    def unreachable_instructions(self) -> tuple[InstructionCoverage, ...]:
        """Return the unexecuted instructions that no path from entry reaches, in order."""
        return tuple(
            instruction
            for instruction in self.function.uncovered_instructions()
            if instruction.index not in self.reachable
        )

    def reachable_instruction_count(self) -> int:
        """Return the number of instructions reachable from the function's entry."""
        return len(self.reachable)

    def covered_reachable_instructions(self) -> int:
        """Return the number of reachable instructions that executed."""
        return self.reachable_instruction_count() - len(self.missed_instructions())

    def reachable_coverage_percent(self) -> float:
        """Return coverage of the reachable instructions as a percentage."""
        total = self.reachable_instruction_count()
        if total == 0:
            return 100.0

        return self.covered_reachable_instructions() / total * 100.0


@dataclass(frozen=True, slots=True)
class SourceFileCoverage:
    """
    Coverage of the functions a compiled program contains from one source file.

    Only functions the compiler produced are represented.  A function inlined
    into its callers has no code object of its own and contributes its
    instructions to its caller's totals instead, so it cannot be reported
    separately.
    """

    source_file: str
    outcomes: tuple[FunctionCoverageOutcome, ...]

    def function_count(self) -> int:
        """Return the number of functions compiled from this source file."""
        return len(self.outcomes)

    def fully_covered(self) -> tuple[FunctionCoverageOutcome, ...]:
        """Return the functions in which every instruction executed."""
        return tuple(outcome for outcome in self.outcomes if outcome.status() == STATUS_FULL)

    def reachable_complete(self) -> tuple[FunctionCoverageOutcome, ...]:
        """Return the functions complete apart from unreachable instructions."""
        return tuple(
            outcome for outcome in self.outcomes if outcome.status() == STATUS_REACHABLE_COMPLETE
        )

    def gaps(self) -> tuple[FunctionCoverageOutcome, ...]:
        """Return the functions with at least one reachable instruction unexecuted."""
        return tuple(outcome for outcome in self.outcomes if outcome.status() == STATUS_GAP)

    def all_fully_covered(self) -> bool:
        """Return True if every function in this source file executed in full."""
        return all(outcome.fully_covered() for outcome in self.outcomes)

    def total_instructions(self) -> int:
        """Return the total number of instructions across these functions."""
        return sum(outcome.function.total_instructions() for outcome in self.outcomes)

    def covered_instructions(self) -> int:
        """Return the number of instructions across these functions that executed."""
        return sum(outcome.function.covered_instructions() for outcome in self.outcomes)

    def coverage_percent(self) -> float:
        """Return instruction coverage across these functions as a percentage."""
        total = self.total_instructions()
        if total == 0:
            return 100.0

        return self.covered_instructions() / total * 100.0

    def reachable_instruction_count(self) -> int:
        """Return the total number of reachable instructions across these functions."""
        return sum(outcome.reachable_instruction_count() for outcome in self.outcomes)

    def covered_reachable_instructions(self) -> int:
        """Return the number of reachable instructions across these functions that executed."""
        return sum(outcome.covered_reachable_instructions() for outcome in self.outcomes)

    def reachable_coverage_percent(self) -> float:
        """Return coverage of the reachable instructions as a percentage."""
        total = self.reachable_instruction_count()
        if total == 0:
            return 100.0

        return self.covered_reachable_instructions() / total * 100.0

    def unreachable_instruction_count(self) -> int:
        """Return the number of unexecuted instructions no path from entry reaches."""
        return sum(len(outcome.unreachable_instructions()) for outcome in self.outcomes)

    def ordered(self) -> tuple[FunctionCoverageOutcome, ...]:
        """Return the functions ordered by coverage, least covered first."""
        return tuple(
            sorted(
                self.outcomes,
                key=lambda outcome: (
                    outcome.reachable_coverage_percent(),
                    outcome.function.coverage_percent(),
                    outcome.function.code.source_line,
                    outcome.function.code.name,
                ),
            )
        )


def coverage_for_source(result: CoverageResult, source_file: str) -> SourceFileCoverage:
    """
    Select the coverage of one source file from a compiled program's coverage.

    Args:
        result: Coverage of the whole compiled program.
        source_file: The `CodeObject.source_file` value to keep, which for a
            module is its name with the `.menai` extension (for example
            `json-decode.menai`).

    Returns:
        The coverage of every function the program compiled from that file, in
        the program's code-object order.
    """
    outcomes = tuple(
        FunctionCoverageOutcome(function=function, reachable=reachable_indices(function.code))
        for function in result.functions
        if function.code.source_file == source_file
    )
    return SourceFileCoverage(source_file=source_file, outcomes=outcomes)


@dataclass
class CumulativeSourceCoverage:
    """
    Source file coverage merged across a set of test suites.

    Each suite contributes one `SourceFileCoverage`, so a run over a directory
    of suites reports every module it covers as a whole rather than as a long
    sequence of per-file blocks.  A file reported twice keeps its later report,
    matching the test run, where a file's own suite is the last thing to measure
    it.
    """

    reports: dict[str, SourceFileCoverage] = field(default_factory=dict)

    def add(self, report: SourceFileCoverage) -> None:
        """Record one suite's coverage of the module it tests."""
        self.reports[report.source_file] = report

    def report_count(self) -> int:
        """Return the number of source files reported."""
        return len(self.reports)

    def function_count(self) -> int:
        """Return the number of functions across all reported source files."""
        return sum(report.function_count() for report in self.reports.values())

    def fully_covered_count(self) -> int:
        """Return the number of functions in which every instruction executed."""
        return sum(len(report.fully_covered()) for report in self.reports.values())

    def reachable_complete_count(self) -> int:
        """Return the number of functions complete apart from unreachable instructions."""
        return sum(len(report.reachable_complete()) for report in self.reports.values())

    def gap_count(self) -> int:
        """Return the number of functions with at least one reachable instruction unexecuted."""
        return sum(len(report.gaps()) for report in self.reports.values())

    def total_instructions(self) -> int:
        """Return the total number of instructions across all reported functions."""
        return sum(report.total_instructions() for report in self.reports.values())

    def covered_instructions(self) -> int:
        """Return the number of instructions across all reported functions that executed."""
        return sum(report.covered_instructions() for report in self.reports.values())

    def coverage_percent(self) -> float:
        """Return instruction coverage across all reported functions as a percentage."""
        total = self.total_instructions()
        if total == 0:
            return 100.0

        return self.covered_instructions() / total * 100.0

    def reachable_instruction_count(self) -> int:
        """Return the number of reachable instructions across all reported functions."""
        return sum(
            report.reachable_instruction_count() for report in self.reports.values()
        )

    def covered_reachable_instructions(self) -> int:
        """Return the number of reachable instructions that executed."""
        return sum(
            report.covered_reachable_instructions() for report in self.reports.values()
        )

    def reachable_coverage_percent(self) -> float:
        """Return coverage of the reachable instructions as a percentage."""
        total = self.reachable_instruction_count()
        if total == 0:
            return 100.0

        return self.covered_reachable_instructions() / total * 100.0

    def unreachable_instruction_count(self) -> int:
        """Return the number of unexecuted instructions no path from entry reaches."""
        return sum(
            report.unreachable_instruction_count() for report in self.reports.values()
        )

    def ordered_reports(self) -> tuple[SourceFileCoverage, ...]:
        """Return the source files ordered by coverage, least covered first."""
        return tuple(
            sorted(
                self.reports.values(),
                key=lambda report: (
                    not report.gaps(),
                    report.reachable_coverage_percent(),
                    report.source_file,
                ),
            )
        )

    def ordered_gaps(self) -> tuple[FunctionCoverageOutcome, ...]:
        """Return every function with a gap, ordered by coverage, least covered first."""
        gaps = [
            outcome
            for report in self.reports.values()
            for outcome in report.gaps()
        ]
        return tuple(
            sorted(
                gaps,
                key=lambda outcome: (
                    outcome.reachable_coverage_percent(),
                    outcome.function.code.source_file,
                    outcome.function.code.source_line,
                ),
            )
        )
