from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
import time
from typing import Any

from menai import Menai


@dataclass
class BenchmarkCase:
    """A single parameterised scenario to benchmark across all implementations."""

    name: str
    input: Any
    iterations: int


@dataclass
class Implementation:
    """
    A named callable that can be timed against a BenchmarkCase.

    When *prepare* is provided it is called once **outside** the timed loop
    with the case input.  Its return value is passed to *run* on every
    timed iteration.  This lets implementations move string construction
    and compilation out of the measured section.

    When *prepare* is ``None`` (the default), *run* receives the case
    input directly, preserving backward compatibility.
    """

    name: str
    run: Callable
    prepare: Callable | None = None


@dataclass
class CaseResult:
    """Timing and validation outcome for one implementation on one case."""

    case: BenchmarkCase
    impl_name: str
    mean_s: float
    min_s: float
    valid: bool
    error: str | None


@dataclass
class ProfileResult:
    """Opcode profiling data for one implementation on one case."""

    case: BenchmarkCase
    impl_name: str
    opcode_counts: dict[str, int] = field(default_factory=dict)
    total_instructions: int = 0
    error: str | None = None


@dataclass
class TimingResult:
    """VM phase timing data for one implementation on one case."""

    case: BenchmarkCase
    impl_name: str
    convert_ns: int = 0
    execute_ns: int = 0
    error: str | None = None


class BenchmarkSuite(ABC):
    """
    Abstract base class for a family of related benchmarks.

    Subclasses declare the cases to run, the implementations to compare, and
    the equality predicate used to validate results against the reference
    implementation.
    """

    name: str
    description: str

    @abstractmethod
    def cases(self) -> list[BenchmarkCase]:
        """Return the list of cases that every implementation will be run against."""

    @abstractmethod
    def implementations(self, menai: Menai) -> list[Implementation]:
        """
        Return the ordered list of implementations to benchmark.

        The first entry is treated as the reference; all others are validated
        against it.  The supplied *menai* instance is already warmed up.
        """

    @abstractmethod
    def results_equal(self, a: Any, b: Any) -> bool:
        """Return True if two results should be considered equivalent."""


class BenchmarkRunner:
    """
    Runs a BenchmarkSuite and collects CaseResult objects.

    The caller is responsible for warming up the Menai instance before
    passing it in.  No additional warmup is performed here.

    When *profile* is True, opcode profiling is enabled on the Menai VM
    during the timed runs.  Profile data is collected from the final
    timed iteration of each Menai implementation and returned alongside
    timing results.  VM phase timing (conversion vs execution) is always
    collected for Menai implementations.
    """

    def __init__(self, suite: BenchmarkSuite, menai: Menai, profile: bool = False) -> None:
        """Initialise the runner with a suite, a warmed-up Menai instance, and optional profiling."""
        self._suite = suite
        self._menai = menai
        self._profile = profile

    def run(self) -> tuple[list[CaseResult], list[ProfileResult], list[TimingResult]]:
        """Execute every (case, implementation) combination and return timing and profile results."""
        suite = self._suite
        impls = suite.implementations(self._menai)
        results: list[CaseResult] = []
        profile_results: list[ProfileResult] = []
        timing_results: list[TimingResult] = []

        for case in suite.cases():
            reference_result: Any = None
            reference_set = False

            for idx, impl in enumerate(impls):
                times: list[float] = []
                result: Any = None
                error: str | None = None
                profile_data: dict[str, int] = {}
                timing_data: dict[str, int] = {}

                # Pre-timing setup: build strings, compile, etc.
                if impl.prepare is not None:
                    prepared = impl.prepare(case.input)

                else:
                    prepared = case.input

                if self._profile:
                    self._menai.vm.enable_profiling()

                try:
                    for iteration in range(case.iterations):
                        t0 = time.perf_counter()
                        raw = impl.run(prepared)
                        t1 = time.perf_counter()
                        times.append(t1 - t0)

                        # Collect profile data from the last iteration.
                        if self._profile and iteration == case.iterations - 1:
                            profile_data = self._menai.vm.get_profile_data()

                        # Collect VM timing data from the last iteration.
                        if iteration == case.iterations - 1:
                            timing_data = self._menai.vm.get_timing_data()

                    # Convert MenaiValue → Python outside the timed loop.
                    if hasattr(raw, 'to_python'):
                        result = raw.to_python()

                    else:
                        result = raw

                except Exception as exc:
                    error = str(exc)

                if error is not None:
                    mean_s = 0.0
                    min_s = 0.0
                    valid = False

                else:
                    mean_s = sum(times) / len(times)
                    min_s = min(times)
                    if idx == 0:
                        reference_result = result
                        reference_set = True
                        valid = True

                    else:
                        valid = reference_set and suite.results_equal(reference_result, result)

                results.append(
                    CaseResult(
                        case=case,
                        impl_name=impl.name,
                        mean_s=mean_s,
                        min_s=min_s,
                        valid=valid,
                        error=error,
                    )
                )

                if self._profile:
                    total = profile_data.pop("__total__", 0) if profile_data else 0
                    profile_results.append(
                        ProfileResult(
                            case=case,
                            impl_name=impl.name,
                            opcode_counts=profile_data,
                            total_instructions=total,
                            error=error,
                        )
                    )

                timing_results.append(
                    TimingResult(
                        case=case,
                        impl_name=impl.name,
                        convert_ns=timing_data.get("convert_ns", 0) if timing_data else 0,
                        execute_ns=timing_data.get("execute_ns", 0) if timing_data else 0,
                        error=error,
                    )
                )

        return results, profile_results, timing_results


class BenchmarkReporter:
    """Formats and prints a comparison table for a completed benchmark run."""

    _SEPARATOR = "─" * 131
    _MS = 1_000.0
    _COL_CASE = 24
    _COL_MEAN = 9
    _COL_MIN = 9
    _COL_VS = 16
    _PROFILE_SEPARATOR = "─" * 70
    _COL_OPCODE = 40
    _COL_COUNT = 15
    _COL_PCT = 12

    def report(
        self,
        suite_name: str,
        results: list[CaseResult],
        timing_results: list[TimingResult],
        implementations: list[Implementation],
    ) -> None:
        """
        Print a formatted comparison table to stdout.

        The first implementation is the reference.  Subsequent implementations
        show a "vs ref" speedup/slowdown ratio and a validity marker.
        """
        if not implementations:
            return

        impl_names = [i.name for i in implementations]
        cases: list[BenchmarkCase] = []
        seen: set[str] = set()
        for result in results:
            if result.case.name not in seen:
                cases.append(result.case)
                seen.add(result.case.name)

        by_key: dict[tuple[str, str], CaseResult] = {
            (result.case.name, result.impl_name): result for result in results
        }
        timing_by_key: dict[tuple[str, str], TimingResult] = {
            (tr.case.name, tr.impl_name): tr for tr in timing_results
        }

        print()
        print(suite_name.upper())
        print(self._SEPARATOR)

        header1_parts = [f"{'Case':<{self._COL_CASE}}"]
        header2_parts = [" " * self._COL_CASE]
        for idx, name in enumerate(impl_names):
            col_width = self._COL_MEAN + self._COL_MIN + 2
            if idx > 0:
                col_width += self._COL_VS + 2

            header1_parts.append(f"{name:>{col_width}}")
            sub = f"{'mean (ms)':>{self._COL_MEAN}}  {'min (ms)':>{self._COL_MIN}}"
            if idx > 0:
                sub += f" {'vs ref':>{self._COL_VS - 1}}  "

            else:
                sub += "  "

            header2_parts.append(sub)

        print("   ".join(header1_parts))
        print("   ".join(header2_parts))
        print(self._SEPARATOR)

        validation_counts: dict[str, int] = {n: 0 for n in impl_names}

        for case in cases:
            ref_result = by_key.get((case.name, impl_names[0]))
            ref_tr: TimingResult | None = timing_by_key.get((case.name, impl_names[0]))
            ref_mean = (ref_tr.execute_ns / 1_000_000_000.0) if ref_tr and not ref_tr.error else (
                ref_result.mean_s if ref_result and not ref_result.error else None
            )

            row_parts = [f"{case.name:<{self._COL_CASE}}"]

            for idx, name in enumerate(impl_names):
                r: CaseResult | None = by_key.get((case.name, name))
                if r is None:
                    cell = f"{'N/A':>{self._COL_MEAN}}  {'N/A':<{self._COL_MIN}}"
                    if idx > 0:
                        cell += f"  {'':>{self._COL_VS}}"

                    row_parts.append(cell)
                    continue

                if r.error is not None:
                    mean_str = "ERROR"
                    min_str = ""

                else:
                    tr: TimingResult | None = timing_by_key.get((case.name, name)) if idx == 0 else None
                    if tr is not None and not tr.error and tr.execute_ns > 0:
                        mean_str = f"{tr.execute_ns / 1_000_000.0:.3f}"
                        min_str = f"{tr.execute_ns / 1_000_000.0:.3f}"

                    else:
                        mean_str = f"{r.mean_s * self._MS:.3f}"
                        min_str = f"{r.min_s * self._MS:.3f}"

                    if r.valid:
                        validation_counts[name] += 1

                validity = "✓" if r.valid else "✗"
                cell = f"{mean_str:>{self._COL_MEAN}}  {min_str:>{self._COL_MIN}}"

                if idx == 0:
                    cell += f" {validity}"

                else:
                    vs_str = self._vs_ref(ref_mean, r.mean_s if not r.error else None)
                    cell += f"  {vs_str:>{self._COL_VS - 2}} {validity}"

                row_parts.append(cell)

            print("   ".join(row_parts))

        print(self._SEPARATOR)

        summary_parts: list[str] = []
        for name in impl_names:
            count = validation_counts[name]
            total = len(cases)
            marker = "✓" if count == total else "✗"
            summary_parts.append(f"{name} {count}/{total} {marker}")

        print("Validation: " + "  |  ".join(summary_parts))
        print()

    def report_profile(
        self,
        suite_name: str,
        profile_results: list[ProfileResult],
        implementations: list[Implementation],
        top_n: int = 40,
    ) -> None:
        """
        Print opcode frequency profiles for each (case, implementation) pair.

        Only Menai implementations produce opcode profile data.  Non-Menai
        implementations are skipped silently.
        """
        if not profile_results:
            return

        impl_names = [i.name for i in implementations]
        cases: list[BenchmarkCase] = []
        seen: set[str] = set()
        for prof in profile_results:
            if prof.case.name not in seen:
                cases.append(prof.case)
                seen.add(prof.case.name)

        by_key: dict[tuple[str, str], ProfileResult] = {
            (prof.case.name, prof.impl_name): prof for prof in profile_results
        }

        print()
        print(f"{suite_name.upper()} — OPCODE PROFILES")
        print(self._SEPARATOR)

        for case in cases:
            for name in impl_names:
                pr: ProfileResult | None = by_key.get((case.name, name))
                if pr is None or pr.error is not None:
                    continue

                if not pr.opcode_counts or pr.total_instructions == 0:
                    continue

                print(f"\n  {case.name} / {name}")
                print(f"  {self._PROFILE_SEPARATOR}")

                entries = [
                    (op, count)
                    for op, count in pr.opcode_counts.items()
                    if count > 0
                ]
                entries.sort(key=lambda e: e[1], reverse=True)

                print(f"  {'Opcode':<{self._COL_OPCODE}} {'Count':>{self._COL_COUNT}} {'% of total':>{self._COL_PCT}}")
                print(f"  {'-' * self._COL_OPCODE} {'-' * self._COL_COUNT} {'-' * self._COL_PCT}")

                for op, count in entries[:top_n]:
                    pct = (count / pr.total_instructions * 100.0) if pr.total_instructions > 0 else 0.0
                    print(f"  {op:<{self._COL_OPCODE}} {count:>{self._COL_COUNT},} {pct:>{self._COL_PCT - 1}.1f}%")

                print(f"  {'-' * self._COL_OPCODE} {'-' * self._COL_COUNT} {'-' * self._COL_PCT}")
                print(f"  {'TOTAL':<{self._COL_OPCODE}} {pr.total_instructions:>{self._COL_COUNT},}")

        print()
        print(self._SEPARATOR)
        print()

    def _vs_ref(self, ref_mean_s: float | None, impl_mean_s: float | None) -> str:
        """
        Return a human-readable speedup/slowdown string relative to the reference.

        Returns an empty string when either value is unavailable.
        """
        if ref_mean_s is None or impl_mean_s is None or impl_mean_s == 0.0:
            return ""

        if ref_mean_s == 0.0:
            return ""

        ratio = ref_mean_s / impl_mean_s
        if ratio >= 1.0:
            fmt = ".1f" if ratio < 10 else ".0f"
            return f"{ratio:{fmt}}x faster"

        inv = 1.0 / ratio
        fmt = ".1f" if inv < 10 else ".0f"
        return f"{inv:{fmt}}x slower"
