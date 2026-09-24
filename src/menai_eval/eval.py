#!/usr/bin/env python3
"""
Menai evaluator - compile and evaluate a Menai source file.

Reads a Menai source file (or an expression from stdin), compiles it, and
evaluates it, printing the result.  Two optional profiling modes are
available and may be combined:

  --cprofile   Python-level cProfile of the compilation pipeline.  Shows
               per-pass attribution across lexing, parsing, semantic
               analysis, module resolution, desugaring, AST optimisation,
               IR building, IR optimisation, CFG building, CFG optimisation,
               VCode building, and bytecode emission.

  --profile    VM-level opcode frequency profiling.  Counts how many times
               each bytecode opcode is executed and measures total
               wall-clock time, giving instruction throughput and average
               time per instruction.

  --trace      VM-level per-function tracing.  Ranks functions by the number
               of instructions they executed, showing each function's share of
               the total instructions executed and its call count.  This
               answers "which function dominates".

  --annotate   Annotated disassembly.  Renders every function in disassembly
               order with each instruction's execution count and its share of
               the total instructions executed.  This is the perf-annotate
               view for reading hot regions in context, and is the
               instruction-level counterpart to --trace.

The two modes are complementary: cProfile covers the compiler (which is
pure Python) but sees VM execution as a single opaque C frame, while opcode
profiling covers the VM runtime but not the compiler.

Module paths are resolved the same way as the disassembler and the pipeline
runner: the file's own directory first, then the current working directory.
When reading from stdin there is no file directory, so the current working
directory is used.

Usage:
    menai-eval <file.menai>
    menai-eval -
    menai-eval <file.menai> --cprofile
    menai-eval <file.menai> --profile
    menai-eval <file.menai> --trace
    menai-eval <file.menai> --profile --trace
    menai-eval <file.menai> --annotate
    menai-eval <file.menai> --profile --annotate
    menai-eval <file.menai> --cprofile --profile
    menai-eval <file.menai> --profile --top 50
    menai-eval <file.menai> --cprofile --sort time
    menai-eval <file.menai> --cprofile --output stats.prof
    menai-eval <file.menai> --raw
"""

import argparse
import cProfile
import pstats
import sys
import time
import traceback
from io import StringIO
from pathlib import Path

from menai import Menai, MenaiError, MenaiString, MenaiValue
from menai.bytecode.menai_bytecode import CodeObject
from menai.menai_compiler import MenaiCompiler
from menai_trace.menai_trace_data import resolve_trace
from menai_trace.menai_trace_render import render_annotated, render_function_summary

_ANSI_GREY = "\033[90m"
_ANSI_RESET = "\033[0m"

_SEPARATOR_WIDTH = 70

_OPCODE_COL = 40
_COUNT_COL = 15
_PCT_COL = 12


def build_module_path(source_path: Path | None) -> list[str]:
    """
    Build a deduplicated module search path for the given source file.

    Mirrors the strategy used by the disassembler and the pipeline runner:
      1. The file's own directory (so bare module names resolve next to the file)
      2. The current working directory (so project-root-relative import paths work)

    When source_path is None (stdin input) only the current working directory
    is used.
    """
    cwd = str(Path.cwd())
    candidates = [str(source_path.parent.absolute()), cwd] if source_path is not None else [cwd]

    module_path: list[str] = []
    for directory in candidates:
        if directory not in module_path:
            module_path.append(directory)

    return module_path


def use_color(no_color: bool) -> bool:
    """Return True if ANSI colour output should be used."""
    return not no_color and sys.stdout.isatty()


def separator(color: bool) -> str:
    """Return a full-width section separator, optionally coloured grey."""
    line = "\u2500" * _SEPARATOR_WIDTH
    return f"{_ANSI_GREY}{line}{_ANSI_RESET}" if color else line


def print_section(title: str, color: bool) -> None:
    """Print a titled section block delimited by full-width separators."""
    line = separator(color)
    print(line)
    print(title)
    print(line)


def read_source(input_arg: str) -> tuple[str, Path | None, str]:
    """
    Read Menai source from a file or from stdin.

    Args:
        input_arg: A file path, or "-" to read from stdin.

    Returns:
        A triple of (source, source_path, name) where source_path is None for
        stdin input and name is a display name for error messages.

    Raises:
        SystemExit: If the file does not exist.
    """
    if input_arg == "-":
        return sys.stdin.read(), None, "<stdin>"

    source_path = Path(input_arg)
    if not source_path.exists():
        print(f"Error: file not found: {input_arg}", file=sys.stderr)
        sys.exit(1)

    return source_path.read_text(encoding="utf-8"), source_path, str(source_path)


def compile_source(
    source: str,
    name: str,
    menai: Menai,
) -> tuple[CodeObject, float]:
    """
    Compile Menai source and return the CodeObject and elapsed compile time.

    Args:
        source: Menai source code.
        name:   Display name for the compilation unit.
        menai:  Initialised Menai instance (provides the module loader).

    Returns:
        A tuple of (CodeObject, elapsed seconds).

    Raises:
        SystemExit: If compilation fails.
    """
    compiler = MenaiCompiler(module_loader=menai)

    start = time.perf_counter()
    try:
        code = compiler.compile(source, name=name)

    except MenaiError as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        sys.exit(1)

    except Exception as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    return code, time.perf_counter() - start


def profile_compiler(
    source: str,
    name: str,
    menai: Menai,
    top_n: int,
    sort_by: str,
    output_file: str | None,
    color: bool,
) -> CodeObject:
    """
    Compile the source under cProfile and report the per-pass breakdown.

    Only the compilation pipeline is profiled; execution is performed
    separately so that the single opaque C ``execute`` frame does not
    dominate the cumulative-time table.

    Args:
        source:      Menai source code.
        name:        Display name for the compilation unit.
        menai:       Initialised Menai instance (provides the module loader).
        top_n:       Number of top entries to print.
        sort_by:     pstats sort key.
        output_file: Optional path to save raw profile data (.prof).
        color:       Whether to colour the section separators.

    Returns:
        The compiled CodeObject (so it can be reused for execution).

    Raises:
        SystemExit: If compilation fails.
    """
    compiler = MenaiCompiler(module_loader=menai)

    profiler = cProfile.Profile()
    profiler.enable()

    try:
        code = compiler.compile(source, name=name)

    except MenaiError as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        sys.exit(1)

    except Exception as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    finally:
        profiler.disable()

    print()
    print_section(f"COMPILER PROFILE  (top {top_n} by {sort_by})", color)

    buffer = StringIO()
    stats = pstats.Stats(profiler, stream=buffer)
    stats.sort_stats(sort_by)
    stats.print_stats(top_n)
    print(buffer.getvalue(), end="")

    if output_file:
        profiler.dump_stats(output_file)
        print(f"Profile data saved to: {output_file}")
        print(f"  View with: python -m pstats {output_file}")
        print(f"  Or:        snakeviz {output_file}")

    return code


def instrument_vm(
    menai: Menai,
    code: CodeObject,
    top_n: int,
    color: bool,
    profile: bool,
    trace: bool,
    annotate: bool,
) -> MenaiValue:
    """
    Execute the compiled code once with VM instrumentation enabled.

    Both the opcode histogram and the instruction/call trace are collected
    during a single run, so requesting both costs no more than requesting one.
    A warm-up run is performed without instrumentation so that first-run costs
    (page faults, code cache warming) do not skew the measurement.

    Args:
        menai:   Initialised Menai instance.
        code:    Compiled CodeObject.
        top_n:   Number of entries to show in each report.
        color:   Whether to colour the output.
        profile: Whether to report the opcode histogram.
        trace:   Whether to report the instruction and call trace.
        annotate: Whether to report the annotated per-function disassembly.

    Returns:
        The evaluation result (raw MenaiValue).

    Raises:
        SystemExit: If execution fails.
    """
    try:
        menai.execute_raw(code)

    except Exception as exc:
        _report_execution_error(exc)
        sys.exit(1)

    menai.vm.enable_profiling()

    start = time.perf_counter()
    try:
        result = menai.execute_raw(code)

    except Exception as exc:
        _report_execution_error(exc)
        sys.exit(1)

    elapsed_s = time.perf_counter() - start

    if profile:
        _print_opcode_profile(menai, top_n, elapsed_s, color)

    if trace or annotate:
        instr_counts, call_counts = menai.vm.get_trace_data()
        trace_result = resolve_trace(code, instr_counts, call_counts)

        if trace:
            print()
            for line in render_function_summary(trace_result, color=color, top_n=top_n):
                print(line)

        if annotate:
            print()
            for line in render_annotated(trace_result, color=color):
                print(line)

    return result


def _print_opcode_profile(menai: Menai, top_n: int, elapsed_s: float, color: bool) -> None:
    """Print the per-opcode frequency histogram collected from the last run."""
    profile_data = menai.vm.get_profile_data()

    print()
    print_section(f"VM OPCODE PROFILE  (top {top_n} by count)", color)

    if not profile_data or profile_data.get("__total__", 0) == 0:
        print("No opcode profiling data collected.")
        print("The C VM may not have been built with profiling support.")
        return

    total_instr = profile_data.pop("__total__")
    avg_ns_per_instr = (elapsed_s / total_instr * 1e9) if total_instr > 0 else 0.0
    instr_per_sec = (total_instr / elapsed_s) if elapsed_s > 0 else 0.0

    entries = [
        (name, count)
        for name, count in profile_data.items()
        if count > 0
    ]
    entries.sort(key=lambda e: e[1], reverse=True)

    print(f"{'Opcode':<{_OPCODE_COL}} {'Count':>{_COUNT_COL}} {'% of total':>{_PCT_COL}}")
    print(f"{'-' * _OPCODE_COL} {'-' * _COUNT_COL} {'-' * _PCT_COL}")

    for name, count in entries[:top_n]:
        pct = (count / total_instr * 100.0) if total_instr > 0 else 0.0
        print(f"{name:<{_OPCODE_COL}} {count:>{_COUNT_COL},} {pct:>{_PCT_COL - 1}.1f}%")

    print(f"{'-' * _OPCODE_COL} {'-' * _COUNT_COL} {'-' * _PCT_COL}")
    print(f"{'TOTAL':<{_OPCODE_COL}} {total_instr:>{_COUNT_COL},}")
    print()
    print(f"Wall-clock time:      {elapsed_s * 1000.0:.3f} ms")
    print(f"Instructions/sec:     {instr_per_sec:,.0f}")
    print(f"Avg time/instruction: {avg_ns_per_instr:.1f} ns")


def _report_execution_error(exc: Exception) -> None:
    """Print an execution error, with a traceback for non-Menai errors."""
    if isinstance(exc, MenaiError):
        print(f"Execution failed: {exc}", file=sys.stderr)
        return

    print(f"Execution failed: {exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


def render_result(result: MenaiValue, raw: bool) -> str:
    """
    Render an evaluation result for display.

    By default the result is rendered in its canonical Menai form via
    describe(), which quotes and escapes string values.  When raw is True a
    string result is rendered as its literal contents instead, so that
    embedded newlines and tabs appear as real characters rather than escapes.
    Non-string results are unaffected by raw and always use describe().
    """
    if raw and isinstance(result, MenaiString):
        return result.value

    return result.describe()


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the evaluator CLI."""
    parser = argparse.ArgumentParser(
        description="Compile and evaluate a Menai source file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        help='Menai source file to evaluate (use "-" to read from stdin)',
    )
    parser.add_argument(
        "--cprofile",
        action="store_true",
        help="Profile the compilation pipeline with Python's cProfile",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Profile VM execution with per-opcode frequency counting",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Trace VM execution with per-function instruction shares and call counts",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Annotate the disassembly of every function with per-instruction execution shares",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save raw cProfile data to FILE (requires --cprofile, viewable with pstats)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        metavar="N",
        help="Show top N entries in profile output (default: 40)",
    )
    parser.add_argument(
        "--sort",
        default="cumulative",
        choices=["cumulative", "time", "calls", "name", "filename"],
        help="Sort cProfile results by this metric (default: cumulative)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Render a string result as its literal contents instead of its quoted, escaped form",
    )
    return parser


def main() -> int:
    """Main entry point for the Menai evaluator."""
    parser = build_parser()
    args = parser.parse_args()

    if args.output and not args.cprofile:
        print("Error: --output requires --cprofile", file=sys.stderr)
        return 1

    color = use_color(args.no_color)

    source, source_path, name = read_source(args.input)
    module_path = build_module_path(source_path)

    print("Initialising Menai (compiling prelude)...", file=sys.stderr)
    menai = Menai(module_path=module_path)

    if args.cprofile:
        code = profile_compiler(
            source=source,
            name=name,
            menai=menai,
            top_n=args.top,
            sort_by=args.sort,
            output_file=args.output,
            color=color,
        )

    else:
        code, _ = compile_source(source, name, menai)

    if args.profile or args.trace or args.annotate:
        result = instrument_vm(
            menai, code, args.top, color,
            profile=args.profile, trace=args.trace, annotate=args.annotate,
        )

    else:
        try:
            result = menai.execute_raw(code)

        except Exception as exc:
            _report_execution_error(exc)
            return 1

    print()
    print_section("EVALUATION", color)
    rendered = render_result(result, args.raw)
    if args.raw and isinstance(result, MenaiString):
        print(rendered, end="" if rendered.endswith("\n") else "\n")

    else:
        print(f"Result: {rendered}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
