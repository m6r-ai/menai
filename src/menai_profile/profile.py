#!/usr/bin/env python3
"""
Menai Profiler - Compile and profile any Menai source file.

Reads a Menai source file, compiles it, and profiles execution.  Two
profiling modes are available:

  --mode cprofile  (default)  Python-level cProfile of the execution.
  --mode opcode               VM-level opcode frequency profiling (requires
                              the C VM to be built with profiling support).

Opcode mode counts how many times each opcode is executed and measures
total wall-clock time.  This reveals which opcodes are hot (dominant by
frequency) and the overall instruction throughput.  Per-opcode time
attribution is not provided because the per-instruction timer overhead
(~100ns) exceeds the cost of most opcodes (~4ns), making per-instruction
timing unreliable.

Module paths are resolved the same way as the disassembler: the file's
own directory first, then the current working directory.

Usage:
    python menai_profiler.py <file.menai>
    python menai_profiler.py <file.menai> --mode opcode
    python menai_profiler.py <file.menai> --output stats.prof
    python menai_profiler.py <file.menai> --top 50
    python menai_profiler.py <file.menai> --sort time
"""

import argparse
import cProfile
import pstats
import sys
import time
import traceback
from io import StringIO
from pathlib import Path

from menai import Menai
from menai.menai_compiler import MenaiCompiler


def build_module_path(source_path: Path) -> list[str]:
    """
    Build a deduplicated module search path for the given source file.

    Mirrors the strategy used by the disassembler:
      1. The file's own directory (so bare module names resolve next to the file)
      2. The current working directory (so project-root-relative import paths work)
    """
    file_dir = str(source_path.parent.absolute())
    cwd = str(Path.cwd())
    module_path: list[str] = []
    for d in [file_dir, cwd]:
        if d not in module_path:
            module_path.append(d)

    return module_path


def compile_source(source_path: Path) -> object:
    """
    Compile a Menai source file and return the CodeObject.

    Args:
        source_path: Path to the .menai file.

    Returns:
        Compiled CodeObject.

    Raises:
        SystemExit: If compilation fails.
    """
    source = source_path.read_text(encoding="utf-8")
    module_path = build_module_path(source_path)
    menai = Menai(module_path=module_path)

    compiler = MenaiCompiler(module_loader=menai)
    try:
        return compiler.compile(source, name=str(source_path))

    except Exception as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def run_cprofile(
    source_path: Path,
    output_file: str | None,
    top_n: int,
    sort_by: str,
) -> int:
    """
    Compile the file, execute it under cProfile, and display results.

    Args:
        source_path:     Path to the .menai source file.
        output_file:     Optional path to save raw profile data (.prof).
        top_n:           Number of top functions to print.
        sort_by:         pstats sort key.

    Returns:
        Exit code (0 = success, 1 = execution error).
    """
    source = source_path.read_text(encoding="utf-8")
    module_path = build_module_path(source_path)

    print("Initialising Menai (compiling prelude)...", file=sys.stderr)
    menai = Menai(module_path=module_path)

    print(f"Compiling: {source_path}", file=sys.stderr)
    compiler = MenaiCompiler(module_loader=menai)
    try:
        code = compiler.compile(source, name=str(source_path))

    except Exception as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print("Running with cProfile...", file=sys.stderr)
    print("=" * 100)

    profiler = cProfile.Profile()
    profiler.enable()

    result = None
    exec_error = None
    try:
        result = menai.execute_raw(code)

    except Exception as exc:
        exec_error = exc

    finally:
        profiler.disable()

    if exec_error is not None:
        print(f"\n\u2717 Execution failed: {exec_error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print("\n\u2713 Execution completed successfully")
    print(f"Result: {result.describe() if result is not None else '<none>'}")

    print("\n" + "=" * 100)
    print(f"PROFILING RESULTS (Top {top_n} functions, sorted by {sort_by})")
    print("=" * 100)

    s = StringIO()
    stats = pstats.Stats(profiler, stream=s)
    stats.sort_stats(sort_by)
    stats.print_stats(top_n)
    print(s.getvalue())

    if output_file:
        profiler.dump_stats(output_file)
        print(f"\n\u2713 Profile data saved to: {output_file}")
        print(f"  View with: python -m pstats {output_file}")
        print(f"  Or:        snakeviz {output_file}")

    return 0


def run_opcode_profile(
    source_path: Path,
    top_n: int,
) -> int:
    """
    Compile the file, execute it with VM-level opcode frequency profiling.

    Counts per-opcode execution frequency in the C VM (one increment per
    instruction, negligible overhead) and measures total wall-clock time.
    Reports which opcodes are hottest by frequency, overall instruction
    throughput, and average time per instruction.

    Args:
        source_path:     Path to the .menai source file.
        top_n:           Number of top opcodes to print.

    Returns:
        Exit code (0 = success, 1 = execution error).
    """
    source = source_path.read_text(encoding="utf-8")
    module_path = build_module_path(source_path)

    print("Initialising Menai (compiling prelude)...", file=sys.stderr)
    menai = Menai(module_path=module_path)

    print(f"Compiling: {source_path}", file=sys.stderr)
    compiler = MenaiCompiler(module_loader=menai)
    try:
        code = compiler.compile(source, name=str(source_path))

    except Exception as exc:
        print(f"Compilation error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print("Running with opcode profiling...", file=sys.stderr)
    print("=" * 100)

    # Warm up: run once without profiling so first-run costs (page faults,
    # code cache warming) don't skew the timed measurement.
    menai.execute_raw(code)

    # Timed run with opcode counting enabled.
    menai.vm.enable_profiling()

    t0 = time.perf_counter()
    result = None
    exec_error = None
    try:
        result = menai.execute_raw(code)

    except Exception as exc:
        exec_error = exc

    t1 = time.perf_counter()

    if exec_error is not None:
        print(f"\n\u2717 Execution failed: {exec_error}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    print("\n\u2713 Execution completed successfully")
    print(f"Result: {result.describe() if result is not None else '<none>'}")

    profile_data = menai.vm.get_profile_data()

    if not profile_data or profile_data.get("__total__", 0) == 0:
        print("\n" + "=" * 100)
        print("WARNING: No opcode profiling data collected.")
        print("Profiling was not enabled before execution.")
        print("Make sure enable_profiling() is called before execute_raw().")
        print("=" * 100)
        return 0

    total_instr = profile_data.pop("__total__")
    elapsed_s = t1 - t0
    avg_ns_per_instr = (elapsed_s / total_instr * 1e9) if total_instr > 0 else 0.0
    instr_per_sec = (total_instr / elapsed_s) if elapsed_s > 0 else 0.0

    print("\n" + "=" * 100)
    print(f"OPCODE FREQUENCY PROFILE (Top {top_n} opcodes, sorted by count)")
    print("=" * 100)

    entries = [
        (name, count)
        for name, count in profile_data.items()
        if count > 0
    ]
    entries.sort(key=lambda e: e[1], reverse=True)

    print(f"{'Opcode':<40} {'Count':>15} {'% of total':>12}")
    print("-" * 70)

    for name, count in entries[:top_n]:
        pct = (count / total_instr * 100.0) if total_instr > 0 else 0.0
        print(f"{name:<40} {count:>15,} {pct:>11.1f}%")

    print("-" * 70)
    print(f"{'TOTAL':<40} {total_instr:>15,}")
    print()
    print(f"Wall-clock time:     {elapsed_s * 1000.0:.3f} ms")
    print(f"Instructions/sec:   {instr_per_sec:,.0f}")
    print(f"Avg time/instruction: {avg_ns_per_instr:.1f} ns")
    print("=" * 100)

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Compile and profile a Menai source file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("file", help="Menai source file to profile")
    parser.add_argument(
        "--mode",
        default="cprofile",
        choices=["cprofile", "opcode"],
        help="Profiling mode: cprofile (Python-level) or opcode (VM-level)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save raw profile data to FILE (cprofile mode only, viewable with pstats)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        metavar="N",
        help="Show top N entries in the profile output (default: 40)",
    )
    parser.add_argument(
        "--sort",
        default="cumulative",
        choices=["cumulative", "time", "calls", "name", "filename"],
        help="Sort cprofile results by this metric (default: cumulative)",
    )

    args = parser.parse_args()

    source_path = Path(args.file)
    if not source_path.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    if args.mode == "opcode":
        return run_opcode_profile(
            source_path=source_path,
            top_n=args.top,
        )

    return run_cprofile(
        source_path=source_path,
        output_file=args.output,
        top_n=args.top,
        sort_by=args.sort,
    )


if __name__ == "__main__":
    sys.exit(main())
