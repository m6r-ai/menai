#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from menai import Menai

from menai_benchmark import (
    BenchmarkCase,
    BenchmarkReporter,
    BenchmarkRunner,
    BenchmarkSuite,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_BENCHMARK_DIR = Path(__file__).resolve().parent

_SUITES_DIR = _BENCHMARK_DIR / "suites"
_MENAI_MODULES_DIR = _REPO_ROOT / "menai_modules"


def discover_suites() -> list[tuple[Path, type[BenchmarkSuite]]]:
    """
    Discover all suite classes by scanning suites/*/suite.py.

    Each suite module must contain a class named ``Suite`` that subclasses
    ``BenchmarkSuite``.  Suites are returned in alphabetical order by
    directory name.

    Returns:
        A list of (suite_directory, Suite class) pairs.
    """
    found: list[tuple[Path, type[BenchmarkSuite]]] = []

    for suite_dir in sorted(d for d in _SUITES_DIR.iterdir() if d.is_dir() and (d / "suite.py").exists()):
        module_name = f"menai_benchmark.suites.{suite_dir.name}.suite"

        try:
            module = importlib.import_module(module_name)

        except Exception as exc:
            print(
                f"Warning: error importing {module_name}: {exc}, skipping.",
                file=sys.stderr,
            )
            continue

        suite_class = getattr(module, "Suite", None)
        if suite_class is None:
            print(
                f"Warning: {module_name} has no 'Suite' class, skipping.",
                file=sys.stderr,
            )
            continue

        found.append((suite_dir, suite_class))

    return found


def find_suite(
    all_suites: list[tuple[Path, type[BenchmarkSuite]]],
    name: str,
) -> tuple[Path, type[BenchmarkSuite]] | None:
    """
    Return the suite whose name exactly matches, ignoring case.

    A suite's name is taken from its ``BenchmarkSuite.name`` class attribute.

    Args:
        all_suites: The full list of discovered (directory, class) pairs.
        name:       The suite name to match.

    Returns:
        The matching (directory, class) pair, or ``None`` if there is no match.
    """
    lowered = name.lower()
    for suite_dir, cls in all_suites:
        if cls.name.lower() == lowered:
            return suite_dir, cls

    return None


def find_case(cases: list[BenchmarkCase], name: str) -> BenchmarkCase | None:
    """
    Return the case whose name exactly matches, ignoring case.

    Args:
        cases: The suite's cases.
        name:  The case name to match.

    Returns:
        The matching case, or ``None`` if there is no match.
    """
    lowered = name.lower()
    for case in cases:
        if case.name.lower() == lowered:
            return case

    return None


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the benchmark runner."""
    parser = argparse.ArgumentParser(
        description="Run Menai benchmark suites and report timing results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "python run.py                             # run all suites\n"
            "python run.py --suite sort                # run only the sort suite\n"
            "python run.py --suite sort --case n=1000  # run one case in a suite\n"
            "python run.py --iterations 5              # override iteration count\n"
            "python run.py --profile                   # opcode profiling (Menai only)\n"
            "python run.py --profile --profile-top 20  # limit opcode output\n"
            "python run.py --trace                     # per-function tracing (Menai only)\n"
            "python run.py --trace --trace-top 10      # limit trace output\n"
            "python run.py --annotate                  # annotated disassembly (Menai only)"
        ),
    )
    parser.add_argument(
        "--suite",
        metavar="NAME",
        dest="suite",
        default=None,
        help=(
            "Run only the suite named NAME (case-insensitive exact match).  "
            "Omit to run all suites."
        ),
    )
    parser.add_argument(
        "--case",
        metavar="NAME",
        dest="case",
        default=None,
        help=(
            "Run only the case named NAME within the selected suite "
            "(case-insensitive exact match).  Requires --suite."
        ),
    )
    parser.add_argument(
        "--iterations",
        metavar="N",
        type=int,
        default=None,
        help="Override the iteration count on every BenchmarkCase.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        dest="profile",
        help=(
            "Enable VM opcode profiling during timed runs.  Adds per-opcode "
            "frequency tables after the timing report.  Profiling overhead "
            "is included in the measured times."
        ),
    )
    parser.add_argument(
        "--profile-top",
        metavar="N",
        type=int,
        default=40,
        dest="profile_top",
        help="Show top N opcodes in the profile output (default: 40).",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        dest="trace",
        help=(
            "Enable VM per-function tracing during timed runs.  Adds a "
            "per-function summary per case, ranked by instructions executed, "
            "after the timing report.  "
            "Tracing overhead is included in the measured times.  Only applies "
            "to the Menai implementation."
        ),
    )
    parser.add_argument(
        "--trace-top",
        metavar="N",
        type=int,
        default=20,
        dest="trace_top",
        help="Show top N functions per case in the trace output (default: 20).",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        dest="annotate",
        help=(
            "Enable VM instruction tracing and print the annotated disassembly "
            "of every function per case, with each instruction's share of the "
            "total instructions executed.  Only applies to the Menai "
            "implementation."
        ),
    )
    return parser


def run_suite(
    suite_dir: Path,
    suite_class: type[BenchmarkSuite],
    case_name: str | None,
    iterations: int | None,
    profile: bool,
    profile_top: int,
    trace: bool,
    trace_top: int,
    annotate: bool,
) -> None:
    """
    Instantiate, run, and report a single benchmark suite.

    Args:
        suite_dir:    Directory containing the suite's ``suite.py`` (and any
                      ``.menai`` files it imports).
        suite_class:  The ``Suite`` subclass to instantiate.
        case_name:    If given, run only the case with this name (case-insensitive
                      exact match); otherwise run every case in the suite.
        iterations:   If given, override ``BenchmarkCase.iterations`` on every
                      case before running.
        profile:      If ``True``, enable opcode profiling during timed runs.
        profile_top:  Number of top opcodes to show in the profile output.
        trace:        If ``True``, enable per-function tracing during timed runs.
        trace_top:    Number of top functions to show per case in the trace output.
        annotate:     If ``True``, print the annotated disassembly per case.
    """
    suite = suite_class()
    cases = suite.cases()

    if case_name is not None:
        selected = find_case(cases, case_name)
        if selected is None:
            available = ", ".join(case.name for case in cases)
            print(
                f"No case named '{case_name}' in suite '{suite.name}'. "
                f"Available cases: {available}",
                file=sys.stderr,
            )
            sys.exit(1)

        cases = [selected]

    if iterations is not None:
        for case in cases:
            case.iterations = iterations

    module_path = [str(suite_dir), str(_MENAI_MODULES_DIR)]
    menai = Menai(module_path=module_path)

    runner = BenchmarkRunner(suite, cases, menai, profile=profile, trace=trace or annotate)
    results, profile_results, trace_results = runner.run()

    reporter = BenchmarkReporter()
    reporter.report(suite.name, results)

    if profile:
        reporter.report_profile(
            suite.name,
            profile_results,
            top_n=profile_top,
        )

    if trace:
        reporter.report_trace(
            suite.name,
            trace_results,
            top_n=trace_top,
        )

    if annotate:
        reporter.report_annotated(
            suite.name,
            trace_results,
        )


def main() -> None:
    """Entry point for the benchmark CLI."""
    parser = build_parser()
    args = parser.parse_args()

    all_suites = discover_suites()

    if not all_suites:
        print("No suites found under suites/*/suite.py.", file=sys.stderr)
        sys.exit(1)

    if args.case is not None and args.suite is None:
        parser.error("--case requires --suite")

    if args.suite is not None:
        found = find_suite(all_suites, args.suite)
        if found is None:
            available = ", ".join(cls.name for _, cls in all_suites)
            print(
                f"No suite named '{args.suite}'. Available suites: {available}",
                file=sys.stderr,
            )
            sys.exit(1)

        selected = [found]

    else:
        selected = all_suites

    for suite_dir, suite_class in selected:
        run_suite(
            suite_dir=suite_dir,
            suite_class=suite_class,
            case_name=args.case,
            iterations=args.iterations,
            profile=args.profile,
            profile_top=args.profile_top,
            trace=args.trace,
            trace_top=args.trace_top,
            annotate=args.annotate,
        )

    if len(selected) > 1:
        print(f"\n{len(selected)} suite(s) completed.")


if __name__ == "__main__":
    main()
