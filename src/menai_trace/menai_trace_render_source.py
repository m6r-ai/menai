"""
Rendering of a single source file's function coverage.

The report answers two questions at once.  Which functions did the test suite
not fully exercise — the test gaps — and which unexecuted instructions could no
execution have reached — the dead bytecode.  Keeping the two populations apart
is the point of the report, so they are listed in separate sections rather than
merged into one list of instruction ranges.
"""

from menai.bytecode.menai_bytecode import CodeObject, Opcode
from menai.menai_value import MenaiString
from menai_render.menai_render_colour import grey, yellow
from menai_trace.menai_trace_coverage import FunctionCoverage, InstructionCoverage, function_key
from menai_trace.menai_trace_render import format_index_ranges
from menai_trace.menai_trace_source_coverage import (
    STATUS_FULL,
    STATUS_GAP,
    STATUS_REACHABLE_COMPLETE,
    CumulativeSourceCoverage,
    FunctionCoverageOutcome,
    SourceFileCoverage,
)

_SEPARATOR_WIDTH = 70
_COUNT_COL = 10
_FUNCTION_COL = 40
_STATUS_COL = 18
_LABEL_COL = 34

_STATUS_LABELS = {
    STATUS_FULL: "full",
    STATUS_REACHABLE_COMPLETE: "reachable-complete",
    STATUS_GAP: "gap",
}


def render_source_coverage(result: SourceFileCoverage, color: bool = False) -> list[str]:
    """
    Render the coverage of one source file's functions.

    The function table is ordered by coverage, least covered first, using
    reachable coverage as the ranking so that a function held back only by dead
    bytecode does not outrank one with a genuine gap.

    Args:
        result: The source file coverage to render.
        color:  Whether to emit ANSI colour codes.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    functions = result.function_count()
    full = len(result.fully_covered())
    reachable_complete = len(result.reachable_complete())
    gaps = len(result.gaps())

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow(f"FUNCTION COVERAGE \u2014 {result.source_file}", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Functions:':<{_LABEL_COL}} {functions:>12,}")
    lines.append(f"{'Fully exercised:':<{_LABEL_COL}} {full:>12,}")
    lines.append(f"{'Complete but for dead bytecode:':<{_LABEL_COL}} {reachable_complete:>12,}")
    lines.append(f"{'With reachable gaps:':<{_LABEL_COL}} {gaps:>12,}")
    lines.append("")
    lines.append(f"{'Instructions:':<{_LABEL_COL}} {result.total_instructions():>12,}")
    lines.append(
        f"{'Executed:':<{_LABEL_COL}} {result.covered_instructions():>12,} "
        f"{_percent(result.covered_instructions(), result.total_instructions()):>9}"
    )
    lines.append(
        f"{'Reachable:':<{_LABEL_COL}} {result.reachable_instruction_count():>12,} "
        f"{_percent(result.covered_reachable_instructions(), result.reachable_instruction_count()):>9}"
    )
    lines.append(f"{'Unreachable:':<{_LABEL_COL}} {result.unreachable_instruction_count():>12,}")
    lines.append("")

    if functions == 0:
        lines.append("  No code was compiled from this source file.")
        return lines

    lines.append(
        f"{'Executed':>{_COUNT_COL}} {'Total':>{_COUNT_COL}} {'Coverage':>10} "
        f"{'Calls':>{_COUNT_COL}}  {'Status':<{_STATUS_COL}}  {'Function':<{_FUNCTION_COL}}"
    )
    lines.append(
        f"{'-' * _COUNT_COL} {'-' * _COUNT_COL} {'-' * 10} {'-' * _COUNT_COL}  "
        f"{'-' * _STATUS_COL}  {'-' * _FUNCTION_COL}"
    )

    for outcome in result.ordered():
        lines.append(_render_row(outcome))

    lines.append("")
    lines.extend(_render_missed(result, color))
    lines.append("")
    lines.extend(_render_unreachable(result, color))
    return lines


def render_cumulative_source_coverage(
    result: CumulativeSourceCoverage,
    color: bool = False,
    top_n: int | None = 20,
) -> list[str]:
    """
    Render source file coverage merged across a set of test suites.

    The per-file reports name every gap; this report exists so that a run over a
    directory answers "where is the worst of it" without reading every block.
    The module table is ordered by coverage, least covered first, and the least
    covered functions are listed below it.

    Args:
        result: The merged coverage to render.
        color:  Whether to emit ANSI colour codes.
        top_n:  When given, only the first N gap functions are listed.  When
                None, every gap function is listed.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    functions = result.function_count()

    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(yellow("CUMULATIVE FUNCTION COVERAGE", color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))
    lines.append(f"{'Source files:':<{_LABEL_COL}} {result.report_count():>12,}")
    lines.append(f"{'Functions:':<{_LABEL_COL}} {functions:>12,}")
    lines.append(f"{'Fully exercised:':<{_LABEL_COL}} {result.fully_covered_count():>12,}")
    lines.append(
        f"{'Complete but for dead bytecode:':<{_LABEL_COL}} "
        f"{result.reachable_complete_count():>12,}"
    )
    lines.append(f"{'With reachable gaps:':<{_LABEL_COL}} {result.gap_count():>12,}")
    lines.append("")
    lines.append(f"{'Instructions:':<{_LABEL_COL}} {result.total_instructions():>12,}")
    lines.append(
        f"{'Executed:':<{_LABEL_COL}} {result.covered_instructions():>12,} "
        f"{_percent(result.covered_instructions(), result.total_instructions()):>9}"
    )
    lines.append(
        f"{'Reachable:':<{_LABEL_COL}} {result.reachable_instruction_count():>12,} "
        f"{_percent(result.covered_reachable_instructions(), result.reachable_instruction_count()):>9}"
    )
    lines.append(f"{'Unreachable:':<{_LABEL_COL}} {result.unreachable_instruction_count():>12,}")
    lines.append("")

    lines.append(
        f"{'Module':<24}{'Functions':>10}{'Full':>7}{'Gaps':>7}{'Coverage':>11}"
    )
    lines.append(f"{'-' * 24}{'-' * 10}{'-' * 7}{'-' * 7}{'-' * 11}")
    for report in result.ordered_reports():
        lines.append(
            f"{_module_name(report.source_file):<24}"
            f"{report.function_count():>10,}"
            f"{len(report.fully_covered()):>7,}"
            f"{len(report.gaps()):>7,}"
            f"{report.coverage_percent():>10.2f}%"
        )

    gaps = result.ordered_gaps()
    if not gaps:
        return lines

    shown = gaps if top_n is None else gaps[:top_n]
    lines.append("")
    heading = "Functions with reachable gaps"
    if len(shown) < len(gaps):
        heading += f" (worst {len(shown)} of {len(gaps)})"

    lines.append(yellow(heading, color))
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    for outcome in shown:
        module = _module_name(outcome.function.code.source_file)
        missed = outcome.missed_instructions()
        lines.append(
            f"  {module:<16} {_label(outcome.function):<30} "
            f"{_describe(outcome.function.code, missed)}"
        )

    return lines


def _module_name(source_file: str) -> str:
    """Return a source file name without the Menai module extension."""
    return source_file.removesuffix(".menai")


def _render_row(outcome: FunctionCoverageOutcome) -> str:
    """Render one function's row of the coverage table."""
    function = outcome.function
    return (
        f"{function.covered_instructions():>{_COUNT_COL},} "
        f"{function.total_instructions():>{_COUNT_COL},} "
        f"{function.coverage_percent():>9.2f}% "
        f"{function.calls:>{_COUNT_COL},}  "
        f"{_STATUS_LABELS[outcome.status()]:<{_STATUS_COL}}  "
        f"{_label(function):<{_FUNCTION_COL}}"
    )


def _render_missed(result: SourceFileCoverage, color: bool) -> list[str]:
    """Render the reachable instructions that never executed."""
    lines = [yellow("Reachable instructions not executed", color)]
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    any_missed = False
    for outcome in result.ordered():
        missed = outcome.missed_instructions()
        if not missed:
            continue

        any_missed = True
        lines.append(f"  {_label(outcome.function):<{_LABEL_COL + 8}} {_describe(outcome.function.code, missed)}")

    if not any_missed:
        lines.append("  None.")

    return lines


def _render_unreachable(result: SourceFileCoverage, color: bool) -> list[str]:
    """Render the unexecuted instructions that no path from entry reaches."""
    lines = [yellow("Dead bytecode (unreachable from entry)", color)]
    lines.append(grey("\u2500" * _SEPARATOR_WIDTH, color))

    any_unreachable = False
    for outcome in result.ordered():
        unreachable = outcome.unreachable_instructions()
        if not unreachable:
            continue

        any_unreachable = True
        lines.append(f"  {_label(outcome.function):<{_LABEL_COL + 8}} {_describe(outcome.function.code, unreachable)}")

    if not any_unreachable:
        lines.append("  None.")

    return lines


def _label(function: FunctionCoverage) -> str:
    """
    Return the display label for a function in this file.

    The file name is in the report heading, so the label carries only the
    function's short name and its line.  The short name drops the import index
    the compiler prefixes to closure names, which differs between programs.
    """
    name = function_key(function.code).name
    if function.code.source_line > 0:
        return f"{name} [line {function.code.source_line}]"

    return name


def _describe(code: CodeObject, instructions: tuple[InstructionCoverage, ...]) -> str:
    """
    Describe a set of unexecuted instructions compactly.

    A single instruction is named by its index and opcode, because an opcode is
    what distinguishes a source `error` from a `RETURN` or `MOVE` that no path
    reaches.  A `RAISE_ERROR` also shows its message, which identifies the
    compiler's synthetic match fallthrough.  A larger set is shown as index
    ranges, the individual instructions being too numerous to name.
    """
    if len(instructions) == 1:
        instruction = instructions[0]
        text = f"{instruction.index} {Opcode(instruction.instruction.opcode).name}"
        message = _raise_message(code, instruction)
        if message is not None:
            text += f" {message!r}"

        return text

    return format_index_ranges([instruction.index for instruction in instructions])


def _raise_message(code: CodeObject, instruction: InstructionCoverage) -> str | None:
    """
    Return the message a `RAISE_ERROR` instruction carries, if it is a string literal.

    The message is a constant only when the instruction's tag marks its first
    source position as a constant-pool index; a register-held message has no
    static value and yields None.
    """
    if Opcode(instruction.instruction.opcode) != Opcode.RAISE_ERROR:
        return None

    if not instruction.instruction.tag & 1:
        return None

    index = instruction.instruction.src0
    if index >= len(code.constants):
        return None

    constant = code.constants[index]
    if not isinstance(constant, MenaiString):
        return None

    return constant.value


def _percent(part: int, total: int) -> str:
    """Return part over total as a formatted percentage, or a dash when total is zero."""
    if total == 0:
        return "-"

    return f"{part / total * 100.0:.2f}%"
