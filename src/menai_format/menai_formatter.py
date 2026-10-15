"""
Menai source formatter.

Implements the formatting style defined by ADR-0051.  The formatter lexes the
source (preserving comments), builds a tree, and renders it with a single
recursive pass that decides compact versus multi-line layout at each list from
the column at which the list starts.
"""

from collections.abc import Callable
from dataclasses import dataclass

from menai.ast.menai_lexer import MenaiLexer
from menai.ast.menai_token import MenaiToken, MenaiTokenType


@dataclass
class FormatOptions:
    """Options for controlling formatter behavior."""
    indent_size: int = 2
    max_width: int = 100
    comment_spacing: int = 2


class ASTNode:
    """Base class for AST nodes."""
    start_line: int
    end_line: int
    blank_before: bool


@dataclass
class ASTAtom(ASTNode):
    """An atomic value (symbol, number, string, boolean)."""
    value: str

    def __init__(self, source_value: str, start_line: int):
        self.value = source_value
        self.start_line = start_line
        self.end_line = start_line
        self.blank_before = False


@dataclass
class ASTQuote(ASTNode):
    """A quoted expression."""
    expr: ASTNode

    def __init__(self, expr: ASTNode, start_line: int):
        self.expr = expr
        self.start_line = start_line
        self.end_line = expr.end_line if expr else start_line
        self.blank_before = False


@dataclass
class ASTComment(ASTNode):
    """A comment."""
    text: str
    is_eol: bool

    def __init__(self, text: str, start_line: int, is_eol: bool):
        self.text = text
        self.start_line = start_line
        self.end_line = start_line
        self.is_eol = is_eol
        self.blank_before = False


@dataclass
class ASTList(ASTNode):
    """A list with elements and associated comments."""
    elements: list[ASTNode | ASTComment]

    def __init__(self, elements: list[ASTNode | ASTComment], start_line: int):
        self.elements = elements
        self.start_line = start_line
        self.end_line = start_line
        self.blank_before = False


COMPOUND_FORMS = frozenset({'if', 'lambda', 'let', 'let*', 'letrec'})
ALIGN_ONLY_FORMS = frozenset({'if', 'and', 'or'})


class TreeBuilder:
    """Build tree from tokens."""

    def __init__(self, tokens: list[MenaiToken]):
        self.tokens = tokens
        self.pos = 0

    def build(self) -> list[ASTNode]:
        """Build list of top-level expressions."""
        result: list[ASTNode] = []
        last_code_line = -1
        prev_end_line = -1

        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]

            if token.type == MenaiTokenType.COMMENT:
                is_eol = bool(token.line == last_code_line)
                comment_node = ASTComment(token.value, token.line, is_eol)
                comment_node.blank_before = token.line - prev_end_line > 1
                self.pos += 1
                result.append(comment_node)
                prev_end_line = token.line

            else:
                node = self._parse_expr()
                if node:
                    node.blank_before = node.start_line - prev_end_line > 1
                    result.append(node)
                    last_code_line = node.end_line
                    prev_end_line = node.end_line

        return result

    def _parse_expr(self) -> ASTNode | None:
        """Parse a single expression."""
        if self.pos >= len(self.tokens):
            return None

        token = self.tokens[self.pos]

        if token.type == MenaiTokenType.LPAREN:
            return self._parse_list()

        if token.type == MenaiTokenType.QUOTE:
            start_line = token.line
            self.pos += 1
            expr = self._parse_expr()
            return ASTQuote(expr, start_line) if expr else None

        atom = ASTAtom(self._format_atom_value(token), token.line)
        self.pos += 1
        return atom

    def _format_atom_value(self, token: MenaiToken) -> str:
        """Format an atom's value as a string."""
        if token.raw is not None:
            return token.raw

        if token.type == MenaiTokenType.NONE:
            return '#none'

        return str(token.value)

    def _parse_list(self) -> ASTList:
        """Parse a list."""
        start_line = self.tokens[self.pos].line
        self.pos += 1
        end_line = start_line

        elements: list[ASTNode | ASTComment] = []
        last_code_line = start_line
        prev_end_line = start_line

        while self.pos < len(self.tokens) and self.tokens[self.pos].type != MenaiTokenType.RPAREN:
            token = self.tokens[self.pos]

            if token.type == MenaiTokenType.COMMENT:
                is_eol = bool(token.line == last_code_line)
                comment = ASTComment(token.value, token.line, is_eol)
                comment.blank_before = token.line - prev_end_line > 1
                elements.append(comment)
                self.pos += 1
                prev_end_line = token.line

            else:
                expr = self._parse_expr()
                if expr:
                    expr.blank_before = expr.start_line - prev_end_line > 1
                    elements.append(expr)
                    if isinstance(expr, (ASTAtom, ASTList, ASTQuote)):
                        last_code_line = expr.end_line
                        prev_end_line = expr.end_line

        if self.pos < len(self.tokens):
            end_line = self.tokens[self.pos].line
            self.pos += 1

        result = ASTList(elements, start_line)
        result.end_line = end_line
        return result


class Renderer:
    """
    Render a tree to formatted source.

    Layout is decided top-down: a list is rendered compactly when it contains no
    comments and its compact rendering ends at or before the configured width,
    and multi-line otherwise.  Because a list's column depends on how its parent
    was laid out, planning and rendering are a single recursive pass.
    """

    def __init__(self, options: FormatOptions):
        self.options = options

    def render(self, nodes: list[ASTNode]) -> str:
        """Render all top-level nodes."""
        parts: list[str] = []

        i = 0
        while i < len(nodes):
            node = nodes[i]

            if parts and node.blank_before:
                parts.append('\n')

            if isinstance(node, ASTComment):
                parts.append(node.text)
                parts.append('\n')

            else:
                rendered = self._render_node(node, 0)
                parts.append(rendered)

                if i + 1 < len(nodes):
                    comment = nodes[i + 1]
                    if isinstance(comment, ASTComment) and comment.is_eol:
                        parts.append(' ' * self.options.comment_spacing)
                        parts.append(comment.text)
                        i += 1

                parts.append('\n')

            i += 1

        return self._cleanup(''.join(parts))

    def _cleanup(self, text: str) -> str:
        """Strip trailing whitespace and collapse runs of blank lines."""
        lines = [line.rstrip() for line in text.split('\n')]

        cleaned: list[str] = []
        blank_count = 0
        for line in lines:
            if line == '':
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)

            else:
                blank_count = 0
                cleaned.append(line)

        result = '\n'.join(cleaned)
        if result and not result.endswith('\n'):
            result += '\n'

        return result

    def _append_close(self, lines: list[str]) -> None:
        """
        Append the closing paren to the last line.

        A rendered list may end with a comment: a comment that trails the last
        element of the list is an element in its own right.  A comment runs to
        the end of its line, so the closing paren cannot share that line without
        being swallowed into the comment text.  It is placed on its own line,
        aligned with the last line of code, instead.
        """
        if lines and lines[-1].lstrip().startswith(';'):
            indent = next(
                (len(line) - len(line.lstrip()) for line in reversed(lines) if line.strip() and not line.lstrip().startswith(';')),
                0,
            )
            lines.append(' ' * indent + ')')

        else:
            lines[-1] += ')'

    def _render_node(self, node: ASTNode, column: int, trailing: int = 0) -> str:
        """Render a single node starting at the given column, with trailing columns reserved after it."""
        if isinstance(node, ASTAtom):
            return node.value

        if isinstance(node, ASTComment):
            return node.text

        if isinstance(node, ASTQuote):
            expr_str = self._render_node(node.expr, column + 1, trailing) if node.expr else ''
            return f"'{expr_str}"

        if isinstance(node, ASTList):
            return self._render_list(node, column, trailing)

        return ''

    def _try_compact(self, node: ASTNode) -> str | None:
        """Return the compact single-line rendering, or None if not possible."""
        if isinstance(node, ASTAtom):
            return node.value

        if isinstance(node, ASTQuote):
            inner = self._try_compact(node.expr) if node.expr else ''
            return f"'{inner}" if inner is not None else None

        if isinstance(node, ASTList):
            if any(isinstance(elem, ASTComment) for elem in node.elements):
                return None

            if any(elem.blank_before for elem in node.elements[1:]):
                return None

            head = node.elements[0] if node.elements else None
            if isinstance(head, ASTAtom):
                if head.value == 'match':
                    return None

                if head.value in ('and', 'or') and len(node.elements) >= 3:
                    return None

            parts = ['(']
            for i, elem in enumerate(node.elements):
                if i > 0:
                    parts.append(' ')

                compact = self._try_compact(elem)
                if compact is None:
                    return None

                parts.append(compact)

            parts.append(')')
            return ''.join(parts)

        return None

    def _has_nested_body(self, head_name: str, body: list[ASTNode | ASTComment]) -> bool:
        """Return whether a compound form has a nested body, so it must be multi-line."""
        if head_name in ('lambda', 'let', 'let*', 'letrec'):
            body = body[1:]

        return any(isinstance(elem, (ASTList, ASTQuote)) for elem in body)

    def _all_peers(self, head: ASTNode, body: list[ASTNode | ASTComment]) -> bool:
        """Return whether a general list is a list of peers, each on its own line."""
        head_name = head.value if isinstance(head, ASTAtom) else None
        if head_name in ALIGN_ONLY_FORMS:
            return False

        if len(body) < 2:
            return False

        return all(isinstance(elem, (ASTList, ASTQuote)) for elem in body)

    def _render_list(self, lst: ASTList, column: int, trailing: int = 0) -> str:
        """Render a list, choosing compact or multi-line layout, with trailing columns reserved after it."""
        if not lst.elements:
            return '()'

        head = lst.elements[0]
        head_name = head.value if isinstance(head, ASTAtom) else None
        body = lst.elements[1:]

        if head_name in ('and', 'or') and len(body) >= 2:
            return self._render_general(head, body, column, trailing)

        if head_name == 'match':
            return self._render_match(body, column, trailing)

        compact = self._try_compact(lst)
        compound = head_name in COMPOUND_FORMS and self._has_nested_body(head_name, body)

        if (
            compact is not None
            and not compound
            and column + len(compact) + trailing <= self.options.max_width
        ):
            return compact

        if head_name == 'dict':
            return self._render_dict(head, body, column, trailing)

        if head_name in ('let', 'let*', 'letrec'):
            return self._render_binding_form(head_name, body, column, trailing)

        if head_name == 'lambda':
            return self._render_lambda(body, column, trailing)

        return self._render_general(head, body, column, trailing)

    def _render_general(self, head: ASTNode, body: list[ASTNode | ASTComment], column: int, trailing: int = 0) -> str:
        """Render a general list: head and first argument, then one per line, or head alone when there is one argument."""
        head_str = self._render_node(head, column + 1)

        if not body:
            return f'({head_str})'

        if self._all_peers(head, body):
            inner_col = column + self.options.indent_size
            lines = [f'({head_str}']
            lines.extend(self._render_tail(body, inner_col, trailing + 1))
            self._append_close(lines)
            return '\n'.join(lines)

        if len(body) == 1 and not isinstance(body[0], ASTComment):
            inner_col = column + self.options.indent_size
            arg_str = self._render_node(body[0], inner_col, trailing + 1)
            return f'({head_str}\n' + ' ' * inner_col + arg_str + ')'

        first_col = column + 1 + len(head_str) + 1

        if isinstance(body[0], ASTComment):
            lines = [f'({head_str}']
            lines.extend(self._render_tail(body, first_col, trailing + 1))

        else:
            first_str = self._render_node(body[0], first_col)
            lines = [f'({head_str} {first_str}']
            lines.extend(self._render_tail(body[1:], first_col, trailing + 1))

        self._append_close(lines)
        return '\n'.join(lines)

    def _render_dict(self, head: ASTNode, body: list[ASTNode | ASTComment], column: int, trailing: int = 0) -> str:
        """Render a dict: each key/value pair on its own line, aligned under the first key."""
        head_str = self._render_node(head, column + 1)
        key_col = column + 1 + len(head_str) + 1

        if not body:
            return f'({head_str})'

        entries: list[str] = []
        pending: list[ASTNode] = []
        last_pair_end = self._last_pair_end(body)
        for elem in body:
            if isinstance(elem, ASTComment):
                if pending:
                    entries.append(self._render_pair(pending, key_col))
                    pending = []

                if elem.blank_before and entries:
                    entries.append('')

                entries.append(elem.text)
                continue

            pending.append(elem)
            if len(pending) == 2:
                reserve = trailing + 1 if elem is last_pair_end else 0
                entries.append(self._render_pair(pending, key_col, reserve))
                pending = []

        if pending:
            reserve = trailing + 1 if pending[-1] is last_pair_end else 0
            entries.append(self._render_pair(pending, key_col, reserve))

        if not entries:
            return f'({head_str})'

        lines = [f'({head_str} {entries[0]}']
        lines.extend(' ' * key_col + entry if entry else '' for entry in entries[1:])

        self._append_close(lines)
        return '\n'.join(lines)

    def _last_pair_end(self, body: list[ASTNode | ASTComment]) -> ASTNode | None:
        """Return the value of the last key/value pair in a dict body, or None if it does not end with one."""
        values = [elem for elem in body if not isinstance(elem, ASTComment)]
        if len(values) < 2:
            return None

        return values[-1]

    def _render_pair(self, pair: list[ASTNode], column: int, trailing: int = 0) -> str:
        """Render a key/value pair: the value's first line shares the key's line."""
        key_str = self._render_node(pair[0], column)
        if len(pair) < 2:
            return key_str

        value_col = column + len(key_str) + 1
        value_str = self._render_node(pair[1], value_col, trailing)
        return f'{key_str} {value_str}'

    def _render_tail(
        self,
        elements: list[ASTNode | ASTComment],
        column: int,
        trailing: int = 0,
        renderer: Callable[[ASTNode, int, int], str] | None = None,
    ) -> list[str]:
        """Render elements one per line at the given column, reserving trailing columns after the last."""
        render = renderer or self._render_node
        lines: list[str] = []
        last = self._last_renderable_index(elements)
        for index, elem in enumerate(elements):
            if elem.blank_before:
                lines.append('')

            if isinstance(elem, ASTComment):
                lines.append(' ' * column + elem.text)
                continue

            reserve = trailing if index == last else 0
            lines.append(' ' * column + render(elem, column, reserve))

        return lines

    def _last_renderable_index(self, elements: list[ASTNode | ASTComment]) -> int:
        """Return the index of the last non-comment element, or -1 if there is none."""
        for index in range(len(elements) - 1, -1, -1):
            if not isinstance(elements[index], ASTComment):
                return index

        return -1

    def _render_binding_form(self, name: str, body: list[ASTNode | ASTComment], column: int, trailing: int = 0) -> str:
        """Render let/let*/letrec: bindings then body, both indented by two."""
        if not body:
            return f'({name})'

        inner_col = column + self.options.indent_size

        leading: list[ASTComment] = []
        index = 0
        while index < len(body):
            candidate = body[index]
            if not isinstance(candidate, ASTComment):
                break

            leading.append(candidate)
            index += 1

        if index >= len(body):
            return self._render_general(ASTAtom(name, 0), body, column)

        bindings = body[index]
        if not isinstance(bindings, ASTList):
            return self._render_general(ASTAtom(name, 0), body, column)

        tail = body[index + 1:]

        binding_reserve = trailing if not tail else 0
        inline_col = column + 1 + len(name) + 1
        binding_lines = self._render_bindings(bindings, inline_col, binding_reserve)

        binding_elems = [e for e in bindings.elements if not isinstance(e, ASTComment)]
        single_binding = len(binding_elems) <= 1
        all_single_line = all(
            '\n' not in self._render_binding(e, inline_col + 1) for e in binding_elems
        )
        first_is_binding = bool(bindings.elements) and \
            not isinstance(bindings.elements[0], ASTComment)

        first_line = binding_lines[0].split('\n')[0]
        inline = not leading and first_is_binding and binding_elems and \
            (single_binding or all_single_line) and \
            inline_col + len(first_line) <= self.options.max_width

        if inline:
            lines = [f'({name} {binding_lines[0]}']
            lines.extend(binding_lines[1:])

        else:
            binding_lines = self._render_bindings(bindings, inner_col, binding_reserve)
            lines = [f'({name}']
            lines.extend(' ' * inner_col + comment.text for comment in leading)
            lines.append(' ' * inner_col + binding_lines[0])
            lines.extend(binding_lines[1:])

        if tail:
            lines.extend(self._render_tail(tail, inner_col, trailing + 1))

        self._append_close(lines)
        return '\n'.join(lines)

    def _render_bindings(self, bindings: ASTList, column: int, trailing: int = 0) -> list[str]:
        """Render a binding list: each binding aligned under the first."""
        if not bindings.elements:
            return ['()']

        binding_col = column + 1

        # Group each binding with the comments that precede it.
        groups: list[tuple[list[str], str, bool]] = []
        comments: list[str] = []
        blank = False
        last = self._last_renderable_index(bindings.elements)
        for index, elem in enumerate(bindings.elements):
            if isinstance(elem, ASTComment):
                blank = blank or elem.blank_before
                comments.append(elem.text)
                continue

            reserve = trailing + 1 if index == last else 0
            groups.append((comments, self._render_binding(elem, binding_col, reserve), blank or elem.blank_before))
            comments = []
            blank = False

        lines: list[str] = []
        prev_multiline = False
        for index, (group_comments, rendered, blank) in enumerate(groups):
            multiline = '\n' in rendered
            if index > 0 and (prev_multiline or multiline or blank):
                lines.append('')

            if index == 0:
                if group_comments:
                    lines.append('(' + group_comments[0])
                    lines.extend(' ' * binding_col + c for c in group_comments[1:])
                    lines.append(' ' * binding_col + rendered)

                else:
                    lines.append('(' + rendered)

            else:
                lines.extend(' ' * binding_col + c for c in group_comments)
                lines.append(' ' * binding_col + rendered)

            prev_multiline = multiline

        for comment in comments:
            lines.append(' ' * binding_col + comment)

        self._append_close(lines)
        return lines

    def _render_binding(self, binding: ASTNode, column: int, trailing: int = 0) -> str:
        """Render a single binding: (name value), value on the next line if long."""
        if not isinstance(binding, ASTList) or not binding.elements:
            return self._render_node(binding, column, trailing)

        name = binding.elements[0]
        rest = binding.elements[1:]

        if not isinstance(name, ASTAtom):
            return self._render_node(binding, column, trailing)

        name_col = column + 1
        value_col = name_col

        if not rest:
            return f'({name.value})'

        value = rest[0]
        if isinstance(value, ASTComment):
            return f'({name.value} {value.text})'

        inline_col = column + 1 + len(name.value) + 1
        compact = self._try_compact(value)
        lambda_value = self._is_lambda(value)

        if (
            compact is not None
            and not lambda_value
            and inline_col + len(compact) + 1 + trailing <= self.options.max_width
        ):
            return f'({name.value} {compact})'

        value_str = self._render_node(value, value_col, trailing + 1)
        return f'({name.value}\n' + ' ' * value_col + value_str + ')'

    def _is_lambda(self, node: ASTNode) -> bool:
        """Return whether a node is a lambda form."""
        if not isinstance(node, ASTList) or not node.elements:
            return False

        head = node.elements[0]
        return isinstance(head, ASTAtom) and head.value == 'lambda'

    def _render_lambda(self, body: list[ASTNode | ASTComment], column: int, trailing: int = 0) -> str:
        """Render a lambda: params on the head line, body indented by two."""
        inner_col = column + self.options.indent_size
        params_col = column + 1 + len('lambda') + 1

        if not body:
            return '(lambda)'

        if isinstance(body[0], ASTComment):
            lines = ['(lambda']
            lines.extend(self._render_tail(body, inner_col, trailing + 1))

        else:
            params_str = self._render_node(body[0], params_col)
            lines = [f'(lambda {params_str}']
            lines.extend(self._render_tail(body[1:], inner_col, trailing + 1))

        self._append_close(lines)
        return '\n'.join(lines)

    def _render_match(self, body: list[ASTNode | ASTComment], column: int, trailing: int = 0) -> str:
        """Render a match: scrutinee on the head line, arms indented by two."""
        inner_col = column + self.options.indent_size
        scrutinee_col = column + 1 + len('match') + 1

        if not body:
            return '(match)'

        if isinstance(body[0], ASTComment):
            lines = ['(match']
            lines.extend(self._render_tail(body, inner_col, trailing + 1))

        else:
            scrutinee_str = self._render_node(body[0], scrutinee_col)
            lines = [f'(match {scrutinee_str}']
            lines.extend(self._render_tail(body[1:], inner_col, trailing + 1, self._render_arm))

        self._append_close(lines)
        return '\n'.join(lines)

    def _render_arm(self, arm: ASTNode, column: int, trailing: int = 0) -> str:
        """Render a match arm: (pattern body), body on the next line if long."""
        if not isinstance(arm, ASTList) or len(arm.elements) < 2:
            return self._render_node(arm, column, trailing)

        pattern = arm.elements[0]
        body = arm.elements[1]
        if isinstance(body, ASTComment):
            return self._render_node(arm, column, trailing)

        pattern_str = self._render_node(pattern, column + 1)
        body_col = column + 1

        inline_col = column + 1 + len(pattern_str) + 1
        body_inline = self._render_node(body, inline_col, 1 + trailing)
        if '\n' not in body_inline:
            return f'({pattern_str} {body_inline})'

        body_str = self._render_node(body, body_col, trailing + 1)
        return f'({pattern_str}\n' + ' ' * body_col + body_str + ')'


class MenaiFormatter:
    """Format Menai source code."""

    def __init__(self, options: FormatOptions | None = None):
        self.options = options or FormatOptions()

    def format(self, source_code: str) -> str:
        """Format Menai source code."""
        lexer = MenaiLexer()
        tokens = lexer.lex(source_code, preserve_comments=True)
        tree = TreeBuilder(tokens).build()
        return Renderer(self.options).render(tree)
