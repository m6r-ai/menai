"""
Menai parenthesis diagnostics.

Menai source is an S-expression tree, so an unbalanced parenthesis is the most
common syntax error.  This module locates one from the token stream, and is used
by the AST builder to place its compile-time error on the line the reader has to
change rather than the line where the offending form opened.

The starting observation is that an opening parenthesis is never the mistake.
Menai is written by adding forms, so a ``(`` that is present is intended; what
goes wrong is a ``)`` that is missing or one that is present and should not be.
That asymmetry is what makes the offending line derivable rather than guessed:

- The intended depth profile is known up to the point of the error, because
  every line's *starting* depth is correct.  A line whose ending depth fails to
  return to its starting depth is a line where a ``)`` is missing.
- A line whose ending depth falls *below* its starting depth has closed
  something that was not open, so a ``)`` on that line is extra.

Balance alone is not enough to be useful: a file can have equal numbers of
opens and closes and still be malformed, and a genuinely unbalanced file leaves
the parser unable to say which of the many closes is the wrong one.  The
diagnosis therefore also reports the insertion point for missing closes (the
position the ``)`` should occupy) and, for extra closes, the first ``)`` that
cannot be matched together with any earlier close that completed a form which
cannot be complete.
"""

from dataclasses import dataclass

from menai.ast.menai_token import MenaiToken, MenaiTokenType


# Forms whose body is required, with the number of elements they must have
# before a close can be legitimate.  A close that completes one of these forms
# with fewer elements has closed it before its body, which is the signature of a
# stray ')' that ended the form early.
_MINIMUM_ELEMENTS = {
    'let': 3,
    'let*': 3,
    'letrec': 3,
    'lambda': 3,
    'if': 4,
}


# Form heads that are reserved: they are syntax, not names, so a form headed by
# one can never be a binding.  A bindings list that absorbs one has absorbed its
# own body, which means it is missing its close paren.
_RESERVED_FORMS = frozenset({'export', 'import', 'struct', '::', 'quote'})


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

    ``insertion_line`` and ``insertion_column`` are where the missing closes
    belong, and are set only when closes are missing.  ``extra_line`` and
    ``extra_column`` are the first ``)`` that cannot be matched, and are set only
    when closes are extra.  ``suspected_*`` name an earlier ``)`` that closed a
    form before its body, which is the likely cause of an extra close.
    """

    insertion_line: int | None
    insertion_column: int | None
    binding_list_line: int | None
    binding_list_column: int | None
    extra_line: int | None
    extra_column: int | None
    lines: list[LineDepth]
    suspected_line: int | None
    suspected_column: int | None
    suspected_form: str | None


@dataclass
class _OpenForm:
    """An open parenthesis being tracked while scanning."""

    line: int
    column: int
    form_type: str
    depth_when_opened: int
    is_bindings_list: bool = False
    element_count: int = 0
    last_element_line: int = 0
    last_element_column: int = 0


def _form_type_at(tokens: list[MenaiToken], index: int) -> str:
    """
    Name the form opened by the parenthesis at *index*.

    The name is the symbol that follows the parenthesis, or ``'('`` for a
    parenthesised group with no head symbol.
    """
    if index + 1 < len(tokens) and tokens[index + 1].type == MenaiTokenType.SYMBOL:
        return str(tokens[index + 1].value)

    return '('


def diagnose_parens(tokens: list[MenaiToken], source: str) -> ParenDiagnosis:
    """
    Analyse parenthesis balance over a token stream.

    Args:
        tokens: The token stream, with comments already discarded.
        source: The original source text, used for the per-line depth table.

    Returns:
        A ParenDiagnosis describing the balance, the position of any missing or
        extra parenthesis, and the unclosed forms.
    """
    source_lines = source.split('\n')
    line_count = max(len(source_lines), 1)

    start_depths: list[int | None] = [None] * line_count
    end_depths: list[int | None] = [None] * line_count

    open_forms: list[_OpenForm] = []
    depth = 0
    last_token: MenaiToken | None = None
    previous_end_line = 0
    previous_end_column = 0
    body_after_line: int | None = None
    body_after_column: int | None = None

    extra_line: int | None = None
    extra_column: int | None = None
    suspected_line: int | None = None
    suspected_column: int | None = None
    suspected_form: str | None = None

    for index, token in enumerate(tokens):
        line_index = token.line - 1
        if line_index < 0 or line_index >= line_count:
            continue

        if start_depths[line_index] is None:
            start_depths[line_index] = depth

        last_token = token

        # Every open form contains this token, so its end is the end of the last
        # element of each of them.  That is where a missing ')' for that form
        # belongs.
        for form in open_forms:
            form.last_element_line = token.line
            form.last_element_column = token.column + token.length

        if token.type == MenaiTokenType.LPAREN:
            is_bindings_list = False
            if open_forms:
                parent = open_forms[-1]
                parent.element_count += 1

                # A binding form's bindings list is its second element (after the
                # keyword).  Its head is another '(', so it is identified by
                # position rather than by the symbol after the paren.
                is_bindings_list = (
                    parent.form_type in ('let', 'let*', 'letrec')
                    and parent.element_count == 2
                )

                # A reserved form can never be a binding, so if a bindings list
                # absorbs one, that form is the body and the bindings list is
                # missing its close immediately before it.  The close belongs at
                # the end of the token preceding this form.
                if (
                    parent.is_bindings_list
                    and _form_type_at(tokens, index) in _RESERVED_FORMS
                    and body_after_line is None
                ):
                    body_after_line = previous_end_line
                    body_after_column = previous_end_column

            form = _OpenForm(
                line=token.line,
                column=token.column,
                form_type=_form_type_at(tokens, index),
                depth_when_opened=depth,
                is_bindings_list=is_bindings_list,
            )
            open_forms.append(form)
            depth += 1

        elif token.type == MenaiTokenType.RPAREN:
            depth -= 1

            if open_forms:
                closed = open_forms.pop()
                minimum = _MINIMUM_ELEMENTS.get(closed.form_type)
                if (
                    minimum is not None
                    and closed.element_count < minimum
                    and extra_line is None
                    and suspected_line is None
                ):
                    suspected_line = token.line
                    suspected_column = token.column
                    suspected_form = closed.form_type

            elif extra_line is None:
                extra_line = token.line
                extra_column = token.column

        else:
            if open_forms:
                open_forms[-1].element_count += 1

        end_depths[line_index] = depth
        previous_end_line = token.line
        previous_end_column = token.column + token.length

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
        binding_list_line=body_after_line,
        binding_list_column=body_after_column,
        extra_line=extra_line,
        extra_column=extra_column,
        lines=lines,
        suspected_line=suspected_line,
        suspected_column=suspected_column,
        suspected_form=suspected_form,
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
