"""
Menai parenthesis diagnostics.

Menai source is an S-expression tree, so an unbalanced parenthesis is the most
common syntax error.  This module measures the balance of a token stream, and is
used by the AST builder to place its compile-time error on the line the reader
has to change rather than the line where the offending form opened.

The starting observation is that an opening parenthesis is never the mistake.
Menai is written by adding forms, so a `(` that is present is intended; what
goes wrong is a `)` that is missing or one that is present and should not be.
That asymmetry is what makes the offending line derivable rather than guessed:

- The intended depth profile is known up to the point of the error, because
  every line's *starting* depth is correct.  A line whose ending depth fails to
  return to its starting depth is a line where a `)` is missing.
- A line whose ending depth falls *below* its starting depth has closed
  something that was not open, so a `)` on that line is extra.

What this module does NOT do is decide which form is at fault.  The parser knows
that from its own frames: it records a form as closing short when it pops a
frame with fewer elements than the form's arity requires.  Inferring the same
thing from token balance was a second, independent model of the nesting, and the
two could disagree.  This module therefore reports only what balance alone can
establish: where a `)` is missing, where one is unmatched, and the per-line depth
profile used to display both.
"""

from dataclasses import dataclass

from menai.ast.menai_token import MenaiToken, MenaiTokenType


@dataclass(frozen=True)
class LineDepth:
    """Parenthesis depth at the start and end of one source line."""

    line: int
    start_depth: int
    end_depth: int
    content: str


@dataclass(frozen=True)
class ParenDiagnosis:
    """
    The result of analysing parenthesis balance over a token stream.

    `insertion_line` and `insertion_column` are where the missing closes
    belong, and are set only when closes are missing.  `extra_line` and
    `extra_column` are the first `)` that cannot be matched, and are set only
    when closes are extra.
    """

    insertion_line: int | None
    insertion_column: int | None
    extra_line: int | None
    extra_column: int | None
    lines: list[LineDepth]


def diagnose_parens(tokens: list[MenaiToken], source: str) -> ParenDiagnosis:
    """
    Measure parenthesis balance over a token stream.

    This reports only what balance alone can establish: where a `)` is
    unmatched, where a missing `)` belongs, and the per-line depth profile.
    It does not decide which form is at fault — the parser knows that from its
    own frames.

    Args:
        tokens: The token stream, with comments already discarded.
        source: The original source text, used for the per-line depth table.

    Returns:
        A ParenDiagnosis describing the balance and the position of any missing
        or extra parenthesis.
    """
    source_lines = source.split('\n')
    line_count = max(len(source_lines), 1)

    start_depths: list[int | None] = [None] * line_count
    end_depths: list[int | None] = [None] * line_count

    depth = 0
    last_token: MenaiToken | None = None

    extra_line: int | None = None
    extra_column: int | None = None

    for token in tokens:
        line_index = token.line - 1
        if line_index < 0 or line_index >= line_count:
            continue

        if start_depths[line_index] is None:
            start_depths[line_index] = depth

        last_token = token

        if token.type == MenaiTokenType.LPAREN:
            depth += 1

        elif token.type == MenaiTokenType.RPAREN:
            depth -= 1

            if depth < 0 and extra_line is None:
                extra_line = token.line
                extra_column = token.column

        end_depths[line_index] = depth

    lines = _build_line_depths(source_lines, start_depths, end_depths)

    insertion_line: int | None = None
    insertion_column: int | None = None
    if depth > 0:
        if last_token is not None:
            insertion_line = last_token.line
            insertion_column = last_token.column + last_token.length

    return ParenDiagnosis(
        insertion_line=insertion_line,
        insertion_column=insertion_column,
        extra_line=extra_line,
        extra_column=extra_column,
        lines=lines,
    )


def _build_line_depths(
    source_lines: list[str],
    start_depths: list[int | None],
    end_depths: list[int | None],
) -> list[LineDepth]:
    """
    Fill in depth for lines that carry no tokens.

    A line with no tokens inherits the depth of the preceding line, so the table
    reads as a continuous profile rather than a column of gaps.
    """
    lines: list[LineDepth] = []
    carried = 0

    for index, content in enumerate(source_lines):
        start = start_depths[index]
        end = end_depths[index]

        if start is None and end is None:
            start = carried
            end = carried

        else:
            start = carried if start is None else start
            end = start if end is None else end

        carried = end
        lines.append(LineDepth(
            line=index + 1,
            start_depth=start,
            end_depth=end,
            content=content,
        ))

    return lines


def format_depth_table(
    diagnosis: ParenDiagnosis,
    first_line: int,
    last_line: int,
) -> str:
    """
    Render the per-line depth table for a range of lines.

    The table is the most direct answer to "which line has too many or too few
    closing parentheses": each row shows the depth entering the line and the
    depth leaving it, so a row that does not return to its starting depth is a
    row with a missing close, and a row that drops below its starting depth is a
    row with an extra close.

    Args:
        diagnosis: The diagnosis to render.
        first_line: First line to include (1-indexed, inclusive).
        last_line: Last line to include (1-indexed, inclusive).

    Returns:
        The formatted table, one row per line.
    """
    if not diagnosis.lines:
        return ""

    first = max(1, first_line)
    last = min(len(diagnosis.lines), last_line)

    width = len(str(last))
    rows = [f"  {'line':>{width}} | {'depth':^11} | code"]

    for entry in diagnosis.lines[first - 1:last]:
        depth = f"{entry.start_depth}->{entry.end_depth}"
        rows.append(f"  {entry.line:>{width}} | {depth:^11} | {entry.content}")

    return "\n".join(rows)
