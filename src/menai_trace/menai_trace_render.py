"""
Rendering of a resolved trace as text.

The instruction listing reuses the disassembler's instruction formatting and
annotations, so a traced instruction line matches the corresponding
disassembly line with a leading execution-count column added.  This lets a hot
spot found in a trace be read directly against the disassembler output.
"""

from menai_render.menai_render_colour import green, grey, yellow
from menai_render.menai_render_instruction import (
    CONTROL_FLOW_OPCODES,
    annotate_instruction,
    clean_name,
    format_instruction,
    jump_targets,
)
from menai_trace.menai_trace_data import FunctionTrace, InstructionTrace, TraceResult

_SEPARATOR_WIDTH = 70
_COUNT_COL = 12
_FUNCTION_COL = 40


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


def render_trace(result: TraceResult, color: bool = False, top_n: int | None = None) -> list[str]:
    """
    Render a trace as a list of text lines.

    Args:
        result: The resolved trace to render.
        color:  Whether to emit ANSI colour codes.
        top_n:  When given, only the top N instructions per function are shown,
                ordered by execution count.  When None, all instructions are
                shown in instruction order.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []

    total_instr = result.total_instructions()
    total_calls = result.total_calls()

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("INSTRUCTION TRACE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Total instructions executed:':<32} {total_instr:>12,}")
    lines.append(f"{'Total function calls:':<32} {total_calls:>12,}")
    lines.append("")

    for function in result.functions:
        lines.extend(_render_function(function, total_instr, color, top_n))

    return lines


def _render_function(function: FunctionTrace, total_instr: int, color: bool, top_n: int | None) -> list[str]:
    """Render one function's instruction listing and call count."""
    lines: list[str] = []
    label = _function_label(function)

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(
        f"{yellow('Function: ', color)}{label}"
        f"{grey(f'  (ordinal {function.ordinal})', color)}"
    )
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Calls:':<12} {function.calls:>10,}")
    lines.append(f"{'Instructions executed:':<24} {function.total_executed():>10,}")
    lines.append("")

    traces = list(function.instructions)
    if top_n is not None:
        traces = sorted(traces, key=lambda t: t.count, reverse=True)[:top_n]

    lines.append(f"{'Count':>{_COUNT_COL}} {'% of total':>11}  Instruction")
    lines.append(f"{'-' * _COUNT_COL} {'-' * 11}  {'-' * 48}")

    for trace in traces:
        lines.append(_render_instruction(trace, function, total_instr, color))

    lines.append("")
    return lines


def _render_instruction(
    trace: InstructionTrace,
    function: FunctionTrace,
    total_instr: int,
    color: bool,
    target_marker: str = "  ",
) -> str:
    """Render one instruction line: count, percentage, then the disassembly line."""
    pct = (trace.count / total_instr * 100.0) if total_instr > 0 else 0.0
    instr_str = format_instruction(trace.instruction, trace.index, function.code)
    annotation = annotate_instruction(trace.instruction, function.code)
    prefix = f"{trace.count:>{_COUNT_COL},} {pct:>10.2f}%  {target_marker}"

    if trace.count == 0:
        return f"{grey(prefix + instr_str + annotation, color)}"

    if annotation:
        return f"{prefix}{instr_str}{green(annotation, color)}"

    return f"{prefix}{instr_str}"


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

    lines.append(f"{'Count':>{_COUNT_COL}} {'% of total':>11}    Instruction")
    lines.append(f"{'-' * _COUNT_COL} {'-' * 11}    {'-' * 48}")

    targets = jump_targets(function.code)

    for trace in function.instructions:
        is_target = trace.index in targets
        if is_target and trace.index > 0:
            lines.append("")

        # The marker sits before the count column so the numeric columns stay
        # aligned with non-target lines.
        marker = "\u25ba " if is_target else "  "
        lines.append(_render_instruction(trace, function, total_instr, color, marker))

        # Blank line after a control flow opcode, unless the next instruction is
        # already a jump target (which inserts its own blank line above).
        if trace.instruction.opcode in CONTROL_FLOW_OPCODES and (trace.index + 1) not in targets:
            lines.append("")

    lines.append("")
    return lines


def render_call_summary(result: TraceResult, color: bool = False) -> list[str]:
    """Render a per-function call-count summary, ordered by call count."""
    lines: list[str] = []

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("CALL SUMMARY", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    ordered = sorted(result.functions, key=lambda f: f.calls, reverse=True)
    lines.append(f"{'Calls':>{_COUNT_COL}}  {'Function':<{_FUNCTION_COL}}")
    lines.append(f"{'-' * _COUNT_COL}  {'-' * _FUNCTION_COL}")

    for function in ordered:
        label = _function_label(function)
        if len(label) > _FUNCTION_COL:
            label = label[:_FUNCTION_COL - 3] + "..."

        lines.append(f"{function.calls:>{_COUNT_COL},}  {label:<{_FUNCTION_COL}}")

    return lines


def render_hot_instructions(result: TraceResult, color: bool = False, top_n: int = 20) -> list[str]:
    """Render the hottest individual instructions across the whole program."""
    lines: list[str] = []

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow(f"HOT INSTRUCTIONS  (top {top_n})", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    total_instr = result.total_instructions()

    flattened: list[tuple[FunctionTrace, InstructionTrace]] = []
    for function in result.functions:
        for trace in function.instructions:
            if trace.count > 0:
                flattened.append((function, trace))

    flattened.sort(key=lambda pair: pair[1].count, reverse=True)

    lines.append(f"{'Count':>{_COUNT_COL}} {'% of total':>11}  {'Function':<28} Instruction")
    lines.append(f"{'-' * _COUNT_COL} {'-' * 11}  {'-' * 28} {'-' * 40}")

    for function, trace in flattened[:top_n]:
        pct = (trace.count / total_instr * 100.0) if total_instr > 0 else 0.0
        label = clean_name(function.code.name) if function.code.name else "<anonymous>"
        if len(label) > 28:
            label = label[:25] + "..."

        instr_str = f"{trace.index:4}: {trace.instruction.format(function.code)}"
        if len(instr_str) > 40:
            instr_str = instr_str[:37] + "..."

        lines.append(f"{trace.count:>{_COUNT_COL},} {pct:>10.2f}%  {label:<28} {instr_str}")

    return lines


def render_full_trace(result: TraceResult, color: bool = False, top_n: int | None = None) -> list[str]:
    """Render the complete trace: hot instructions, call summary, and per-function detail."""
    lines = render_hot_instructions(result, color)
    lines.append("")
    lines.extend(render_call_summary(result, color))
    lines.append("")
    lines.extend(render_trace(result, color, top_n))
    return lines
