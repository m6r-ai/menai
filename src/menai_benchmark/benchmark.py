from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from menai import Menai, MenaiBytes, MenaiDict, MenaiString
from menai.bytecode.menai_bytecode import CodeObject
from menai_trace.menai_trace_data import TraceResult as ResolvedTrace
from menai_trace.menai_trace_data import resolve_trace
from menai_trace.menai_trace_render import render_annotated, render_function_summary


@dataclass
class BenchmarkCase:
    """A single parameterised scenario to benchmark."""

    name: str
    input: Any
    iterations: int


@dataclass
class Implementation:
    """
    The Menai implementation under benchmark.

    When *prepare* is provided it is called once **outside** the timed loop
    with the case input.  Its return value is passed to *run* on every
    timed iteration.  This lets the implementation move string construction
    and compilation out of the measured section.

    When *prepare* is ``None`` (the default), *run* receives the case
    input directly.
    """

    run: Callable
    prepare: Callable | None = None


@dataclass
class MenaiProgram:
    """
    Declarative description of the Menai program a suite benchmarks.

    *expression* is either a constant expression string, or a callable that
    maps a case input to an expression string.

    *fixture* is an optional callable that maps a case input to the bytes
    bound as the ``inputs`` dict's ``"input-data"`` member.  When it is
    provided, the expression reads its input via
    ``(dict-get inputs "input-data")``; when it is ``None`` the expression is
    compiled with no injected binding.
    """

    expression: str | Callable[[Any], str]
    fixture: Callable[[Any], bytes] | None = None


def build_menai_implementation(menai: Menai, program: MenaiProgram) -> Implementation:
    """
    Build the benchmark Implementation for a Menai program.

    Compilation (including expression construction and fixture binding) happens
    in *prepare*, outside the timed loop; *run* executes the pre-compiled
    bytecode.  This keeps compilation cost out of the measured section.
    """
    expression = program.expression
    fixture = program.fixture

    def prepare(case_input: Any) -> CodeObject:
        """Build the expression, bind any fixture bytes, and compile (untimed)."""
        expr = expression if isinstance(expression, str) else expression(case_input)

        if fixture is None:
            return menai.compile(expr)

        inputs = MenaiDict(((MenaiString("input-data"), MenaiBytes(fixture(case_input))),))
        return menai.compile(expr, inject=("inputs", inputs))

    def run(code: CodeObject) -> Any:
        """Execute the pre-compiled bytecode (timed)."""
        return menai.execute_raw(code)

    return Implementation(run=run, prepare=prepare)


@dataclass
class CaseResult:
    """Timing outcome for one case."""

    case: BenchmarkCase
    mean_s: float
    min_s: float
    error: str | None


@dataclass
class ProfileResult:
    """Opcode profiling data for one case."""

    case: BenchmarkCase
    opcode_counts: dict[str, int] = field(default_factory=dict)
    total_instructions: int = 0
    error: str | None = None


@dataclass
class TraceResult:
    """Instruction and call trace data for one case."""

    case: BenchmarkCase
    trace: ResolvedTrace | None = None
    error: str | None = None


class BenchmarkSuite(ABC):
    """
    Abstract base class for a family of related benchmarks.

    Subclasses declare the cases to run and the single Menai implementation
    that will be timed against them.
    """

    name: str
    description: str

    @abstractmethod
    def cases(self) -> list[BenchmarkCase]:
        """Return the list of cases that the implementation will be run against."""

    @abstractmethod
    def menai_program(self) -> MenaiProgram:
        """Return the Menai program that will be timed against the cases."""


class BenchmarkRunner:
    """
    Runs selected cases from a BenchmarkSuite and collects CaseResult objects.

    The cases to run are supplied explicitly rather than read from the suite,
    so the caller controls selection (e.g. running a single case).  The suite
    is still required to resolve the implementation under benchmark.

    The caller is responsible for warming up the Menai instance before
    passing it in.  No additional warmup is performed here.

    Timing is per-iteration: the recorded time is the VM's internal
    execute_ns (pure VM execution, excluding Python↔C bridge overhead),
    read via get_timing_data() after each call.

    When *profile* is True, opcode profiling is enabled on the Menai VM
    during the timed runs.  Profile data is collected from the final
    timed iteration of each case and returned alongside timing results.

    When *trace* is True, instruction and call tracing is enabled on the
    Menai VM during the timed runs.  Trace data is collected from the final
    timed iteration of each case and resolved against the compiled code
    object.  Tracing requires the prepared value to be a CodeObject, so it
    only applies to the Menai implementation.
    """

    def __init__(
        self,
        suite: BenchmarkSuite,
        cases: list[BenchmarkCase],
        menai: Menai,
        profile: bool = False,
        trace: bool = False,
    ) -> None:
        """Initialise the runner with a suite, the cases to run, a warmed-up Menai instance, and optional instrumentation."""
        self._suite = suite
        self._cases = cases
        self._menai = menai
        self._profile = profile
        self._trace = trace

    def run(self) -> tuple[list[CaseResult], list[ProfileResult], list[TraceResult]]:
        """Execute the selected cases and return timing, profile, and trace results."""
        suite = self._suite
        impl = build_menai_implementation(self._menai, suite.menai_program())
        results: list[CaseResult] = []
        profile_results: list[ProfileResult] = []
        trace_results: list[TraceResult] = []

        for case in self._cases:
            times: list[float] = []
            error: str | None = None
            profile_data: dict[str, int] = {}
            raw_trace: tuple[list[int], list[int]] | None = None

            # Pre-timing setup: build strings, compile, etc.
            if impl.prepare is not None:
                prepared = impl.prepare(case.input)

            else:
                prepared = case.input

            if self._profile or self._trace:
                self._menai.vm.enable_profiling()

            try:
                for iteration in range(case.iterations):
                    impl.run(prepared)
                    vm_timing = self._menai.vm.get_timing_data()
                    times.append(vm_timing.get("execute_ns", 0) / 1_000_000_000.0)

                    # Collect instrumentation data from the last iteration.
                    if iteration == case.iterations - 1:
                        if self._profile:
                            profile_data = self._menai.vm.get_profile_data()

                        if self._trace:
                            raw_trace = self._menai.vm.get_trace_data()

            except Exception as exc:
                error = str(exc)

            if error is not None:
                mean_s = 0.0
                min_s = 0.0

            else:
                mean_s = sum(times) / len(times)
                min_s = min(times)

            results.append(
                CaseResult(
                    case=case,
                    mean_s=mean_s,
                    min_s=min_s,
                    error=error,
                )
            )

            if self._profile:
                total = profile_data.pop("__total__", 0) if profile_data else 0
                profile_results.append(
                    ProfileResult(
                        case=case,
                        opcode_counts=profile_data,
                        total_instructions=total,
                        error=error,
                    )
                )

            if self._trace:
                trace_results.append(
                    TraceResult(
                        case=case,
                        trace=_resolve_case_trace(prepared, raw_trace, error),
                        error=error,
                    )
                )

        return results, profile_results, trace_results


def _resolve_case_trace(
    prepared: Any,
    raw_trace: tuple[list[int], list[int]] | None,
    error: str | None,
) -> ResolvedTrace | None:
    """
    Resolve raw trace arrays against the prepared code object.

    Returns None when the case errored, when no trace was collected, or when
    the prepared value is not a CodeObject (i.e. not the Menai implementation).
    """
    if error is not None or raw_trace is None or not isinstance(prepared, CodeObject):
        return None

    instr_counts, call_counts = raw_trace
    return resolve_trace(prepared, instr_counts, call_counts)


class BenchmarkReporter:
    """Formats and prints a timing table for a completed benchmark run."""

    _MS = 1_000.0
    _MIN_COL_CASE = 24
    _COL_MEAN = 12
    _COL_MIN = 12
    _MIN_COL_OPCODE = 40
    _COL_COUNT = 15
    _COL_PCT = 12

    def report(self, suite_name: str, results: list[CaseResult]) -> None:
        """Print a formatted timing table to stdout."""
        col_case = max(self._MIN_COL_CASE, max((len(r.case.name) for r in results), default=0))
        separator = "─" * (col_case + 3 + self._COL_MEAN + 3 + self._COL_MIN)
        print()
        print(suite_name.upper())
        print(separator)
        print(
            f"{'Case':<{col_case}}   "
            f"{'mean (ms)':>{self._COL_MEAN}}   "
            f"{'min (ms)':>{self._COL_MIN}}"
        )
        print(separator)

        for result in results:
            if result.error is not None:
                mean_str = "ERROR"
                min_str = ""

            else:
                mean_str = f"{result.mean_s * self._MS:.3f}"
                min_str = f"{result.min_s * self._MS:.3f}"

            print(
                f"{result.case.name:<{col_case}}   "
                f"{mean_str:>{self._COL_MEAN}}   "
                f"{min_str:>{self._COL_MIN}}"
            )

        print(separator)
        print()

    def report_profile(
        self,
        suite_name: str,
        profile_results: list[ProfileResult],
        top_n: int = 40,
    ) -> None:
        """Print opcode frequency profiles for each case."""
        if not profile_results:
            return

        col_opcode = max(
            self._MIN_COL_OPCODE,
            max(
                (len(op) for pr in profile_results for op in pr.opcode_counts),
                default=0,
            ),
        )
        separator = "─" * (2 + col_opcode + 1 + self._COL_COUNT + 1 + self._COL_PCT)
        print()
        print(f"{suite_name.upper()} — OPCODE PROFILES")
        print(separator)

        for pr in profile_results:
            if pr.error is not None:
                continue

            if not pr.opcode_counts or pr.total_instructions == 0:
                continue

            print(f"\n  {pr.case.name}")
            print(f"  {'─' * (col_opcode + 1 + self._COL_COUNT + 1 + self._COL_PCT)}")

            entries = [
                (op, count)
                for op, count in pr.opcode_counts.items()
                if count > 0
            ]
            entries.sort(key=lambda e: e[1], reverse=True)

            shown = entries[:top_n]

            print(f"  {'Opcode':<{col_opcode}} {'Count':>{self._COL_COUNT}} {'% of total':>{self._COL_PCT}}")
            print(f"  {'-' * col_opcode} {'-' * self._COL_COUNT} {'-' * self._COL_PCT}")

            for op, count in shown:
                pct = (count / pr.total_instructions * 100.0) if pr.total_instructions > 0 else 0.0
                print(f"  {op:<{col_opcode}} {count:>{self._COL_COUNT},} {pct:>{self._COL_PCT - 1}.1f}%")

            print(f"  {'-' * col_opcode} {'-' * self._COL_COUNT} {'-' * self._COL_PCT}")
            print(f"  {'TOTAL':<{col_opcode}} {pr.total_instructions:>{self._COL_COUNT},}")

        print()
        print(separator)
        print()

    def report_trace(
        self,
        suite_name: str,
        trace_results: list[TraceResult],
        top_n: int | None = 20,
    ) -> None:
        """
        Print a per-function summary for each case, ranked by instructions executed.

        Only cases whose prepared value was a CodeObject produce a trace, so
        cases from non-Menai implementations are skipped.
        """
        self._report_traces(
            suite_name,
            "TRACES",
            trace_results,
            lambda trace: render_function_summary(trace, color=False, top_n=top_n),
        )

    def report_annotated(
        self,
        suite_name: str,
        trace_results: list[TraceResult],
    ) -> None:
        """
        Print the annotated disassembly for each case.

        Only cases whose prepared value was a CodeObject produce a trace, so
        cases from non-Menai implementations are skipped.
        """
        self._report_traces(
            suite_name,
            "ANNOTATED TRACES",
            trace_results,
            lambda trace: render_annotated(trace, color=False),
        )

    def _report_traces(
        self,
        suite_name: str,
        heading: str,
        trace_results: list[TraceResult],
        render: Callable[[ResolvedTrace], list[str]],
    ) -> None:
        """Print a per-case trace section using the given renderer."""
        print()
        print(f"{suite_name.upper()} \u2014 {heading}")

        for tr in trace_results:
            trace = tr.trace
            if trace is None:
                continue

            print()
            print(f"  {tr.case.name}")
            for line in render(trace):
                print(f"  {line}")
