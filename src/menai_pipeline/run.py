#!/usr/bin/env python3
"""Command-line interface for the pipeline engine."""

import argparse
from collections.abc import Callable
import cProfile
import io
from pathlib import Path
import pstats
import sys
import time

from menai_pipeline.pipeline_engine import PipelineResult, StepResult, execute_pipeline
from menai_pipeline.pipeline_optimizer import optimize_pipeline
from menai_pipeline.pipeline_parser import PipelineParseError, load_pipeline
from menai_pipeline.pipeline_step import MenaiStep, Pipeline, ToolStep
from menai_trace.menai_trace_render import render_annotated, render_function_summary


_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_GREY = "\033[90m"
_ANSI_RESET = "\033[0m"

_SEPARATOR_WIDTH = 70
_OPCODE_COL = 40
_COUNT_COL = 15
_PCT_COL = 12


def _use_color(no_color: bool) -> bool:
    """Return True if ANSI colour output should be used."""
    return not no_color and sys.stdout.isatty()


def _format_elapsed(seconds: float) -> str:
    """Format an elapsed time value for display."""
    if seconds >= 1.0:
        return f"{seconds:.3f}s"

    return f"{seconds * 1000:.1f}ms"


def _make_step_callbacks(
    pipeline: Pipeline,
    verbosity: int,
    timings: bool,
    color: bool,
) -> tuple[Callable[[str], None], Callable[[StepResult], None], Callable[[], None]]:
    """
    Build live step callbacks for verbose pipeline execution.

    Returns a triple of (on_step_start, on_step_done, print_final_block).

    on_step_start prints a separator followed by the full status block: all previously
    completed steps with their final status, and the current step marked as Running.
    on_step_done records the completed result so it appears in the next status block,
    and at verbosity >= 2 appends the truncated debug value beneath the status block.
    print_final_block prints the closing separator and final status block after the
    last step completes.
    """
    step_ids = [s.step_id for s in pipeline.steps]
    num_width = len(str(len(step_ids)))
    id_width = max((len(s) for s in step_ids), default=8)
    _dashes = "-" * (num_width + 2 + id_width + 20)
    separator = f"{_ANSI_GREY}{_dashes}{_ANSI_RESET}" if color else _dashes

    step_index: dict[str, int] = {sid: i for i, sid in enumerate(step_ids)}
    console_step_ids: set[str] = {
        s.step_id for s in pipeline.steps
        if isinstance(s, ToolStep) and s.tool == "console"
    }
    pending: list[StepResult | None] = [None]

    def _flush_pending() -> None:
        prev = pending[0]
        if prev is None:
            return

        i = step_index[prev.step_id]
        if prev.success:
            status = f"{_ANSI_GREEN}OK{_ANSI_RESET}" if color else "OK"

        else:
            status = f"{_ANSI_RED}FAIL{_ANSI_RESET}" if color else "FAIL"

        timing_str = f"  {_format_elapsed(prev.elapsed_s):>8}" if timings else ""

        if not prev.success:
            error_text = f"{_ANSI_RED}{prev.error}{_ANSI_RESET}" if color else prev.error
            print(error_text)
            print(separator)

        elif verbosity >= 2 and prev.value:
            if prev.step_id in console_step_ids:
                print(separator)

            truncated = prev.value
            if len(truncated) > 120:
                truncated = truncated[:117] + "..."

            value_text = f"{_ANSI_GREEN}{truncated}{_ANSI_RESET}" if color else truncated
            print(value_text)
            print(separator)

        elif prev.step_id in console_step_ids:
            print(separator)

        print(f"{i + 1:{num_width}}. {prev.step_id} <- {status}{timing_str}")

        pending[0] = None

    def on_step_start(step_id: str) -> None:
        _flush_pending()
        print(separator)
        i = step_index[step_id]
        running = f"{_ANSI_CYAN}Running{_ANSI_RESET}" if color else "Running"
        print(f"{i + 1:{num_width}}. {step_id} <- {running}")
        print(separator)

    def on_step_done(step_result: StepResult) -> None:
        pending[0] = step_result

    def print_final_block() -> None:
        _flush_pending()
        print(separator)

    return on_step_start, on_step_done, print_final_block


def _print_timings_bar(result: PipelineResult) -> None:
    """Print a simple proportional timing bar for each step."""
    total = sum(r.elapsed_s for r in result.step_results)
    if total == 0:
        return

    bar_width = 40
    id_width = max((len(r.step_id) for r in result.step_results), default=8)

    print("Timing breakdown:")
    for step_result in result.step_results:
        proportion = step_result.elapsed_s / total
        filled = round(proportion * bar_width)
        text_bar = "█" * filled + "░" * (bar_width - filled)
        pct = proportion * 100
        print(
            f"  {step_result.step_id:<{id_width}}  [{text_bar}]  "
            f"{_format_elapsed(step_result.elapsed_s):>8}  ({pct:4.1f}%)"
        )


def _print_pipeline_summary(
    pipeline_path: Path,
    optimized: bool,
    step_count_before: int,
    step_count_after: int
) -> None:
    """Print a summary of the pipeline structure."""
    print(f"Pipeline: {pipeline_path}")
    print(f"Steps: {step_count_before}", end="")
    if optimized and step_count_after < step_count_before:
        collapsed = step_count_before - step_count_after
        print(f" -> {step_count_after} ({collapsed} Menai step(s) collapsed)", end="")

    print()


def _run_with_profile(
    pipeline: Pipeline,
    sort_key: str,
    lines: int,
    on_step_start: Callable[[str], None] | None,
    on_step_done: Callable[[StepResult], None] | None,
    instrument: bool,
) -> tuple[PipelineResult, str]:
    """
    Execute a pipeline under cProfile and return (result, profile_stats_string).

    Args:
        pipeline: The pipeline to execute
        sort_key: pstats sort key (e.g. 'cumulative', 'tottime')
        lines: Number of functions to show in the profile output
        on_step_start: Optional step-start callback forwarded to execute_pipeline
        on_step_done: Optional step-done callback forwarded to execute_pipeline
        instrument: Whether to enable VM profiling during execution

    Returns:
        Tuple of (PipelineResult, formatted profile string)
    """
    profiler = cProfile.Profile()
    profiler.enable()
    result = execute_pipeline(
        pipeline,
        on_step_start=on_step_start,
        on_step_done=on_step_done,
        instrument=instrument,
    )
    profiler.disable()

    buf = io.StringIO()
    stats = pstats.Stats(profiler, stream=buf)
    stats.strip_dirs()
    stats.sort_stats(sort_key)
    stats.print_stats(lines)

    return result, buf.getvalue()


def _separator(color: bool) -> str:
    """Return a full-width section separator, optionally coloured grey."""
    line = "\u2500" * _SEPARATOR_WIDTH
    return f"{_ANSI_GREY}{line}{_ANSI_RESET}" if color else line


def _print_opcode_profile(result: PipelineResult, top_n: int, color: bool) -> None:
    """
    Print the per-opcode frequency histogram aggregated across all Menai steps.

    Opcode counts are additive across steps, so the aggregate is a meaningful
    whole-pipeline view.  A per-step breakdown follows when the pipeline has
    more than one Menai step.
    """
    profiles = [p for p in result.step_profiles if p.opcode_counts]
    if not profiles:
        return

    aggregate: dict[str, int] = {}
    for profile in profiles:
        for name, count in profile.opcode_counts.items():
            aggregate[name] = aggregate.get(name, 0) + count

    total_instr = aggregate.pop("__total__", 0)

    print()
    print(_separator(color))
    print(f"VM OPCODE PROFILE  (top {top_n} by count)")
    print(_separator(color))

    if total_instr == 0:
        print("No opcode profiling data collected.")
        print("The C VM may not have been built with profiling support.")
        return

    entries = [(name, count) for name, count in aggregate.items() if count > 0]
    entries.sort(key=lambda e: e[1], reverse=True)

    print(f"{'Opcode':<{_OPCODE_COL}} {'Count':>{_COUNT_COL}} {'% of total':>{_PCT_COL}}")
    print(f"{'-' * _OPCODE_COL} {'-' * _COUNT_COL} {'-' * _PCT_COL}")

    for name, count in entries[:top_n]:
        pct = (count / total_instr * 100.0) if total_instr > 0 else 0.0
        print(f"{name:<{_OPCODE_COL}} {count:>{_COUNT_COL},} {pct:>{_PCT_COL - 1}.1f}%")

    print(f"{'-' * _OPCODE_COL} {'-' * _COUNT_COL} {'-' * _PCT_COL}")
    print(f"{'TOTAL':<{_OPCODE_COL}} {total_instr:>{_COUNT_COL},}")

    if len(profiles) > 1:
        print()
        print("Per step:")
        for profile in profiles:
            step_total = profile.opcode_counts.get("__total__", 0)
            print(f"  {profile.step_id:<24} {step_total:>{_COUNT_COL},}")


def _print_function_profiles(result: PipelineResult, top_n: int, color: bool) -> None:
    """Print a per-function summary for each Menai step that has trace data."""
    profiles = [p for p in result.step_profiles if p.trace is not None]
    if not profiles:
        return

    print()
    print(_separator(color))
    print(f"VM FUNCTION PROFILE  (top {top_n} per step)")
    print(_separator(color))

    for profile in profiles:
        assert profile.trace is not None
        print()
        print(f"  {profile.step_id}")
        for line in render_function_summary(profile.trace, color=color, top_n=top_n):
            print(f"  {line}")


def _print_annotated(result: PipelineResult, color: bool) -> None:
    """Print the annotated disassembly for each Menai step that has trace data."""
    profiles = [p for p in result.step_profiles if p.trace is not None]
    if not profiles:
        return

    print()
    print(_separator(color))
    print("VM ANNOTATED TRACE")
    print(_separator(color))

    for profile in profiles:
        assert profile.trace is not None
        print()
        print(f"  {profile.step_id}")
        for line in render_annotated(profile.trace, color=color):
            print(f"  {line}")


def main() -> int:
    """
    Entry point for the pipeline CLI.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description="Execute a Menai pipeline from a JSON definition file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m menai_pipeline.run examples/hello-timestamp/pipeline.json
  python -m menai_pipeline.run --no-optimize examples/adjacent-collapse/pipeline.json
  python -m menai_pipeline.run -v examples/file-transform/pipeline.json
  python -m menai_pipeline.run -v --timings examples/clock-and-file/pipeline.json
  python -m menai_pipeline.run --cprofile examples/file-transform/pipeline.json
        """
    )

    parser.add_argument(
        "pipeline",
        type=Path,
        help="Path to the pipeline JSON file"
    )

    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Disable adjacent Menai step collapsing"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        dest="verbosity",
        help="Increase verbosity: -v shows live step status, -vv also shows truncated step output"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate the pipeline without executing it"
    )

    parser.add_argument(
        "--timings", "-t",
        action="store_true",
        help="Show per-step elapsed time and a proportional timing bar"
    )

    parser.add_argument(
        "--cprofile",
        action="store_true",
        help="Run the pipeline under Python's cProfile and print the top hotspots"
    )

    parser.add_argument(
        "--cprofile-lines",
        type=int,
        default=30,
        metavar="N",
        help="Number of functions to show in cProfile output (default: 30)"
    )

    parser.add_argument(
        "--cprofile-sort",
        default="cumulative",
        choices=["cumulative", "tottime", "calls", "filename"],
        help="Sort key for cProfile output (default: cumulative)"
    )

    parser.add_argument(
        "--profile",
        action="store_true",
        dest="profile",
        help=(
            "Profile VM execution per Menai step, ranking functions by the "
            "number of instructions they executed"
        )
    )

    parser.add_argument(
        "--opcodes",
        action="store_true",
        dest="opcodes",
        help=(
            "Profile VM execution per Menai step with per-opcode frequency "
            "counting, aggregated across the pipeline"
        )
    )

    parser.add_argument(
        "--annotate",
        action="store_true",
        dest="annotate",
        help=(
            "Annotate the disassembly of every function in each Menai step "
            "with per-instruction execution shares"
        )
    )

    parser.add_argument(
        "--top",
        type=int,
        default=40,
        metavar="N",
        dest="top",
        help="Show top N entries in VM profile output (default: 40)"
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output"
    )

    args = parser.parse_args()

    color = _use_color(args.no_color)

    pipeline_path: Path = args.pipeline

    try:
        pipeline = load_pipeline(pipeline_path)

    except PipelineParseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    step_count_before = len(pipeline.steps)
    optimized = not args.no_optimize

    if optimized:
        pipeline = optimize_pipeline(pipeline)

    step_count_after = len(pipeline.steps)

    if args.dry_run:
        _print_pipeline_summary(pipeline_path, optimized, step_count_before, step_count_after)
        print()
        for step in pipeline.steps:
            if isinstance(step, MenaiStep):
                print(f"  menai    {step.step_id}")
                if step.module:
                    print(f"             module: {step.module}")

                for name, src in step.inputs.items():
                    print(f"             input:  {name} <- {src}")

                for key, target in step.outputs.items():
                    print(f"             output: {key} -> {target}")

            elif isinstance(step, ToolStep):
                print(f"  {step.tool:<8} {step.step_id}  ({step.operation})")
                if step.value_from:
                    print(f"             value_from: {step.value_from}")

        print()
        print("Pipeline is valid.")
        return 0

    if args.verbosity >= 1:
        _print_pipeline_summary(pipeline_path, optimized, step_count_before, step_count_after)
        print()

    profile_output = None
    start = time.monotonic()
    instrument = args.profile or args.opcodes or args.annotate

    on_step_start = None
    on_step_done = None
    print_final_block = None

    if args.verbosity >= 1:
        on_step_start, on_step_done, print_final_block = _make_step_callbacks(
            pipeline, args.verbosity, args.timings, color
        )

    if args.cprofile:
        result, profile_output = _run_with_profile(
            pipeline,
            args.cprofile_sort,
            args.cprofile_lines,
            on_step_start,
            on_step_done,
            instrument,
        )

    else:
        result = execute_pipeline(
            pipeline,
            on_step_start=on_step_start,
            on_step_done=on_step_done,
            instrument=instrument,
        )

    elapsed = time.monotonic() - start

    if print_final_block is not None:
        print_final_block()

    if args.timings and result.step_results:
        print()
        _print_timings_bar(result)

    if profile_output:
        print()
        print(f"cProfile (top {args.cprofile_lines} by {args.cprofile_sort}):")
        for line in profile_output.splitlines():
            print(line)

    if args.opcodes:
        _print_opcode_profile(result, args.top, color)

    if args.profile:
        _print_function_profiles(result, args.top, color)

    if args.annotate:
        _print_annotated(result, color)

    if result.success:
        if args.verbosity >= 1:
            print()
            print(f"Pipeline completed successfully in {_format_elapsed(elapsed)}")

        return 0

    print(f"Pipeline failed: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
