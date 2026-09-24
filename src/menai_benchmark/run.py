#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from menai import Menai

from menai_benchmark import (
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


def filter_suites(
    all_suites: list[tuple[Path, type[BenchmarkSuite]]],
    names: list[str],
) -> list[tuple[Path, type[BenchmarkSuite]]]:
    """
    Return only the suites whose name contains any of the given substrings.

    Matching is case-insensitive.  A suite's name is taken from its
    ``BenchmarkSuite.name`` class attribute.

    Args:
        all_suites: The full list of discovered (directory, class) pairs.
        names:      Substrings to match against suite names.

    Returns:
        The filtered subset, preserving discovery order.
    """
    lowered = [n.lower() for n in names]
    return [
        (suite_dir, cls)
        for suite_dir, cls in all_suites
        if any(fragment in cls.name.lower() for fragment in lowered)
    ]


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the benchmark runner."""
    parser = argparse.ArgumentParser(
        description="Run Menai benchmark suites and report timing results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "python run.py                             # run all suites\n"
            "python run.py --suite sort                # run only the sort suite\n"
            "python run.py --suite sort sudoku         # run multiple\n"
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
        dest="suites",
        nargs="+",
        action="append",
        default=None,
        help=(
            "Run only suites whose name contains NAME (case-insensitive substring "
            "match).  May be repeated or given multiple values.  Omit to run all."
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
        iterations:   If given, override ``BenchmarkCase.iterations`` on every
                      case before running.
        profile:      If ``True``, enable opcode profiling during timed runs.
        profile_top:  Number of top opcodes to show in the profile output.
        trace:        If ``True``, enable per-function tracing during timed runs.
        trace_top:    Number of top functions to show per case in the trace output.
        annotate:     If ``True``, print the annotated disassembly per case.
    """
    suite = suite_class()

    if iterations is not None:
        for case in suite.cases():
            case.iterations = iterations

    module_path = [str(suite_dir), str(_MENAI_MODULES_DIR)]
    menai = Menai(module_path=module_path)

    runner = BenchmarkRunner(suite, menai, profile=profile, trace=trace or annotate)
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

    if args.suites is not None:
        flat_names = [name for group in args.suites for name in group]
        selected = filter_suites(all_suites, flat_names)
        if not selected:
            joined = ", ".join(flat_names)
            print(
                f"No suites matched the filter(s): {joined}",
                file=sys.stderr,
            )
            sys.exit(1)

    else:
        selected = all_suites

    for suite_dir, suite_class in selected:
        run_suite(
            suite_dir=suite_dir,
            suite_class=suite_class,
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
