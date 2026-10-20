"""AST builder for Menai expressions with detailed error messages."""

from typing import cast
from dataclasses import dataclass
from menai.menai_error import MenaiASTBuildError
from menai.ast.menai_token import MenaiToken, MenaiTokenType
from menai.ast.menai_paren_diagnostic import (
    ParenDiagnosis, diagnose_parens, format_depth_table,
)
from menai.ast.menai_ast import (
    MenaiASTNode, MenaiASTInteger, MenaiASTFloat, MenaiASTComplex, MenaiASTString,
    MenaiASTBoolean, MenaiASTNone, MenaiASTSymbol, MenaiASTList
)


# Each special form's elements, by role, in order.  The head symbol is not
# listed, so the number of roles is the form's arity and the table drives both
# the parse and the arity check.
#
# A role says how the element is parsed:
#
#   'expr'      an expression, parsed by _parse_expression
#   'data'      one data element: an atom or a data list, parsed by
#               _parse_data_element
#   'data-list' a list whose contents are data, parsed by _parse_data_list
#   'symbol'    a bare symbol
#   'string'    a string literal
#
# A form's arity is a property of its shape, so it is checked here rather than
# in the semantic analyzer: the analyzer never runs when the parse fails, and a
# wrong element count is a common cause of a parse that otherwise succeeds.
#
# The names are reserved (see RESERVED_NAMES in the semantic analyzer), so a
# form headed by one is always that form and never a call to a user binding.
# 'match' is not here: its clauses are variadic and each clause is
# (pattern result), where the pattern is data and the result an expression, so
# it has its own path.
_SPECIAL_FORM_ELEMENTS = {
    'if': ('expr', 'expr', 'expr'),
    'lambda': ('data-list', 'expr'),
    'quote': ('data',),
    '::': ('symbol', 'symbol'),
    'import': ('string',),
    'apply': ('expr', 'expr'),
    'struct': ('data-list',),
    'enum': ('data-list',),
}


# Forms whose elements are variadic in shape, so they cannot be described by a
# fixed role list, but which still cannot be complete with fewer elements than
# this (head included).  A 'let' needs a head, a bindings list and a body; a
# 'match' a head, the value and at least one clause.
_VARIADIC_FORM_MINIMUM = {
    'let': 3,
    'match': 3,
}


# Form heads that are reserved: they are syntax, not names, so a form headed by
# one can never be a binding.  A bindings list that absorbs one has absorbed its
# own body, which means it is missing its close paren.
_RESERVED_FORMS = frozenset({'export', 'import', 'struct', '::', 'quote'})


# The shape of each special form, named by its head symbol.  Used in an arity
# error to show what the form takes, in place of a worked example.
_SPECIAL_FORM_SHAPE = {
    'if': "(if condition then else)",
    'lambda': "(lambda (params...) body)",
    'quote': "(quote expression)",
    'match': "(match value (pattern result) ...)",
    '::': "(:: namespace member)",
    'import': '(import "module-name")',
    'apply': "(apply function arguments)",
    'struct': "(struct (field ...))",
    'enum': "(enum (variant ...))",
}


def _is_quoted_symbol(node: MenaiASTNode) -> bool:
    """
    Report whether *node* is a quoted symbol, written 'name.

    The reader produces a quoted symbol as the two-element form (quote name).
    The check is purely structural and does not verify that the name is bound.
    """
    if not isinstance(node, MenaiASTList) or len(node.elements) != 2:
        return False

    head, name_node = node.elements
    return (isinstance(head, MenaiASTSymbol) and head.name == 'quote'
            and isinstance(name_node, MenaiASTSymbol))


@dataclass(slots=True)
class ParenStackFrame:
    """
    An open parenthesis being tracked while parsing.

    `form_type` is the form's head symbol ('let', 'if', ...), or '(' for a
    parenthesised group with no head.  A bindings list has no head of its own,
    so it is named by `is_bindings_list` and `enclosing_form` instead: it is
    the binding form whose bindings it holds.

    `element_count` is how many complete elements the form has parsed so far.
    It is what lets an error say how far the form got before the input ran out.

    `required_elements` is how many elements the form needs before it can be
    complete, head included, or None when the form has no fixed arity.  When the
    form is closed with fewer than that, the close is premature: it ended the
    form before its body, which is what leaves the forms that follow reading as
    further elements of this one.  `closed_short_line` and `closed_short_column`
    record where that ')' was, so an error can point at the line to change.
    """

    line: int
    column: int
    related_symbol: str | None = None  # For bindings: the variable name
    form_type: str = '('
    element_count: int = 0
    is_bindings_list: bool = False
    enclosing_form: str | None = None
    required_elements: int | None = None
    closed_short_line: int | None = None
    closed_short_column: int | None = None


class MenaiASTBuilder:
    """Parses tokens into an Abstract Syntax Tree using pure list representation with detailed error messages."""

    def __init__(self) -> None:
        """
        Initialize parser with tokens and original expression.

        Args:
            tokens: List of tokens to parse
            expression: Original expression string for error context
        """
        self.tokens: list[MenaiToken] | None = None
        self.pos = 0
        self.current_token: MenaiToken | None = None
        self.expression = ""
        self.source_file = ""

        # Paren stack for tracking unclosed expressions
        self.paren_stack: list[ParenStackFrame] = []

        # Track where the most recently closed frame was closed.  Used to
        # report the position of the previous binding when a bindings list is
        # missing its close paren.
        self.last_close_line: int = 1
        self.last_close_column: int = 1

        # Track the line/column of the last token we consumed
        self.last_token_end_line: int = 1
        self.last_token_end_column: int = 1

        # The first ')' that closed a form before its body, as
        # (line, column, form_type, open_line, open_column).  This is the
        # mistake a structural error downstream is usually the symptom of, so
        # the error paths report it in preference to the symptom.  None when no
        # form has closed short.
        self.early_close: tuple[int, int, str, int, int] | None = None

        # Where a bindings list should have closed, as (line, column), when it
        # absorbed its own body.  A reserved form can never be a binding, so one
        # appearing where a binding is expected means the bindings list is
        # missing its close, and the close belongs after the previous binding.
        self.bindings_close_after: tuple[int | None, int | None] | None = None

    def build(self, tokens: list[MenaiToken], expression: str = "", source_file: str = "") -> MenaiASTNode:
        """
        Parse tokens into AST with detailed error reporting.

        Args:
            tokens: List of tokens to parse
            expression: Original expression string for error context
            source_file: Source file name for tracking origin of AST nodes

        Returns:
            Parsed expression

        Raises:
            MenaiASTBuildError: If parsing fails with detailed context
        """
        self.tokens = tokens
        self.pos = 0
        self.current_token = self.tokens[0] if self.tokens else None
        self.expression = expression
        self.source_file = source_file

        # A previous build() that raised leaves its unclosed frames behind.
        # Clearing the stack here keeps a failed parse from inflating the depth
        # reported for the next one.
        self.paren_stack.clear()

        # Reset inter-call state so stale positions from a previous build() never
        # leak into error messages for this expression.
        self.last_close_line = 1
        self.last_close_column = 1
        self.early_close = None
        self.bindings_close_after = None

        if self.current_token is None:
            raise MenaiASTBuildError(
                message="Empty expression",
                expected="Valid Menai expression",
                example="(+ 1 2) or 42 or \"hello\"",
                suggestion="Provide a complete expression to evaluate",
                context="Expression cannot be empty or contain only whitespace",
                source_file=self.source_file,
            )

        expr = self._parse_expression()

        if self.current_token is not None:
            current_value = self.current_token.value if self.current_token else "EOF"
            current_line = self.current_token.line if self.current_token else 1
            current_col = self.current_token.column if self.current_token else 1

            diagnosis = self._diagnose_parens()

            if diagnosis.extra_line is not None and diagnosis.extra_column is not None:
                raise self._create_extra_close_error(
                    diagnosis, current_value, current_line, current_col
                )

            if self.early_close is not None:
                raise self._create_form_closed_early_error(
                    current_value, current_line, current_col
                )

            raise MenaiASTBuildError(
                message="Unexpected token after complete expression",
                line=current_line,
                column=current_col,
                received=f"Found: {current_value}",
                expected="End of expression",
                suggestion="Remove extra tokens or combine into single expression",
                context="Each evaluation can only handle one complete expression",
                source=self.expression,
                source_file=self.source_file,
            )

        return expr

    def _parse_expression(self) -> MenaiASTNode:
        """Parse a single expression with detailed error reporting."""
        token = self.current_token
        assert token is not None, "Should not be called at EOF"

        self._count_element()

        # Our if table is quicker than using a jump table.  As we've got to do this sequentially we
        # try to order the checks by expected frequency.
        if token.type == MenaiTokenType.LPAREN:
            return self._parse_list()

        if token.type == MenaiTokenType.SYMBOL:
            self._advance()
            return MenaiASTSymbol(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.NONE:
            self._advance()
            return MenaiASTNone(line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.BOOLEAN:
            self._advance()
            return MenaiASTBoolean(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.INTEGER:
            self._advance()
            return MenaiASTInteger(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.STRING:
            self._advance()
            return MenaiASTString(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.FLOAT:
            self._advance()
            return MenaiASTFloat(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.COMPLEX:
            self._advance()
            return MenaiASTComplex(token.value, line=token.line, column=token.column, source_file=self.source_file)

        if token.type == MenaiTokenType.QUOTE:
            return self._parse_quoted_expression()

        # Enhanced error for unexpected tokens
        assert token.type == MenaiTokenType.RPAREN, f"Unexpected token type ({token.type}) encountered"
        token_value = token.value
        token_type_name = token.type.name

        raise MenaiASTBuildError(
            message=f"Unexpected token: {token_value}",
            line=token.line,
            column=token.column,
            received=f"Token: {token_value} (type: {token_type_name})",
            expected="Number, string, boolean, symbol, '(', or '",
            example="Valid starts: 42, \"hello\", #t, symbol, (, '",
            suggestion="List expressions must start with '(' and quoted expressions with '",
            context=f"Token '{token_value}' cannot start an expression",
            source=self.expression,
            source_file=self.source_file,
        )

    def _count_element(self) -> None:
        """
        Count one element against the form currently being parsed.

        Every element of every form is counted here or at the few parse paths
        that do not go through _parse_expression (a bindings list and its
        bindings).  The count is what an unterminated-form error reports as how
        far the form got before the input ran out.
        """
        if self.paren_stack:
            self.paren_stack[-1].element_count += 1

    def _form_head_at(self, index: int) -> str:
        """
        Name the form whose opening parenthesis is at *index*.

        The name is the symbol that follows the parenthesis, or an empty string
        when the form has no head symbol.

        Args:
            index: Index of the form's opening parenthesis in the token stream.

        Returns:
            The head symbol, or an empty string for a group with no head.
        """
        if self.tokens is None or index + 1 >= len(self.tokens):
            return ""

        head = self.tokens[index + 1]
        return str(head.value) if head.type == MenaiTokenType.SYMBOL else ""

    def _push_paren_frame(
        self,
        line: int,
        column: int,
        form_type: str = '(',
        is_bindings_list: bool = False,
        enclosing_form: str | None = None,
        required_elements: int | None = None,
    ) -> ParenStackFrame:
        """
        Push a new opening paren onto the tracking stack.

        Args:
            line: Line number (1-indexed)
            column: Column number (1-indexed)
            form_type: The form's head symbol, or '(' for a group with no head
            is_bindings_list: True when this is a binding form's bindings list
            enclosing_form: For a bindings list, the binding form that holds it
            required_elements: The form's arity, head included, or None when it
                has no fixed arity

        Returns:
            The created frame (so caller can update it)
        """
        frame = ParenStackFrame(
            line=line,
            column=column,
            form_type=form_type,
            is_bindings_list=is_bindings_list,
            enclosing_form=enclosing_form,
            required_elements=required_elements,
        )

        self.paren_stack.append(frame)
        return frame

    def _pop_paren_frame(self) -> None:
        """Pop an opening paren from the stack when it's successfully closed."""
        assert self.paren_stack, "Paren stack underflow - trying to pop from empty stack"

        frame = self.paren_stack.pop()

        # current_token is the ')' that just closed this frame — record its position
        if self.current_token is not None:
            self.last_close_line = self.current_token.line
            self.last_close_column = self.current_token.column

        else:
            self.last_close_line = self.last_token_end_line
            self.last_close_column = self.last_token_end_column

        # A form closed with fewer elements than it needs ended before its body.
        # That is the mistake, and the ')' that did it is the line to change.
        # Record the first such close, which the error paths report in
        # preference to whatever structural symptom it produced downstream.
        if (
            frame.required_elements is not None
            and frame.element_count < frame.required_elements
            and self.early_close is None
        ):
            self.early_close = (
                self.last_close_line,
                self.last_close_column,
                frame.form_type,
                frame.line,
                frame.column,
            )

    def _diagnose_parens(self) -> ParenDiagnosis:
        """
        Measure parenthesis balance over the whole token stream.

        This answers only what balance can: where a ')' is unmatched, where a
        missing ')' belongs, and the per-line depth profile.  Which form is at
        fault is the parser's own business, recorded on its frames.
        """
        tokens = cast(list[MenaiToken], self.tokens) if self.tokens is not None else []
        return diagnose_parens(tokens, self.expression)

    def _missing_close_report(
        self, fallback_line: int, fallback_column: int
    ) -> tuple[int, int, str, str, str]:
        """
        Locate the closes missing at end of input, and describe them.

        Every end-of-input error needs the same three things: where to point,
        a sentence saying which ')' goes where, and a one-line statement of what
        is expected.  They are computed together because they must agree — a
        message that names one position while the suggestion names another sends
        the reader to the wrong line.

        The closes do not necessarily all belong in one place.  When a bindings
        list absorbed its own body, one ')' closes that list immediately after
        its last binding, and the rest close the forms enclosing it at the end
        of the input.  Reporting the whole run at the first position would tell
        the reader to put the body's close before the body.

        Args:
            fallback_line: Line to point at when there is no insertion point.
            fallback_column: Column to point at in the same case.

        Returns:
            A tuple of (line, column, where, expected, table_block), where
            `where` is the suggestion sentence, `expected` the one-line
            statement of what is missing, and `table_block` the depth profile.
        """
        depth = len(self.paren_stack)
        closing_parens = ")" * depth

        diagnosis = self._diagnose_parens()
        end_line = diagnosis.insertion_line
        end_column = diagnosis.insertion_column

        if end_line is not None and end_column is not None:
            end_position = f"at line {end_line}, column {end_column}"

        else:
            end_position = "at the end of the input"

        if (
            self.bindings_close_after is not None
            and self.bindings_close_after[0] is not None
            and self.bindings_close_after[1] is not None
            and depth > 1
        ):
            bindings_line = self.bindings_close_after[0]
            bindings_column = self.bindings_close_after[1]
            error_line = bindings_line
            error_column = bindings_column

            outer_count = depth - 1
            outer_parens = ")" * outer_count
            outer_word = "parenthesis" if outer_count == 1 else "parentheses"
            outer_forms = "form" if outer_count == 1 else "forms"

            where = (
                f"Insert ')' at line {bindings_line}, column {bindings_column}, "
                f"immediately after the last binding, to close the bindings list. "
                f"Insert '{outer_parens}' ({outer_count} closing {outer_word}) "
                f"{end_position}, after the last form, to close the "
                f"{outer_count} {outer_forms} enclosing it."
            )
            expected = (
                f"')' after the last binding to close the bindings list, then "
                f"'{outer_parens}' {end_position} to close the enclosing forms"
            )

        elif (
            self.bindings_close_after is not None
            and self.bindings_close_after[0] is not None
            and self.bindings_close_after[1] is not None
        ):
            # The bindings list is the only form left open, so its close is the
            # whole of what is missing.  There is no second insertion point.
            bindings_line = self.bindings_close_after[0]
            bindings_column = self.bindings_close_after[1]
            error_line = bindings_line
            error_column = bindings_column

            where = (
                f"Insert ')' at line {bindings_line}, column {bindings_column}, "
                f"immediately after the last binding, to close the bindings list."
            )
            expected = "')' after the last binding to close the bindings list"

        else:
            if end_line is not None and end_column is not None:
                error_line = end_line
                error_column = end_column
                where = (
                    f"Insert {closing_parens} at line {error_line}, column {error_column} "
                    f"(immediately after the last token)."
                )

            else:
                error_line = fallback_line
                error_column = fallback_column
                where = f"Insert {closing_parens} to close the unclosed expressions."

            expected = f'Additional parentheses, "{closing_parens}", to close all expressions'

        table_block = self._depth_table_block(error_line)

        return error_line, error_column, where, expected, table_block

    def _open_scope_lines(self) -> list[str]:
        """
        Render the forms still open on the parser's stack, outermost first.

        This reads the parser's own frames rather than re-deriving the nesting
        from the token stream.  At the point an unterminated-form error is
        raised the parser has simply run out of tokens: it has not backtracked
        or resynchronised, so its stack is exactly as deep as the input was, and
        it knows each form's name because it recorded it when opening the form.

        Returns:
            One line per open form, or an empty list when nothing is open.
        """
        lines = []
        for index, frame in enumerate(self.paren_stack, 1):
            if frame.is_bindings_list:
                name = f"bindings list of '{frame.enclosing_form}'"

            elif frame.related_symbol is not None:
                # A binding frame knows the variable it binds, which names the
                # binding far better than its position does.
                name = f"binding '{frame.related_symbol}'"

            elif frame.form_type == '(':
                name = "parenthesised group"

            else:
                name = f"'{frame.form_type}'"

            count = frame.element_count
            elements = f"{count} element{'s' if count != 1 else ''}"
            lines.append(f"  {index}. {name} at line {frame.line}, column {frame.column} ({elements})")

        return lines

    def _depth_table_around(self, line: int, context: int = 3) -> str:
        """
        Render the per-line depth table around a line for inclusion in an error.

        Args:
            line: The line to centre the table on.
            context: Number of lines to show either side.

        Returns:
            The formatted table, or an empty string when there is no source.
        """
        if not self.expression:
            return ""

        diagnosis = self._diagnose_parens()
        return format_depth_table(diagnosis, line - context, line + context)

    def _depth_table_block(self, line: int | None) -> str:
        """
        Render the depth table around a line as a labelled block for an error.

        Args:
            line: The line to centre the table on, or None to omit the table.

        Returns:
            The labelled table block, or an empty string when there is no source.
        """
        if line is None:
            return ""

        depth_table = self._depth_table_around(line)
        if not depth_table:
            return ""

        return f"\n\nParenthesis depth by line (start->end):\n{depth_table}"

    def _is_binding_node(self, node: MenaiASTNode) -> bool:
        """
        Report whether *node* has the shape of a let binding.

        A binding is a two-element `(name value)` form: a symbol followed by a
        single expression.  This is the shape a bindings list keeps when a `)`
        closes it early and the remaining bindings are read as body expressions.

        Args:
            node: The parsed form to test.

        Returns:
            True when the form is a two-element symbol-headed list.
        """
        if not isinstance(node, MenaiASTList) or len(node.elements) != 2:
            return False

        return isinstance(node.elements[0], MenaiASTSymbol)

    def _create_enhanced_unterminated_error(self, start_line: int, start_col: int) -> MenaiASTBuildError:
        """
        Create an error for input that ran out with forms still open.

        The open forms are taken from the token stream rather than from the
        parser's in-progress stack, because the parser's stack diverges from the
        token stream once it starts recovering from the error.  The token stream
        gives the nesting the reader intended to write, outermost first, which
        names the form whose closing ')' is absent.

        Args:
            start_line: Line where the unterminated list started
            start_col: Column where the unterminated list started

        Returns:
            MenaiASTBuildError naming the open forms and the insertion point
        """
        depth = len(self.paren_stack)

        error_line, error_column, where, expected, table_block = self._missing_close_report(
            start_line, start_col
        )

        # A form that closed before its body is why the input ran out with a form
        # still open: the ')' that ended it early is the mistake, and the open
        # form above it is the symptom.  Naming the early close is what turns
        # "something is unclosed" into "this ')' ended this form too soon".
        early_close_block = ""
        if self.early_close is not None:
            early_line, early_column, early_form, early_open_line, early_open_column = self.early_close
            early_close_block = (
                f"\n\nThe '{early_form}' at line {early_open_line}, "
                f"column {early_open_column} was closed by the ')' at "
                f"line {early_line}, column {early_column} "
                f"before its body, which is what left the input unbalanced."
            )

        scope_lines = self._open_scope_lines()
        scope_block = "Forms still open (outermost first):\n" + "\n".join(scope_lines) if scope_lines else ""

        # Create the context message
        context_msg = (
            f"Reached end of input at depth {depth}.\n\n"
            f"{where}\n\n"
            f"{scope_block}"
            f"{early_close_block}"
            f"{table_block}"
        )

        # Determine singular vs plural
        paren_word = "parenthesis" if depth == 1 else "parentheses"

        return MenaiASTBuildError(
            message=f"Unterminated list - missing {depth} closing {paren_word}",
            line=error_line,
            column=error_column,
            expected=expected,
            suggestion=where,
            context=context_msg,
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_extra_close_error(
        self,
        diagnosis: ParenDiagnosis,
        current_value: object,
        current_line: int,
        current_col: int,
    ) -> MenaiASTBuildError:
        """
        Create an error for a ')' that cannot be matched to any '('.

        The location is the ')' the reader has to remove.  When an earlier ')'
        closed a form that cannot be complete without a body, that close is the
        mistake: it ended the form early, which is what leaves the first
        unmatched ')' with nothing to close.  The error is therefore anchored on
        that earlier close, and the unmatched ')' is reported as the symptom it
        is.  Only when no such early close exists is the location the first ')'
        at which the depth went negative — the earliest point at which an extra
        ')' is provable.

        Args:
            diagnosis: The parenthesis diagnosis for the token stream.
            current_value: The token value found after the parsed expression.
            current_line: Line of that token.
            current_col: Column of that token.

        Returns:
            MenaiASTBuildError describing the extra closing parenthesis.
        """
        extra_line = cast(int, diagnosis.extra_line)
        extra_column = cast(int, diagnosis.extra_column)

        if self.early_close is not None:
            error_line, error_column, early_form, early_open_line, early_open_column = self.early_close

        else:
            error_line = extra_line
            error_column = extra_column
            early_form = ""
            early_open_line = 0
            early_open_column = 0

        culprit = ""
        if self.early_close is not None:
            culprit = (
                f"\n\nThe ')' at line {error_line}, column {error_column} closed the "
                f"'{early_form}' opened at line {early_open_line}, "
                f"column {early_open_column} before it had a body, which is "
                f"what leaves the ')' at line {extra_line}, column {extra_column} with "
                f"nothing to close."
            )

        depth_table = self._depth_table_around(error_line)
        table_block = f"\n\nParenthesis depth by line (start->end):\n{depth_table}" if depth_table else ""

        if current_value == ')':
            follows = "Another ')' follows it."

        else:
            follows = f"'{current_value}' at line {current_line}, column {current_col} follows it."

        if self.early_close is not None:
            context = (
                f"The ')' at line {error_line}, column {error_column} closed the "
                f"'{early_form}' before its body, which left the parentheses "
                f"unbalanced. The ')' at line {extra_line}, column {extra_column} then "
                f"closes nothing: every '(' before it is already matched. "
                f"{follows}"
                f"{culprit}"
                f"{table_block}"
            )

        else:
            context = (
                f"The ')' at line {extra_line}, column {extra_column} closes nothing: "
                f"every '(' before it is already matched, so this ')' is one too many. "
                f"{follows}"
                f"{table_block}"
            )

        return MenaiASTBuildError(
            message=f"Extra closing parenthesis at line {error_line}, column {error_column}",
            line=error_line,
            column=error_column,
            received=f"Unmatched ')' at line {extra_line}, column {extra_column}",
            expected="A matching '(' before this ')'",
            suggestion=f"Remove the ')' at line {error_line}, column {error_column}",
            context=context,
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_form_closed_early_error(
        self,
        current_value: object,
        current_line: int,
        current_col: int,
    ) -> MenaiASTBuildError:
        """
        Create an error for a form closed before it had a body.

        The parentheses balance, so there is no extra ')' to point at.  What
        went wrong is that a ')' closed a form — a 'let' or similar — before its
        body, which makes the following form a second top-level expression
        rather than the body.  The error points at that early ')'.

        Args:
            current_value: The token value found after the parsed expression.
            current_line: Line of that token.
            current_col: Column of that token.

        Returns:
            MenaiASTBuildError describing the form closed before its body.
        """
        early_line, early_column, form, _, _ = cast(
            tuple[int, int, str, int, int], self.early_close
        )

        depth_table = self._depth_table_around(early_line)
        table_block = f"\n\nParenthesis depth by line (start->end):\n{depth_table}" if depth_table else ""

        context = (
            f"The ')' at line {early_line}, column {early_column} closed the '{form}' "
            f"before its body, so '{current_value}' at line {current_line}, "
            f"column {current_col} is a second top-level expression rather than that "
            f"body. The parentheses balance, so no ')' is missing or extra — the "
            f"'{form}' simply ended too early."
            f"{table_block}"
        )

        return MenaiASTBuildError(
            message=f"'{form}' closed before its body at line {early_line}, column {early_column}",
            line=early_line,
            column=early_column,
            received=f"'{form}' has no body before the ')' at line {early_line}, column {early_column}",
            expected=f"A body expression before the ')' that closes the '{form}'",
            suggestion=f"Move the ')' at line {early_line}, column {early_column} to after the body",
            context=context,
            source=self.expression,
            source_file=self.source_file,
        )

    def _early_close_error_for(
        self, form_name: str, form_line: int, form_column: int
    ) -> MenaiASTBuildError | None:
        """
        Build an error for a structural fault caused by an enclosing form that
        closed before its body.

        A form with the wrong number of elements is often the symptom of a ')'
        that closed an enclosing form before its body, so the elements that
        follow are read as further elements of this form.  When the parser
        recorded such a close, that close is the line the reader has to change,
        so it is reported instead of the symptom.

        The diagnosis is the parser's own: a frame is recorded as closing short
        when it is popped with fewer elements than its arity requires, so this
        cannot disagree with the parse the way a separate token walk could.

        Args:
            form_name: The name of the form whose structural check failed.
            form_line: Line of the form whose structural check failed.
            form_column: Column of the form whose structural check failed.

        Returns:
            An error pointing at the ')' that closed the enclosing form early,
            or None when the parser recorded no such close, or when the form
            that closed early is this form itself (in which case the fault is
            its own structural error, not a misplaced ')').
        """
        if self.early_close is None:
            return None

        line, column, closed_form, open_line, open_column = self.early_close

        if open_line == form_line and open_column == form_column:
            return None

        depth_table = self._depth_table_around(line)
        table_block = f"\n\nParenthesis depth by line (start->end):\n{depth_table}" if depth_table else ""

        context = (
            f"The '{form_name}' has the wrong number of elements because the ')' at "
            f"line {line}, column {column} closed the '{closed_form}' before its body. "
            f"The elements that follow were read as further elements of the '{form_name}' "
            f"rather than as the body of the '{closed_form}'. The parentheses balance, "
            f"so no ')' is missing or extra — it is in the wrong place."
            f"{table_block}"
        )

        return MenaiASTBuildError(
            message=(
                f"'{closed_form}' closed before its body at line {line}, "
                f"column {column} (reported as '{form_name}' structure error)"
            ),
            line=line,
            column=column,
            received=f"The ')' at line {line}, column {column} closed the '{closed_form}' before its body",
            expected=f"A body expression before the ')' that closes the '{closed_form}'",
            suggestion=f"Move the ')' at line {line}, column {column} to after the body of the '{closed_form}'",
            context=context,
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_multiple_body_error(
        self, start_line: int, start_col: int
    ) -> MenaiASTBuildError:
        """
        Create an error for a binding form with more than one body expression.

        Menai binding forms accept exactly one body expression and have no
        sequencing form, so a second expression in body position is an error.
        The error points at the first unexpected expression.

        Args:
            start_line: Line where the binding form started
            start_col: Column where the binding form started

        Returns:
            MenaiASTBuildError describing the extra body expression
        """
        token = cast(MenaiToken, self.current_token)

        return MenaiASTBuildError(
            message="let body must be a single expression",
            line=token.line,
            column=token.column,
            received=f"Additional expression starting at line {token.line}, column {token.column}",
            expected="Exactly one body expression: (let ((bindings...)) body)",
            context=(
                f"The let starting at line {start_line}, column {start_col} "
                f"already has a body expression, and Menai has no sequencing form."
            ),
            suggestion=(
                "Nest the extra expressions in a single body, for example "
                "(let (...) (let () expr))"
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_early_bindings_close_error(
        self,
        start_line: int,
        start_col: int,
        close_line: int,
        close_column: int,
    ) -> MenaiASTBuildError:
        """
        Create an error for a binding form whose bindings list closed early.

        The parser reports a "second body expression" because a `)` closed
        the bindings list before its last binding, so the forms that follow are
        read as body expressions.  The depth profile identifies the `)` that
        closed the bindings list, and that is the line the reader has to change,
        not the form the parser happened to flag.

        Args:
            start_line: Line where the binding form started.
            start_col: Column where the binding form started.
            close_line: Line of the `)` that closed the bindings list early.
            close_column: Column of that `)`.

        Returns:
            MenaiASTBuildError describing the early close.
        """
        depth_table = self._depth_table_around(close_line)
        table_block = f"\n\nParenthesis depth by line (start->end):\n{depth_table}" if depth_table else ""

        context = (
            f"The bindings list of the let starting at line {start_line}, "
            f"column {start_col} closed at line {close_line}, column {close_column}, "
            f"but more forms follow at the same depth before the let closes. "
            f"The ')' at line {close_line}, column {close_column} therefore ended the "
            f"bindings list one binding too early, and the forms after it were read as "
            f"body expressions. The parentheses balance, so no ')' is missing or extra "
            f"\u2014 it is in the wrong place."
            f"{table_block}"
        )

        return MenaiASTBuildError(
            message=(
                f"let bindings list closed early at line {close_line}, "
                f"column {close_column}"
            ),
            line=close_line,
            column=close_column,
            received=f"Bindings list closed at line {close_line}, column {close_column}, with further forms inside the let",
            expected="The bindings list to stay open until its last binding, before the single body",
            suggestion=(
                f"Move the ')' at line {close_line}, column {close_column} to after the "
                f"last binding, so the forms that follow are inside the bindings list"
            ),
            context=context,
            source=self.expression,
            source_file=self.source_file,
        )

    def _parse_list(self) -> MenaiASTList:
        """Parse (element1 element2 ...) with enhanced error tracking."""
        # Push opening paren onto tracking stack
        current_token = self.current_token
        assert current_token is not None, "Should not be called at EOF"
        start_line = current_token.line
        start_col = current_token.column

        # The head symbol names the form, so the frame can record it.  Peeking
        # here rather than after the push is what lets an unterminated-form
        # error say which form ran out of input instead of guessing it from the
        # token stream.
        head = self.tokens[self.pos + 1] if self.tokens is not None and self.pos + 1 < len(self.tokens) else None
        form_type = head.value if head is not None and head.type == MenaiTokenType.SYMBOL else '('

        # The arity comes from the same table that drives the parse, so the
        # premature-close check cannot disagree with it.  'let' and 'match' are
        # not in that table because their elements are variadic in shape, but
        # both need a head, a second element, and a body, so their minimum is
        # the same 3.
        roles = _SPECIAL_FORM_ELEMENTS.get(str(form_type))
        required: int | None
        if roles is not None:
            required = len(roles) + 1

        else:
            required = _VARIADIC_FORM_MINIMUM.get(str(form_type))

        self._push_paren_frame(
            start_line, start_col, form_type=str(form_type), required_elements=required
        )

        self._advance()  # consume '('

        elements: list[MenaiASTNode] = []

        # The head symbol decides the form.  A special form is parsed by a path
        # that knows each element's role, because a reserved word can also head a
        # list that is data rather than a form: in (lambda (if) if) the (if) is a
        # parameter list, not an if form, and only the lambda path knows that.
        if head is not None and head.type == MenaiTokenType.SYMBOL and head.value == 'let':
            return self._parse_let_with_tracking(start_line, start_col, elements)

        if head is not None and head.type == MenaiTokenType.SYMBOL and head.value == 'match':
            return self._parse_match(start_line, start_col, elements)

        if head is not None and head.type == MenaiTokenType.SYMBOL and head.value in _SPECIAL_FORM_ELEMENTS:
            return self._parse_special_form(head.value, start_line, start_col, elements)

        return self._parse_generic_list(start_line, start_col, elements, _parse_data_list=False)

    def _parse_generic_list(
        self,
        start_line: int,
        start_col: int,
        elements: list[MenaiASTNode],
        _parse_data_list: bool,
    ) -> MenaiASTList:
        """
        Parse a list's elements until its closing ')'.

        The same loop serves an expression list and a data list.  They differ
        only in how a nested list is parsed: an expression list parses a nested
        list as an expression (so a special form is recognised), while a data
        list parses it as data (so a reserved word inside it is not a form).

        Args:
            start_line: Line where the list opened.
            start_col: Column where the list opened.
            elements: List to accumulate the parsed elements.
            _parse_data_list: True when this list's contents are data.

        Returns:
            The parsed list.

        Raises:
            MenaiASTBuildError: When the input runs out before the list closes.
        """
        while True:
            token = self.current_token
            if token is None or token.type == MenaiTokenType.RPAREN:
                break

            if _parse_data_list:
                elements.append(self._parse_data_element())

            else:
                elements.append(self._parse_expression())

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        self._pop_paren_frame()
        self._advance()  # consume ')'

        return MenaiASTList(tuple(elements), line=start_line, column=start_col, source_file=self.source_file)

    def _parse_data_list(self) -> MenaiASTList:
        """
        Parse a list whose contents are data, not expressions.

        A parameter list, a struct's field list, an enum's variant list, and a
        match pattern are data: a reserved word inside one is a name, not a form.

        Returns:
            The parsed list.
        """
        current_token = self.current_token
        assert current_token is not None, "Should not be called at EOF"
        start_line = current_token.line
        start_col = current_token.column
        self._push_paren_frame(start_line, start_col)
        self._advance()  # consume '('

        return self._parse_generic_list(start_line, start_col, [], _parse_data_list=True)

    def _parse_data_element(self) -> MenaiASTNode:
        """
        Parse one element of a data list.

        A nested list is parsed as data; anything else is an atom and is parsed
        as an expression (atoms carry no form interpretation).
        """
        token = self.current_token
        assert token is not None, "Should not be called at EOF"

        if token.type == MenaiTokenType.LPAREN:
            return self._parse_data_list()

        return self._parse_expression()

    def _parse_pattern(self) -> MenaiASTNode:
        """
        Parse one match pattern and validate its shape.

        A pattern is data, so it is parsed without form interpretation, then
        checked against the pattern grammar.  The shape is a property of the
        pattern itself, so it is checked here rather than by the semantic
        analyzer, which never runs when the parse fails.

        Returns:
            The parsed pattern.

        Raises:
            MenaiASTBuildError: When the pattern's shape is invalid.
        """
        pattern = self._parse_data_element()
        self._validate_pattern(pattern)
        return pattern

    def _validate_pattern(self, pattern: MenaiASTNode) -> None:
        """
        Validate the shape of a parsed match pattern, recursing into sub-patterns.

        A pattern is one of: an atom (a variable, wildcard, or literal), the
        empty list, a predicate pattern `(? pred var)`, a type pattern
        `(: TypeName ...)`, a dotted pattern `(head . tail)`, or a list
        destructuring pattern `(p1 p2 ...)`.

        Args:
            pattern: The parsed pattern to validate.

        Raises:
            MenaiASTBuildError: When the pattern's shape is invalid.
        """
        if not isinstance(pattern, MenaiASTList) or pattern.is_empty():
            return

        elements = pattern.elements
        head = elements[0]

        if isinstance(head, MenaiASTSymbol) and head.name == '?':
            self._validate_predicate_pattern(pattern)
            return

        if isinstance(head, MenaiASTSymbol) and head.name == ':':
            self._validate_type_pattern(pattern)
            return

        self._validate_list_pattern(pattern)

    def _validate_predicate_pattern(self, pattern: MenaiASTList) -> None:
        """Validate a predicate pattern: exactly (? predicate variable)."""
        elements = pattern.elements

        if len(elements) != 3:
            raise self._create_pattern_error(
                pattern,
                "Predicate pattern must be (? predicate variable)",
                f"Got {len(elements)} elements",
                "Exactly 3 elements: (? predicate variable)",
                "Use (? predicate variable) for a predicate test pattern",
            )

        variable = elements[2]
        if not isinstance(variable, MenaiASTSymbol):
            raise self._create_pattern_error(
                variable,
                "Predicate pattern variable must be a symbol",
                f"Variable: {variable.type_name()}",
                "An unquoted symbol (variable name)",
                "Use an unquoted variable name as the third element",
            )

        # The predicate is an expression and is analysed normally.
        self._validate_pattern(elements[1])

    def _validate_type_pattern(self, pattern: MenaiASTList) -> None:
        """Validate a type pattern: (: TypeName sub-pattern ...)."""
        elements = pattern.elements

        if len(elements) < 2:
            raise self._create_pattern_error(
                pattern,
                "Type pattern must name a type",
                f"Got {len(elements)} elements",
                "At least 2 elements: (: TypeName ...)",
                "Name a struct or enum type after the : head symbol",
            )

        type_name = elements[1]
        if not isinstance(type_name, MenaiASTSymbol):
            raise self._create_pattern_error(
                type_name,
                "Type pattern name must be a symbol",
                f"Type name: {type_name.type_name()}",
                "An unquoted symbol (type name)",
                "Use an unquoted type name after the : head symbol",
            )

        # A quoted symbol (an enum variant) binds nothing and is not a
        # sub-pattern; every other trailing element is a sub-pattern.
        for element in elements[2:]:
            if _is_quoted_symbol(element):
                continue

            self._validate_pattern(element)

    def _validate_list_pattern(self, pattern: MenaiASTList) -> None:
        """Validate a list destructuring or dotted pattern."""
        elements = pattern.elements
        dot_positions = [
            index for index, element in enumerate(elements)
            if isinstance(element, MenaiASTSymbol) and element.name == '.'
        ]

        if len(dot_positions) > 1:
            raise self._create_pattern_error(
                pattern,
                "Pattern has more than one dot",
                f"Pattern: {pattern.describe()}",
                "At most one dot, separating head from tail",
                "Use only one dot to separate the head from the tail",
            )

        if not dot_positions:
            for element in elements:
                self._validate_pattern(element)

            return

        dot_position = dot_positions[0]

        if dot_position == 0:
            raise self._create_pattern_error(
                pattern,
                "Pattern has a dot at the beginning",
                f"Pattern: {pattern.describe()}",
                "At least one element before the dot",
                "Put at least one pattern before the dot",
            )

        if dot_position == len(elements) - 1:
            raise self._create_pattern_error(
                pattern,
                "Pattern has a dot at the end",
                f"Pattern: {pattern.describe()}",
                "A tail pattern after the dot",
                "Add a tail pattern after the dot",
            )

        if dot_position != len(elements) - 2:
            raise self._create_pattern_error(
                pattern,
                "Pattern has more than one element after the dot",
                f"Pattern: {pattern.describe()}",
                "Exactly one tail pattern after the dot",
                "Use only one pattern after the dot for the tail",
            )

        for element in elements[:dot_position]:
            self._validate_pattern(element)

        self._validate_pattern(elements[dot_position + 1])

    def _create_pattern_error(
        self,
        node: MenaiASTNode,
        message: str,
        received: str,
        expected: str,
        suggestion: str,
    ) -> MenaiASTBuildError:
        """
        Create an error for a match pattern with an invalid shape.

        Args:
            node: The node to report the error at.
            message: A one-line description of the problem.
            received: What was found.
            expected: What the pattern grammar requires.
            suggestion: How to fix it.

        Returns:
            MenaiASTBuildError describing the invalid pattern.
        """
        return MenaiASTBuildError(
            message=message,
            line=node.line,
            column=node.column,
            received=received,
            expected=expected,
            suggestion=suggestion,
            context=(
                "A match pattern is an atom, the empty list, a predicate "
                "pattern (? predicate variable), a type pattern (: TypeName ...), "
                "a dotted pattern (head . tail), or a list destructuring pattern."
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _parse_special_form(
        self,
        form: str,
        start_line: int,
        start_col: int,
        elements: list[MenaiASTNode],
    ) -> MenaiASTList:
        """
        Parse a special form whose elements are fixed in number and role.

        Each element is parsed by its role (see _SPECIAL_FORM_ELEMENTS), so a
        data element is not mistaken for a form.  The element count is checked
        against the role list, which is the form's arity.

        Args:
            form: The form's head symbol.
            start_line: Line where the form opened.
            start_col: Column where the form opened.
            elements: List to accumulate the parsed elements.

        Returns:
            The parsed form.

        Raises:
            MenaiASTBuildError: When the form has too few or too many elements.
        """
        roles = _SPECIAL_FORM_ELEMENTS[form]

        elements.append(self._parse_expression())  # the head symbol

        for index, role in enumerate(roles):
            if self.current_token is None:
                raise self._create_enhanced_unterminated_error(start_line, start_col)

            if self.current_token.type == MenaiTokenType.RPAREN:
                # Too few elements: there is no element to point at, so the
                # error names the form itself.
                raise self._create_arity_error(
                    form, index + 1, len(roles) + 1, start_line, start_col, None
                )

            elements.append(self._parse_role(role))

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        if self.current_token.type != MenaiTokenType.RPAREN:
            # Too many elements.  An enclosing form that closed before its body
            # is the likelier cause: the elements that follow are then read as
            # further elements of this form.  Report the misplaced ')' rather
            # than the count it produced.
            self._raise_too_many_elements(form, len(roles) + 1, start_line, start_col)

        self._pop_paren_frame()
        self._advance()  # consume ')'

        return MenaiASTList(tuple(elements), line=start_line, column=start_col, source_file=self.source_file)

    def _parse_role(self, role: str) -> MenaiASTNode:
        """Parse one element of a special form according to its role."""
        token = self.current_token
        assert token is not None, "Should not be called at EOF"

        # An element is counted by _parse_expression, which every path below
        # reaches for an atom.  Only a data list bypasses it entirely, so only
        # that path counts for itself.
        if role == 'data-list':
            if token.type == MenaiTokenType.LPAREN:
                self._count_element()
                return self._parse_data_list()

            # Not a list.  Parse it as an ordinary expression so the semantic
            # analyzer reports the shape it expected (e.g. lambda parameters
            # must be a list) rather than the parser failing to make sense of it.
            return self._parse_expression()

        if role == 'data':
            return self._parse_data_element()

        return self._parse_expression()

    def _raise_too_many_elements(
        self,
        form: str,
        required: int,
        start_line: int,
        start_col: int,
    ) -> None:
        """
        Raise the error for a special form with more elements than it takes.

        A form with the wrong number of elements is often the symptom of a ')'
        that closed an enclosing form before its body, so the elements that
        follow are read as further elements of this form.  The parenthesis
        diagnosis identifies that ')', and it is the line the reader has to
        change, so it is reported in preference to the count.

        Args:
            form: The form's head symbol.
            required: The number of elements the form requires, head included.
            start_line: Line where the form opened.
            start_col: Column where the form opened.

        Returns:
            Never returns: it always raises.
        """
        error = self._early_close_error_for(form, start_line, start_col)
        if error is not None:
            raise error

        token = cast(MenaiToken, self.current_token)
        raise self._create_arity_error(form, required + 1, required, start_line, start_col, token)

    def _parse_match(
        self,
        start_line: int,
        start_col: int,
        elements: list[MenaiASTNode],
    ) -> MenaiASTList:
        """
        Parse a match form: (match value (pattern result) ...).

        The value and each clause's result are expressions; each clause's pattern
        is data, so a reserved word inside a pattern is a name, not a form.  The
        clauses are variadic, so at least one is required.

        Args:
            start_line: Line where the match opened.
            start_col: Column where the match opened.
            elements: List to accumulate the parsed elements.

        Returns:
            The parsed match form.

        Raises:
            MenaiASTBuildError: When the value or a clause is missing, or a
                clause is not a two-element (pattern result) form.
        """
        elements.append(self._parse_expression())  # the head symbol

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        if self.current_token.type == MenaiTokenType.RPAREN:
            raise self._create_arity_error('match', 1, 3, start_line, start_col, None)

        elements.append(self._parse_expression())  # the value

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        if self.current_token.type == MenaiTokenType.RPAREN:
            raise self._create_arity_error('match', 2, 3, start_line, start_col, None)

        while self.current_token is not None and self.current_token.type != MenaiTokenType.RPAREN:
            self._count_element()
            elements.append(self._parse_match_clause())

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        self._pop_paren_frame()
        self._advance()  # consume ')'

        return MenaiASTList(tuple(elements), line=start_line, column=start_col, source_file=self.source_file)

    def _parse_match_clause(self) -> MenaiASTList:
        """
        Parse one match clause: (pattern result).

        The pattern is data and the result an expression.

        Returns:
            The parsed clause.

        Raises:
            MenaiASTBuildError: When the clause is not a two-element form.
        """
        token = self.current_token
        assert token is not None, "Should not be called at EOF"

        if token.type != MenaiTokenType.LPAREN:
            raise self._create_clause_shape_error(token)

        clause_line = token.line
        clause_col = token.column
        self._push_paren_frame(clause_line, clause_col, form_type='match-clause')
        self._advance()  # consume '('

        clause: list[MenaiASTNode] = []

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(clause_line, clause_col)

        if self.current_token.type == MenaiTokenType.RPAREN:
            raise self._create_clause_shape_error(self.current_token)

        clause.append(self._parse_pattern())  # the pattern

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(clause_line, clause_col)

        if self.current_token.type == MenaiTokenType.RPAREN:
            raise self._create_clause_shape_error(self.current_token)

        clause.append(self._parse_expression())  # the result

        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(clause_line, clause_col)

        if self.current_token.type != MenaiTokenType.RPAREN:
            raise self._create_clause_shape_error(self.current_token)

        self._pop_paren_frame()
        self._advance()  # consume ')'

        return MenaiASTList(tuple(clause), line=clause_line, column=clause_col, source_file=self.source_file)

    def _create_clause_shape_error(self, token: MenaiToken) -> MenaiASTBuildError:
        """Create an error for a match clause that is not (pattern result)."""
        return MenaiASTBuildError(
            message="Match clause must be a (pattern result) form",
            line=token.line,
            column=token.column,
            received=f"Found: {token.value}",
            expected="Each clause: (pattern result-expression)",
            suggestion="Give each clause exactly a pattern and a result expression",
            context=(
                "A match clause is a two-element form: a pattern and the result "
                "expression to use when the pattern matches."
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_arity_error(
        self,
        form: str,
        count: int,
        required: int,
        start_line: int,
        start_col: int,
        offending: MenaiToken | None,
    ) -> MenaiASTBuildError:
        """
        Create an error for a special form with the wrong number of elements.

        Args:
            form: The form's head symbol.
            count: The number of elements the form has, the head included.
            required: The number of elements the form requires, the head included.
            start_line: Line where the form opened.
            start_col: Column where the form opened.
            offending: The token of the first element past the bound, or None
                when the form has too few elements and there is nothing to point
                at.

        Returns:
            MenaiASTBuildError describing the wrong element count.
        """
        # 'match' takes at least this many elements (its clauses are variadic);
        # every other form takes exactly this many.
        if form == 'match':
            count_phrase = f"at least {required} elements"

        else:
            count_phrase = f"exactly {required} elements"

        expected = f"{count_phrase.capitalize()}: {self._form_shape(form)}"

        if offending is not None:
            # The extras have not been parsed, so their number is not known:
            # report the count as a lower bound rather than an exact figure.
            received = f"More than {required - 1} elements"

        else:
            received = f"Got {count} elements"

        if offending is not None:
            error_line = offending.line
            error_column = offending.column
            where = (
                f"The '{form}' at line {start_line}, column {start_col} takes "
                f"{count_phrase}, but the element at line {error_line}, column "
                f"{error_column} is one too many."
            )
            suggestion = (
                f"Remove the element at line {error_line}, column {error_column}, or "
                f"nest it in the '{form}' body"
            )

        else:
            error_line = start_line
            error_column = start_col
            where = (
                f"The '{form}' at line {start_line}, column {start_col} takes "
                f"{count_phrase}, but has {count}."
            )
            suggestion = f"Give '{form}' the elements it requires: {self._form_shape(form)}"

        return MenaiASTBuildError(
            message=f"'{form}' has the wrong number of elements",
            line=error_line,
            column=error_column,
            received=received,
            expected=expected,
            suggestion=suggestion,
            context=where,
            source=self.expression,
            source_file=self.source_file,
        )

    def _form_shape(self, form: str) -> str:
        """
        Render the shape of a special form.

        The shape names each element's role, so the reader sees what the form
        takes without a worked example unrelated to their code.
        """
        return _SPECIAL_FORM_SHAPE[form]

    def _parse_let_with_tracking(
        self,
        start_line: int,
        start_col: int,
        elements: list[MenaiASTNode]
    ) -> MenaiASTList:
        """
        Parse a 'let' form with special tracking for binding-level errors.

        Args:
            start_line: Line where the let started
            start_col: Column where the let started
            elements: List to accumulate parsed elements

        Returns:
            Parsed let expression as MenaiASTList
        """
        # Parse 'let' keyword
        elements.append(self._parse_expression())

        # Check for bindings list
        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        # If we hit a closing paren right after 'let', just return what we have
        # and let the evaluator complain about the structure
        if self.current_token.type == MenaiTokenType.RPAREN:
            self._pop_paren_frame()
            self._advance()  # consume ')'
            return MenaiASTList(tuple(elements), line=start_line, column=start_col, source_file=self.source_file)

        # Parse bindings with special tracking
        bindings_close_line: int | None = None
        bindings_close_column: int | None = None
        if self.current_token.type == MenaiTokenType.LPAREN:
            bindings = self._parse_let_bindings()
            self._count_element()
            elements.append(bindings)
            bindings_close_line = self.last_close_line
            bindings_close_column = self.last_close_column

        else:
            # Not our job to validate structure - just parse what's there
            elements.append(self._parse_expression())

        # Parse body
        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        if self.current_token.type != MenaiTokenType.RPAREN:
            elements.append(self._parse_expression())

        # A binding form accepts exactly one body expression.  Anything other
        # than the closing paren here is a second body expression, which the
        # language does not support.  Report it at the offending form rather
        # than blindly consuming a token that is not ')'.
        if self.current_token is None:
            raise self._create_enhanced_unterminated_error(start_line, start_col)

        if self.current_token.type != MenaiTokenType.RPAREN:
            error = self._early_close_error_for("let", start_line, start_col)
            if error is not None:
                raise error

            # The bindings list has no head symbol, so it has no arity to check
            # against: closing it after two bindings is not by itself wrong.
            # What marks it as closed early is the shape of what follows.  A
            # form that reads as a binding, where only the single body was
            # allowed, is a binding the list should have kept.  The parser stops
            # at the first such form, so that one is what it can test.
            extra_forms = elements[2:]
            if (
                bindings_close_line is not None
                and bindings_close_column is not None
                and extra_forms
                and self._is_binding_node(extra_forms[0])
            ):
                raise self._create_early_bindings_close_error(
                    start_line, start_col, bindings_close_line, bindings_close_column
                )

            raise self._create_multiple_body_error(start_line, start_col)

        # Pop from stack when successfully closed
        self._pop_paren_frame()

        self._advance()  # consume ')'

        return MenaiASTList(tuple(elements), line=start_line, column=start_col, source_file=self.source_file)

    def _parse_let_bindings(self) -> MenaiASTList:
        """
        Parse the bindings list of a let form with per-binding tracking.

        Returns:
            MenaiASTList of bindings
        """
        assert self.current_token is not None, "Current token must not be None here"
        bindings_start_line = self.current_token.line
        bindings_start_col = self.current_token.column

        # Push frame for bindings list
        self._push_paren_frame(
            bindings_start_line,
            bindings_start_col,
            is_bindings_list=True,
            enclosing_form='let',
        )

        self._advance()  # consume '('

        bindings: list[MenaiASTNode] = []
        binding_index = 0

        # Track the previous binding's name and the position of its closing
        # paren.  If the bindings list is missing its own close paren, the next
        # group (the form's body) is misread as a binding, and the missing ')'
        # belongs immediately after this previous binding.
        prev_binding_name: str | None = None
        prev_binding_end_line: int | None = None
        prev_binding_end_column: int | None = None

        while self.current_token is not None and self.current_token.type != MenaiTokenType.RPAREN:
            binding_index += 1

            # Each binding should start with '('
            if self.current_token.type == MenaiTokenType.LPAREN:
                # A reserved form can never be a binding, so a form headed by one
                # is the let's body, read as a binding because the bindings list
                # is missing its close.  That close belongs immediately after the
                # previous binding, which is the line the reader has to change.
                if (
                    self._form_head_at(self.pos) in _RESERVED_FORMS
                    and self.bindings_close_after is None
                ):
                    self.bindings_close_after = (
                        prev_binding_end_line,
                        prev_binding_end_column,
                    )

                binding = self._parse_single_binding(
                    binding_index, prev_binding_name,
                    prev_binding_end_line, prev_binding_end_column,
                )
                bindings.append(binding)
                self._count_element()

                prev_binding_name = self._binding_name(binding)
                prev_binding_end_line = self.last_close_line
                prev_binding_end_column = self.last_close_column

            else:
                # A binding is a two-element (name value) form, so it must start
                # with '('.  Anything else here is not a binding.
                raise self._create_binding_shape_error(
                    binding_index, self.current_token
                )

        if self.current_token is None:
            # EOF while parsing bindings - create enhanced error
            raise self._create_incomplete_bindings_error(bindings, bindings_start_line, bindings_start_col)

        # Pop bindings frame
        self._pop_paren_frame()

        self._advance()  # consume ')'

        return MenaiASTList(tuple(bindings), line=bindings_start_line, column=bindings_start_col, source_file=self.source_file)

    def _binding_name(self, binding: MenaiASTNode) -> str | None:
        """Return the binding's name symbol if it has one, else None."""
        if isinstance(binding, MenaiASTList) and binding.elements:
            first = binding.elements[0]
            if isinstance(first, MenaiASTSymbol):
                return first.name

        return None

    def _create_binding_shape_error(
        self,
        binding_index: int,
        token: MenaiToken | None,
    ) -> MenaiASTBuildError:
        """
        Create an error for a binding that is not a two-element (name value) form.

        A binding is a list of exactly two elements.  This covers both a binding
        that is not a list at all and one whose value is absent.

        Args:
            binding_index: The binding's position in the bindings list (1-based).
            token: The token where the binding starts, or None at end of input.

        Returns:
            MenaiASTBuildError describing the malformed binding.
        """
        line = token.line if token is not None else 1
        column = token.column if token is not None else 1

        return MenaiASTBuildError(
            message=f"let binding {binding_index} must be a (name value) form",
            line=line,
            column=column,
            received="A binding that is not a two-element list",
            expected="Each binding: (variable value-expression)",
            suggestion="Write each binding as (name value), with a name and a value",
            context=(
                f"Binding {binding_index} of the let at line {line}, column "
                f"{column} is not a (name value) form. A binding is a list of "
                f"exactly two elements: the variable name and its value."
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_binding_name_error(
        self,
        binding_index: int,
        name_node: MenaiASTNode,
    ) -> MenaiASTBuildError:
        """
        Create an error for a binding whose name is not a symbol.

        Args:
            binding_index: The binding's position in the bindings list (1-based).
            name_node: The node parsed where the name should have been.

        Returns:
            MenaiASTBuildError describing the malformed binding name.
        """
        return MenaiASTBuildError(
            message=f"let binding {binding_index} variable must be a symbol",
            line=name_node.line,
            column=name_node.column,
            received=f"Variable: {name_node.type_name()}",
            expected="Unquoted symbol (variable name)",
            suggestion="Use an unquoted variable name in each binding",
            context=(
                f"Binding {binding_index} of the let names its variable with "
                f"a {name_node.type_name()}. A binding name is an unquoted symbol."
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _parse_single_binding(
        self,
        binding_index: int,
        prev_binding_name: str | None,
        prev_binding_end_line: int | None,
        prev_binding_end_column: int | None,
    ) -> MenaiASTList:
        """
        Parse a single let binding with tracking.

        Args:
            binding_index: The index of this binding (1-based)
            prev_binding_name: Name of the preceding binding, if any
            prev_binding_end_line: Line of the preceding binding's closing ')', if any
            prev_binding_end_column: Column of the preceding binding's closing ')', if any

        Returns:
            MenaiASTList representing the binding
        """
        assert self.current_token is not None, "Current token must not be None here"
        binding_start_line = self.current_token.line
        binding_start_col = self.current_token.column

        # Push frame for this binding
        binding_frame = self._push_paren_frame(binding_start_line, binding_start_col)

        self._advance()  # consume '('

        elements = []

        # Parse variable name (if present)
        if self.current_token is not None and self.current_token.type == MenaiTokenType.SYMBOL:
            var_name = self.current_token.value
            binding_frame.related_symbol = var_name
            elements.append(self._parse_expression())

        elif self.current_token is not None and self.current_token.type != MenaiTokenType.RPAREN:
            # The binding's name must be a symbol.  Parse the element so the
            # remaining structure is still consumed, then report it.
            elements.append(self._parse_expression())
            raise self._create_binding_name_error(binding_index, elements[0])

        # Parse value (if present)
        if self.current_token is not None and self.current_token.type != MenaiTokenType.RPAREN:
            elements.append(self._parse_expression())

        if self.current_token is None:
            # End of input inside the binding: the binding never closed, which
            # is an unterminated list rather than a malformed binding.
            raise self._create_enhanced_unterminated_error(binding_start_line, binding_start_col)

        if len(elements) < 2:
            # The binding closed with one element, so its value is absent.
            raise self._create_binding_shape_error(binding_index, self.current_token)

        # A binding (name value) has exactly 2 elements.  If a third element
        # appears here, the value's close paren was stolen by the binding —
        # i.e. there is a missing ')' inside the value expression.
        if self.current_token is not None and self.current_token.type != MenaiTokenType.RPAREN:
            # Determine the name of the form that appears where ')' was expected
            form_name = "expression"
            if self.current_token.type == MenaiTokenType.SYMBOL:
                form_name = self.current_token.value

            elif self.current_token.type == MenaiTokenType.LPAREN:
                # Peek at the token after '(' to get the form name
                peek_pos = self.pos + 1
                tokens = cast(list[MenaiToken], self.tokens)
                if peek_pos < len(tokens) and tokens[peek_pos].type == MenaiTokenType.SYMBOL:
                    form_name = tokens[peek_pos].value

            var_name = binding_frame.related_symbol or f"#{binding_index}"
            error = self._early_close_error_for("binding", binding_start_line, binding_start_col)
            if error is not None:
                raise error

            raise self._create_third_element_error(
                var_name=var_name,
                form_name=form_name,
                binding_start_line=binding_start_line,
                binding_start_col=binding_start_col,
                prev_binding_name=prev_binding_name,
                prev_binding_end_line=prev_binding_end_line,
                prev_binding_end_column=prev_binding_end_column,
            )

        # Pop binding frame
        self._pop_paren_frame()

        self._advance()  # consume ')'

        return MenaiASTList(tuple(elements), line=binding_start_line, column=binding_start_col, source_file=self.source_file)

    def _create_third_element_error(
        self,
        var_name: str,
        form_name: str,
        binding_start_line: int,
        binding_start_col: int,
        prev_binding_name: str | None,
        prev_binding_end_line: int | None,
        prev_binding_end_column: int | None,
    ) -> MenaiASTBuildError:
        """
        Create an error for a binding that has a third element where ')' was expected.

        A binding has exactly two elements: a name and a value.  When a third
        element appears, there are two possible causes and the parser cannot
        distinguish them locally:

          1. The value expression is missing its closing ')'.
          2. The enclosing bindings list is missing its closing ')' after the
             previous binding, so the form's body was misread as a binding.

        Case 2 is only possible when a previous binding exists.  When it does,
        the error names both possibilities and points at the third element — the
        one thing that is definitely wrong and definitely observable — rather
        than at a position that is correct under only one of the two
        hypotheses.  No single insertion point is asserted, because there is no
        evidence for one.

        Args:
            var_name: The name of the binding being parsed
            form_name: The name of the form appearing where ')' was expected
            binding_start_line: Line where this binding started
            binding_start_col: Column where this binding started
            prev_binding_name: Name of the preceding binding, if any
            prev_binding_end_line: Line of the preceding binding's closing ')', if any
            prev_binding_end_column: Column of the preceding binding's closing ')', if any

        Returns:
            MenaiASTBuildError describing the malformed binding
        """
        token = cast(MenaiToken, self.current_token)

        if prev_binding_name is None:
            # No preceding binding — the bindings list has nothing before this
            # binding, so the value's close paren must be the one that is missing.
            # The parser has already parsed the value, so the end of the last
            # token consumed is exactly where that ')' belongs — which is the
            # line the reader has to change, not the line the third element
            # appears on.
            insertion_line = self.last_token_end_line
            insertion_column = self.last_token_end_column
            return MenaiASTBuildError(
                message=(
                    f"Missing closing parenthesis — insert ')' at line "
                    f"{insertion_line}, column {insertion_column}, at the end of "
                    f"binding '{var_name}''s value. Without it, '{form_name}' at "
                    f"line {token.line}, column {token.column} is read as a third "
                    f"element of binding '{var_name}'"
                ),
                line=insertion_line,
                column=insertion_column,
                expected="')' to close the binding",
                suggestion=(
                    f"Insert ')' at line {insertion_line}, column {insertion_column}: "
                    f"the value expression of binding '{var_name}' (opened at line "
                    f"{binding_start_line}, column {binding_start_col}) is missing its "
                    f"closing parenthesis"
                ),
                context=(
                    f"The binding '{var_name}' has parsed its name and value (2 elements), "
                    f"but '{form_name}' at line {token.line}, "
                    f"column {token.column} appears as a third element. "
                    f"This means the value expression is missing a ')' — its close paren "
                    f"was consumed by the binding instead. The missing ')' belongs at "
                    f"line {insertion_line}, column {insertion_column}, immediately after "
                    f"the value."
                    f"{self._depth_table_block(insertion_line)}"
                ),
                source=self.expression,
                source_file=self.source_file,
            )

        return MenaiASTBuildError(
            message=(
                f"Missing closing parenthesis — '{form_name}' at line "
                f"{token.line}, column {token.column} is read as a third element "
                f"of binding '{var_name}'"
            ),
            line=token.line,
            column=token.column,
            expected="')' to close the binding",
            suggestion=(
                f"Add a ')' to close the value of binding '{var_name}' if its value "
                f"is missing one, or to close the bindings list after binding "
                f"'{prev_binding_name}' if the form that follows was meant to be "
                f"the body. The two are indistinguishable here, so check which "
                f"form the ')' at line {prev_binding_end_line}, column "
                f"{prev_binding_end_column} was meant to close."
            ),
            context=(
                f"Binding '{var_name}' at line {binding_start_line}, "
                f"column {binding_start_col} already has its name and value, but "
                f"'{form_name}' at line {token.line}, column {token.column} appears as "
                f"a third element. Two different mistakes produce exactly this state, "
                f"and the parser cannot tell them apart:\n"
                f"  1. The value expression of binding '{var_name}' is missing a ')',\n"
                f"     so the binding absorbed the element that follows it.\n"
                f"  2. The enclosing bindings list is missing its ')' after binding "
                f"'{prev_binding_name}' (line {prev_binding_end_line}, "
                f"column {prev_binding_end_column}), so the body was read as a binding.\n"
                f"Nothing in the token stream distinguishes them, so no single "
                f"position is reported as the place to insert the ')'."
                f"{self._depth_table_block(token.line)}"
            ),
            source=self.expression,
            source_file=self.source_file,
        )

    def _create_incomplete_bindings_error(
        self,
        parsed_bindings: list[MenaiASTNode],
        bindings_start_line: int,
        bindings_start_col: int,
    ) -> MenaiASTBuildError:
        """
        Create enhanced error when EOF is reached while parsing let bindings.

        Args:
            parsed_bindings: List of successfully parsed bindings
            bindings_start_line: Line where bindings list started
            bindings_start_col: Column where bindings list started

        Returns:
            MenaiASTBuildError with detailed context
        """
        # Analyze the parsed bindings to show what completed successfully
        binding_summary = []
        for i, binding in enumerate(parsed_bindings, 1):
            if isinstance(binding, MenaiASTList):
                symbol_binding = binding.get(0)
                if binding.length() >= 1 and isinstance(symbol_binding, MenaiASTSymbol):
                    var_name = symbol_binding.name
                    status = "✓" if binding.length() == 2 else "✗"
                    binding_summary.append(f"  {i}. ({var_name} ...) {status}")

                else:
                    binding_summary.append(f"  {i}. <invalid binding> ✗")

            else:
                binding_summary.append(f"  {i}. <not a list> ✗")

        summary_text = "\n".join(binding_summary) if binding_summary else "  (no complete bindings)"

        depth = len(self.paren_stack)
        assert depth >= 2, "Bindings error should have at least 2 frames on stack"

        paren_word = "parentheses"  # Always plural since depth >= 2

        error_line, error_column, where, expected, table_block = self._missing_close_report(
            bindings_start_line, bindings_start_col
        )

        # A form that closed before its body is why the input ran out with a form
        # still open: the ')' that ended it early is the mistake, and the open
        # form above it is the symptom.
        early_close_block = ""
        if self.early_close is not None:
            early_line, early_column, early_form, early_open_line, early_open_column = self.early_close
            early_close_block = (
                f"\n\nThe '{early_form}' at line {early_open_line}, "
                f"column {early_open_column} was closed by the ')' at "
                f"line {early_line}, column {early_column} "
                f"before its body, which is what left the input unbalanced."
            )

        scope_lines = self._open_scope_lines()
        scope_block = "Forms still open (outermost first):\n" + "\n".join(scope_lines) if scope_lines else ""

        context_msg = (
            f"Reached end of input while parsing let bindings.\n\n"
            f"{where}\n\n"
            f"Bindings parsed:\n{summary_text}\n\n"
            f"{scope_block}"
            f"{early_close_block}"
            f"{table_block}"
        )

        return MenaiASTBuildError(
            message=f"Incomplete let bindings - missing {depth} closing {paren_word}",
            line=error_line,
            column=error_column,
            expected=expected,
            suggestion=where,
            context=context_msg,
            source=self.expression,
            source_file=self.source_file,
        )

    def _parse_quoted_expression(self) -> MenaiASTList:
        """
        Parse 'expr and convert to (quote expr) with detailed error reporting.

        Returns:
            MenaiASTList representing (quote expr)

        Raises:
            MenaiASTBuildError: If quote is incomplete or malformed
        """
        quote_line = self.current_token.line if self.current_token else 1
        quote_col = self.current_token.column if self.current_token else 1
        self._advance()  # consume quote

        # Check if we have something to quote
        if self.current_token is None:
            raise MenaiASTBuildError(
                message="Incomplete quote expression",
                line=quote_line,
                column=quote_col,
                received="Quote symbol ' with nothing to quote",
                expected="Expression after quote symbol",
                suggestion="Add an expression after the ' symbol",
                context="Quote symbol must be followed by something to quote",
                source=self.expression,
                source_file=self.source_file,
            )

        # Parse the expression to be quoted
        quoted_expr = self._parse_expression()

        # Transform 'expr into (quote expr)
        quote_symbol = MenaiASTSymbol("quote", line=quote_line, column=quote_col, source_file=self.source_file)
        return MenaiASTList((quote_symbol, quoted_expr), line=quote_line, column=quote_col, source_file=self.source_file)

    def _advance(self) -> None:
        """Move to the next token."""
        # Track where the current token ends before advancing
        token = self.current_token
        assert token is not None, "Cannot advance when current token is None"
        # For simplicity, just track the end as the token's line/column + length
        # (This is approximate but good enough for error messages)
        self.last_token_end_line = token.line
        self.last_token_end_column = token.column + token.length

        pos = self.pos + 1
        self.pos = pos
        tokens = self.tokens
        if tokens is not None and pos < len(tokens):
            self.current_token = tokens[pos]

        else:
            self.current_token = None
