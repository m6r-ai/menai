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

_SEPARATOR_WIDTH = 70
_COUNT_COL = 12
_FUNCTION_COL = 40
_PCT_COL = 11


def _function_label(function: FunctionTrace) -> str:
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
        label = _function_label(function)
        if len(label) > _FUNCTION_COL:
            label = label[:_FUNCTION_COL - 3] + "..."

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
    label = _function_label(function)
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
