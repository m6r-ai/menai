"""
Rendering of a resolved trace as text.

Two views are provided:

  * A per-function summary, ranking functions by the number of instructions
    they executed and showing each function's share of the program total.
  * An annotated disassembly, which reuses the disassembler's instruction
    formatting, annotations, and metadata tables so that a traced function
    matches the corresponding disassembly function with a leading
    execution-count column added.  This lets a hot spot be read directly against
    the disassembler output.
"""

from menai_render.menai_render_colour import grey, yellow
from menai_render.menai_render_instruction import (
    clean_name,
    render_code_metadata,
    render_instruction_lines,
)
from menai_trace.menai_trace_data import FunctionTrace, TraceResult
from menai_trace.menai_trace_coverage import CoverageResult, CumulativeCoverage, FunctionCoverage

_SEPARATOR_WIDTH = 70
_COUNT_COL = 12
_FUNCTION_COL = 40
_PCT_COL = 11


def function_label(function: FunctionTrace | FunctionCoverage) -> str:
    """Return a display label for a function, with its source location if known."""
    code = function.code
    name = clean_name(code.name) if code.name else "<anonymous>"
    loc_parts = []
    if code.source_file:
        loc_parts.append(code.source_file)

    if code.source_line and code.source_line > 0:
        loc_parts.append(f"line {code.source_line}")

    if loc_parts:
        return f"{name} [{':'.join(loc_parts)}]"

    return name


def render_function_summary(result: TraceResult, color: bool = False, top_n: int | None = None) -> list[str]:
    """
    Render a per-function summary, ranked by instructions executed.

    Each function is shown with the number of instructions it executed, its
    share of the total instructions executed by the whole program, and its call
    count.  Functions are ordered by instructions executed, most first, so the
    dominant functions appear at the top.

    Percentages are of instruction count, not time.  Per-instruction timing is
    not measurable at these speeds (see the menai_eval README), so the share of
    executed instructions is the meaningful signal.

    Args:
        result: The resolved trace to render.
        color:  Whether to emit ANSI colour codes.
        top_n:  When given, only the top N functions are shown.  When None, all
                functions are shown.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []

    total_instr = result.total_instructions()
    total_calls = result.total_calls()

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("FUNCTION TRACE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Total instructions executed:':<32} {total_instr:>12,}")
    lines.append(f"{'Total function calls:':<32} {total_calls:>12,}")
    lines.append("")

    ordered = sorted(result.functions, key=lambda f: f.total_executed(), reverse=True)
    if top_n is not None:
        ordered = ordered[:top_n]

    lines.append(
        f"{'Instructions':>{_COUNT_COL}} {'% of total':>{_PCT_COL}} "
        f"{'Calls':>{_COUNT_COL}}  {'Function':<{_FUNCTION_COL}}"
    )
    lines.append(
        f"{'-' * _COUNT_COL} {'-' * _PCT_COL} {'-' * _COUNT_COL}  {'-' * _FUNCTION_COL}"
    )

    for function in ordered:
        executed = function.total_executed()
        pct = (executed / total_instr * 100.0) if total_instr > 0 else 0.0
        label = function_label(function)
        lines.append(
            f"{executed:>{_COUNT_COL},} {pct:>{_PCT_COL - 1}.2f}% "
            f"{function.calls:>{_COUNT_COL},}  {label:<{_FUNCTION_COL}}"
        )

    lines.append(
        f"{'-' * _COUNT_COL} {'-' * _PCT_COL} {'-' * _COUNT_COL}  {'-' * _FUNCTION_COL}"
    )
    lines.append(
        f"{total_instr:>{_COUNT_COL},} {'100.00%':>{_PCT_COL}} "
        f"{total_calls:>{_COUNT_COL},}  {'TOTAL':<{_FUNCTION_COL}}"
    )

    return lines


def render_annotated(result: TraceResult, color: bool = False) -> list[str]:
    """
    Render every function in disassembly order, annotated with execution share.

    Each instruction is shown in instruction order with its execution count and
    its percentage of the total instructions executed by the whole program.
    This is the perf-annotate view: the full body of every function is visible,
    so a hot region can be read in context rather than only as a sorted list of
    the hottest instructions.

    Percentages are of instruction count, not time.  Per-instruction timing is
    not measurable at these speeds (see the menai_eval README), so the share of
    executed instructions is the meaningful signal.

    Args:
        result: The resolved trace to render.
        color:  Whether to emit ANSI colour codes.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    total_instr = result.total_instructions()

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("ANNOTATED TRACE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Total instructions executed:':<32} {total_instr:>12,}")
    lines.append("")

    for function in result.functions:
        lines.extend(_render_annotated_function(function, total_instr, color))

    return lines


def _render_annotated_function(function: FunctionTrace, total_instr: int, color: bool) -> list[str]:
    """Render one function's full instruction listing with execution shares."""
    lines: list[str] = []
    label = function_label(function)
    executed = function.total_executed()
    share = (executed / total_instr * 100.0) if total_instr > 0 else 0.0

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(
        f"{yellow('Function: ', color)}{label}"
        f"{grey(f'  (ordinal {function.ordinal})', color)}"
    )
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Calls:':<12} {function.calls:>10,}")
    lines.append(f"{'Instructions executed:':<24} {executed:>10,}   {share:>6.2f}% of total")
    lines.append("")

    lines.extend(render_code_metadata(function.code, color=color))

    lines.append(f"{'Count':>{_COUNT_COL}} {'% of total':>11}    Instruction")
    lines.append(f"{'-' * _COUNT_COL} {'-' * 11}    {'-' * 48}")

    counts = {trace.index: trace.count for trace in function.instructions}

    lines.extend(
        render_instruction_lines(
            function.code,
            lambda index, _instr: _count_prefix(counts[index], total_instr),
            color=color,
            dim_predicate=lambda index, _instr: counts[index] == 0,
        )
    )

    lines.append("")
    return lines


def _count_prefix(count: int, total_instr: int) -> str:
    """Return the leading execution-count and percentage column for a trace line."""
    pct = (count / total_instr * 100.0) if total_instr > 0 else 0.0
    return f"{count:>{_COUNT_COL},} {pct:>10.2f}%  "


def render_coverage(result: CoverageResult, color: bool = False, top_n: int | None = None) -> list[str]:
    """
    Render bytecode coverage as a summary, a per-function table, and uncovered ranges.

    The table is ordered by coverage, least covered first, so the functions with
    the most unexecuted bytecode appear at the top.  `top_n` limits the table
    only; the uncovered-instruction ranges below it always cover every function,
    because the point of a coverage report is to name what did not run.

    Instructions that an optimisation pass removed before bytecode emission have
    no instructions in the tree and do not appear.  Coverage is over the bytecode
    the VM was actually given.

    Args:
        result: The coverage result to render.
        color:  Whether to emit ANSI colour codes.
        top_n:  When given, only the first N functions are shown in the table.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    total = result.total_instructions()
    entered_pct = (
        result.entered_function_count() / result.function_count() * 100.0
        if result.function_count()
        else 0.0
    )

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("BYTECODE COVERAGE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Total instructions:':<32} {total:>12,}")
    lines.append(
        f"{'Executed:':<32} {result.covered_instructions():>12,} "
        f"{result.coverage_percent():>10.2f}%"
    )
    lines.append(f"{'Not executed:':<32} {result.uncovered_instructions():>12,}")
    lines.append(f"{'Code objects:':<32} {result.function_count():>12,}")
    lines.append(
        f"{'Entered:':<32} {result.entered_function_count():>12,} "
        f"{entered_pct:>10.2f}%"
    )
    lines.append("")

    ordered = sorted(result.functions, key=lambda function: (function.coverage_percent(), function.ordinal))
    if top_n is not None:
        ordered = ordered[:top_n]

    lines.append(
        f"{'Executed':>10} {'Total':>10} {'Coverage':>10} {'Calls':>10}  "
        f"{'Function':<{_FUNCTION_COL}}"
    )
    lines.append(
        f"{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}  {'-' * _FUNCTION_COL}"
    )

    for function in ordered:
        label = function_label(function)
        lines.append(
            f"{function.covered_instructions():>10,} {function.total_instructions():>10,} "
            f"{function.coverage_percent():>9.2f}% {function.calls:>10,}  {label:<{_FUNCTION_COL}}"
        )

    lines.append("")
    lines.append(yellow("Unexecuted instructions", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    any_uncovered = False
    for function in result.functions:
        uncovered = function.uncovered_instructions()
        if not uncovered:
            continue

        any_uncovered = True
        indices = format_index_ranges([instruction.index for instruction in uncovered])
        lines.append(f"  {function_label(function)}  {indices}")

    if not any_uncovered:
        lines.append("  None")

    return lines


def format_index_ranges(indices: list[int]) -> str:
    """Format instruction indices as a compact comma-separated range list."""
    if not indices:
        return "none"

    parts: list[str] = []
    start = previous = indices[0]

    for index in indices[1:]:
        if index == previous + 1:
            previous = index
            continue

        parts.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = index

    parts.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(parts)


def render_cumulative_coverage(
    result: CumulativeCoverage,
    color: bool = False,
    top_n: int | None = None,
) -> list[str]:
    """
    Render coverage merged across every program that contributed to a run.

    Each function is counted once, at its source identity, no matter how many
    test files or benchmark suites compiled it.  The table is ordered by
    coverage, least covered first, and `top_n` limits it.  The unexecuted
    instruction ranges always cover every function.

    Code objects with no source file (module roots) are excluded when the
    coverage is accumulated, so this report contains only functions with a
    stable source identity.

    Args:
        result: The cumulative coverage to render.
        color:  Whether to emit ANSI colour codes.
        top_n:  When given, only the first N functions are shown in the table.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    total = result.total_instructions()
    entered_pct = (
        result.entered_function_count() / result.function_count() * 100.0
        if result.function_count()
        else 0.0
    )

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("CUMULATIVE BYTECODE COVERAGE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Total instructions:':<32} {total:>12,}")
    lines.append(
        f"{'Executed:':<32} {result.covered_instructions():>12,} "
        f"{result.coverage_percent():>10.2f}%"
    )
    lines.append(f"{'Not executed:':<32} {result.uncovered_instructions():>12,}")
    lines.append(f"{'Functions:':<32} {result.function_count():>12,}")
    lines.append(
        f"{'Entered:':<32} {result.entered_function_count():>12,} "
        f"{entered_pct:>10.2f}%"
    )
    lines.append("")

    ordered = list(result.ordered_functions())
    if top_n is not None:
        ordered = ordered[:top_n]

    lines.append(
        f"{'Executed':>10} {'Total':>10} {'Coverage':>10} {'Calls':>10}  "
        f"{'Function':<{_FUNCTION_COL}}"
    )
    lines.append(
        f"{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}  {'-' * _FUNCTION_COL}"
    )

    for function in ordered:
        label = function.label()
        lines.append(
            f"{function.covered_instructions():>10,} {function.total_instructions():>10,} "
            f"{function.coverage_percent():>9.2f}% {function.calls:>10,}  {label:<{_FUNCTION_COL}}"
        )

    lines.append("")
    lines.append(yellow("Unexecuted instructions", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    any_uncovered = False
    for function in result.ordered_functions():
        uncovered = function.uncovered_indices()
        if not uncovered:
            continue

        any_uncovered = True
        lines.append(f"  {function.label()}  {format_index_ranges(list(uncovered))}")

    if not any_uncovered:
        lines.append("  None")

    return lines
