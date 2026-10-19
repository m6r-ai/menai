"""
Menai Semantic Analyzer - validates AST structure and semantics.

This module performs semantic validation after parsing but before desugaring.

A form's *shape* (how many elements it has, and whether its sub-forms are the
right kind) is validated by the parser, not here: the parser knows each
element's role, and the analyzer never runs when the parse fails.  This module
checks only what needs the enclosing scope:

- Binding and parameter names (a name must be a symbol, must not be a reserved
  word, and must be unique within its binder)
- Namespace use (a namespace is second-class: it may only be bound and used as
  the first argument of ``::``)
- Call arity against the builtin registry, respecting lexical shadowing
- Duplicate keys in a dict literal
- Struct and enum definitions (field and variant names)

All validation happens in a single pass to provide clear, precise error messages
before any transformations occur.
"""

from typing import TypeGuard

from menai.ast.menai_ast import (
    MenaiASTBoolean,
    MenaiASTComplex,
    MenaiASTFloat,
    MenaiASTInteger,
    MenaiASTList,
    MenaiASTNode,
    MenaiASTString,
    MenaiASTSymbol,
)
from menai.menai_builtin_registry import MenaiBuiltinRegistry
from menai.menai_error import MenaiEvalError
from menai.ast.menai_ast import MenaiASTStruct, MenaiASTEnum
from menai.ast.menai_paren_diagnostic import ParenDiagnosis, early_close_error


# Names that are syntax rather than names.  A form headed by one of these is
# interpreted as that form, whatever any enclosing binding says, so a binding of
# the same name can never be referenced: the form head wins and the binding is
# silently unreachable.  Rejecting the binding outright turns a confusing
# misbehaviour into a compile-time error.
#
# ':' is the type-pattern head and '.' introduces a lambda rest parameter; both
# are syntax in a position where a name would otherwise go, so neither can be a
# binding either.  '.' is already rejected by the lambda parameter check, and is
# listed here so the set is complete.
RESERVED_NAMES = frozenset({
    '::', 'if', 'let', 'lambda', 'quote', 'match',
    'and', 'or', 'import', 'struct', 'enum', 'export', 'apply',
    ':', '.',
})


def _is_quoted_symbol(expr: MenaiASTNode) -> bool:
    """
    Return True if expr is a quoted symbol, written 'name.

    The reader produces a quoted symbol as the two-element form (quote name).
    The check is purely structural and does not verify that the name is bound.
    """
    if not isinstance(expr, MenaiASTList) or len(expr.elements) != 2:
        return False

    head, name_expr = expr.elements
    return (isinstance(head, MenaiASTSymbol) and head.name == 'quote'
            and isinstance(name_expr, MenaiASTSymbol))


class MenaiASTSemanticAnalyzer:
    """
    Validates Menai AST structure and semantics.

    This analyzer runs after parsing to check that all special forms
    are well-formed before any transformations (desugaring, compilation).
    """

    def __init__(self) -> None:
        """Initialize the semantic analyzer."""
        self.source = ""
        self.source_file = ""
        self._diagnosis: ParenDiagnosis | None = None
        self._next_struct_tag: int = 0
        self._next_enum_tag: int = 0

        # Lexical scope for ordinary variable bindings, innermost frame last.
        # Used to suppress builtin-specific validation (e.g. call arity checks
        # against the builtin registry) when the name is bound by an
        # enclosing binder and therefore refers to the user's binding.
        self._scope_stack: list[set[str]] = []

        # Names bound to a module namespace, innermost frame last.  A name here
        # is a namespace name: it may only be used as the head of a member
        # access (name member).  Namespaces are second-class, so a namespace
        # name may not be used as an ordinary value.
        self._namespace_stack: list[set[str]] = []

    def _push_scope(self, names: set[str]) -> None:
        """Push a lexical scope frame binding the given names."""
        self._scope_stack.append(names)

    def _pop_scope(self) -> None:
        """Pop the innermost lexical scope frame."""
        self._scope_stack.pop()

    def _is_shadowed(self, name: str) -> bool:
        """Return True if name is bound by any enclosing lexical scope frame."""
        return any(name in frame for frame in self._scope_stack)

    def _push_namespace_scope(self, names: set[str]) -> None:
        """Push a lexical scope frame binding the given namespace names."""
        self._namespace_stack.append(names)

    def _pop_namespace_scope(self) -> None:
        """Pop the innermost namespace scope frame."""
        self._namespace_stack.pop()

    def _is_namespace(self, name: str) -> bool:
        """Return True if name is bound to a module namespace in scope."""
        return any(name in frame for frame in self._namespace_stack)

    def _reject_reserved_binding_name(self, name: str, node: MenaiASTNode) -> None:
        """
        Raise when name is a reserved word and so cannot be a binding name.

        A reserved word is syntax, not a name: a form headed by one is
        interpreted as that form regardless of any enclosing binding, so a
        binding of the same name is unreachable and the program does not mean
        what it appears to mean.  Rejecting it here, before desugaring, is the
        only point at which every binding site can be covered with one message.

        Args:
            name: The proposed binding name.
            node: The node naming it, used for the error position.

        Raises:
            MenaiEvalError: If name is a reserved word.
        """
        if name not in RESERVED_NAMES:
            return

        raise MenaiEvalError(
            message=f"Binding name '{name}' is a reserved word",
            received=f"'{name}' used as a binding name",
            expected="A name that is not a reserved word",
            suggestion=(
                f"'{name}' is syntax, not a name: a form headed by it is always "
                f"interpreted as that form, so a binding of the same name could never "
                f"be referenced. Choose a different binding name."
            ),
            line=node.line,
            column=node.column,
            source=self.source,
        )

    def analyze(
        self,
        expr: MenaiASTNode,
        source: str = "",
        source_file: str = "",
        diagnosis: ParenDiagnosis | None = None,
    ) -> MenaiASTNode:
        """
        Analyze an expression recursively, validating all special forms.

        Args:
            expr: AST to analyze
            source: Original source code (for error reporting with line/column)
            source_file: Source file name for location display
            diagnosis: Parenthesis diagnosis for the token stream.  When a
                structural check fails, it identifies a ')' that closed an
                enclosing form before its body, which is the misplaced ')' the
                reader has to change.

        Returns:
            The same AST (unmodified) if validation passes

        Raises:
            MenaiEvalError: If validation fails with detailed error message
        """
        # Store source for error reporting
        self.source = source
        self.source_file = source_file

        # The diagnosis is set by the outermost call and kept for the whole
        # traversal.  Recursive calls pass no diagnosis, so they must not clear
        # it: a nested form's structural error still needs the diagnosis to tell
        # whether a misplaced ')' caused it.
        if diagnosis is not None:
            self._diagnosis = diagnosis

        # Lists need inspection
        if isinstance(expr, MenaiASTList):
            return self._analyze_list(expr)

        # A bare namespace name is a namespace used as a value, which is not
        # allowed: namespaces are second-class.  (A namespace name in member
        # access position is the head of a list, handled above.)
        if isinstance(expr, MenaiASTSymbol):
            self._reject_namespace_as_value(expr, "a value position")

        # Self-evaluating values need no validation
        return expr

    def _raise_if_early_close(self, expr: MenaiASTNode, form_name: str) -> None:
        """
        Raise an early-close error when a structural check failed because of a
        misplaced ')'.

        A form with the wrong number of elements is often the symptom of a ')'
        that closed an enclosing form before its body.  When the diagnosis
        identifies such a close, it is raised here so the reader is sent to the
        ')' to change rather than to the form the analyzer happened to flag.

        Args:
            expr: The form whose structural check failed.  Only its position is
                used, so any node will do.
            form_name: The name of the form whose structural check failed.

        Raises:
            MenaiEvalError: When the diagnosis shows an enclosing form closed
                before its body.
        """
        if self._diagnosis is None:
            return

        if expr.line is None or expr.column is None:
            return

        error = early_close_error(
            self._diagnosis, MenaiEvalError, self.source, self.source_file,
            form_name, expr.line, expr.column,
        )
        if error is not None:
            raise error

    def _analyze_list(self, expr: MenaiASTList) -> MenaiASTList:
        """Analyze a list expression (special form or function call)."""
        if expr.is_empty():
            # Empty list is valid (though semantically meaningless)
            return expr

        first = expr.first()

        # Check for special forms
        if isinstance(first, MenaiASTSymbol):
            name = first.name

            # Member access: (:: namespace member).  The form head decides the
            # meaning, so a namespace name is never mistaken for a special form
            # or builtin call.
            if name == '::':
                return self._analyze_namespace_access(expr)

            if name == 'if':
                return self._analyze_if(expr)

            if name == 'let':
                return self._analyze_letrec(expr)

            if name == 'lambda':
                return self._analyze_lambda(expr)

            if name == 'quote':
                return self._analyze_quote(expr)

            if name == 'match':
                return self._analyze_match(expr)

            if name == 'and':
                return self._analyze_and(expr)

            if name == 'or':
                return self._analyze_or(expr)

            if name == 'import':
                # import is only valid as the value of a let
                # binding.  The binding forms validate it directly, so
                # reaching here means it is used in any other position.
                return self._reject_import_outside_binding(expr)

            if name == 'struct':
                return self._reject_struct_outside_let(expr)

            if name == 'enum':
                return self._reject_enum_outside_let(expr)

            if name == 'export':
                return self._analyze_export(expr)

            if name == 'apply':
                return self._analyze_apply(expr)

        # Regular function call - recursively analyze all subexpressions
        return self._analyze_call(expr)

    def _analyze_if(self, expr: MenaiASTList) -> MenaiASTList:
        """Analyse the subexpressions of an if form."""
        _, condition, then_expr, else_expr = expr.elements

        self.analyze(condition, self.source)
        self.analyze(then_expr, self.source)
        self.analyze(else_expr, self.source)

        return expr

    def _analyze_let(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate let expression: (let ((var val) ...) body)"""
        if len(expr.elements) < 3:
            self._raise_if_early_close(expr, "let")
            raise MenaiEvalError(
                message="Let expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (let ((bindings...)) body)",
                suggestion="Let needs binding list and body: (let ((var1 val1) (var2 val2) ...) body)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # _parse_let_with_tracking hard-caps 'let' at 3 elements (keyword +
        # bindings + body) before consuming ')' — no loop after body.
        assert len(expr.elements) <= 3, (
            f"Parser invariant violated: 'let' produced {len(expr.elements)} elements "
            f"(expected ≤ 3); _parse_let_with_tracking should cap at 3"
        )

        _, bindings_list, body = expr.elements
        if not isinstance(bindings_list, MenaiASTList):
            self._raise_if_early_close(expr, "let")
            raise MenaiEvalError(
                message="Let binding list must be a list",
                received=f"Binding list: {bindings_list.type_name()}",
                expected="List of bindings: ((var1 val1) (var2 val2) ...)",
                suggestion="Wrap bindings in parentheses: ((var val) (var val) ...)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Walk each binding: analyse its value and collect its name.
        var_names: list[str] = []
        namespace_names: set[str] = set()
        new_bindings: list[MenaiASTNode] = []
        for binding in bindings_list.elements:
            # The parser produces each binding as a two-element (name value)
            # list, so the shape is already guaranteed here.
            assert isinstance(binding, MenaiASTList)
            name_expr, value_expr = binding.elements
            # The parser rejects a binding whose name is not a symbol, so the
            # name is always a symbol here.
            assert isinstance(name_expr, MenaiASTSymbol)
            self._reject_reserved_binding_name(name_expr.name, name_expr)

            # Check if this binding's value is a struct definition
            if (isinstance(value_expr, MenaiASTList) and
                    not value_expr.is_empty() and
                    isinstance(value_expr.elements[0], MenaiASTSymbol) and
                    value_expr.elements[0].name == 'struct'):
                struct_node = self._analyze_struct(value_expr, name_expr.name)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, struct_node),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            # Check if this binding's value is an enum definition
            if (isinstance(value_expr, MenaiASTList) and
                    not value_expr.is_empty() and
                    isinstance(value_expr.elements[0], MenaiASTSymbol) and
                    value_expr.elements[0].name == 'enum'):
                enum_node = self._analyze_enum(value_expr, name_expr.name)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, enum_node),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            # A binding whose value is an import binds a namespace name.
            if self._is_import_expr(value_expr):
                self._analyze_import(value_expr)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, value_expr),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                namespace_names.add(name_expr.name)
                continue

            var_names.append(name_expr.name)

            # Recursively analyze the value expression
            analyzed_value = self.analyze(value_expr, self.source)
            new_bindings.append(MenaiASTList(
                elements=(name_expr, analyzed_value),
                line=binding.line, column=binding.column, source_file=binding.source_file
            ))

        # Check for duplicate binding names
        if len(var_names) != len(set(var_names)):
            duplicates = [name for name in var_names if var_names.count(name) > 1]
            raise MenaiEvalError(
                message="Let binding variables must be unique",
                received=f"Duplicate variables: {duplicates}",
                expected="All variable names should be different",
                suggestion="Use different names for each variable",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Analyze body
        # let is parallel: binding values were analyzed above in the enclosing
        # scope; only the body sees the binding names.
        self._push_scope(set(var_names))
        self._push_namespace_scope(namespace_names)
        try:
            analyzed_body = self.analyze(body, self.source)

        finally:
            self._pop_namespace_scope()
            self._pop_scope()

        new_bindings_list = MenaiASTList(
            elements=tuple(new_bindings),
            line=bindings_list.line, column=bindings_list.column,
            source_file=bindings_list.source_file
        )
        return MenaiASTList(
            elements=(expr.elements[0], new_bindings_list, analyzed_body),
            line=expr.line, column=expr.column, source_file=expr.source_file
        )

    def _analyze_letrec(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate let expression: (let ((var val) ...) body)"""
        if len(expr.elements) < 3:
            self._raise_if_early_close(expr, "let")
            raise MenaiEvalError(
                message="Let expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (let ((bindings...)) body)",
                suggestion="Let needs binding list and body: (let ((var1 val1) (var2 val2) ...) body)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # _parse_let_with_tracking hard-caps 'let' at exactly 3 elements
        # (keyword + bindings + body) before consuming ')' — no loop after body.
        assert len(expr.elements) <= 3, (
            f"Parser invariant violated: 'let' produced {len(expr.elements)} elements "
            f"(expected ≤ 3); _parse_let_with_tracking should cap at 3"
        )

        _, bindings_list, body = expr.elements
        if not isinstance(bindings_list, MenaiASTList):
            self._raise_if_early_close(expr, "let")
            raise MenaiEvalError(
                message="Let binding list must be a list",
                received=f"Binding list: {bindings_list.type_name()}",
                expected="List of bindings: ((var1 val1) (var2 val2) ...)",
                suggestion="Wrap bindings in parentheses: ((var val) (var val) ...)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Walk each binding: analyse its value and collect its name.
        var_names: list[str] = []
        new_bindings: list[MenaiASTNode] = []

        # Every let binding is visible to every sibling value and to the body,
        # so all names are in scope for the whole form.
        letrec_names = {
            binding.elements[0].name for binding in bindings_list.elements
            if isinstance(binding, MenaiASTList)
            and len(binding.elements) == 2
            and isinstance(binding.elements[0], MenaiASTSymbol)
        }
        self._push_scope(letrec_names)
        letrec_namespaces = {
            binding.elements[0].name for binding in bindings_list.elements
            if isinstance(binding, MenaiASTList)
            and len(binding.elements) == 2
            and isinstance(binding.elements[0], MenaiASTSymbol)
            and self._is_import_expr(binding.elements[1])
        }
        self._push_namespace_scope(letrec_namespaces)
        for binding in bindings_list.elements:
            # The parser produces each binding as a two-element (name value)
            # list, so the shape is already guaranteed here.
            assert isinstance(binding, MenaiASTList)
            name_expr, value_expr = binding.elements
            # The parser rejects a binding whose name is not a symbol, so the
            # name is always a symbol here.
            assert isinstance(name_expr, MenaiASTSymbol)
            self._reject_reserved_binding_name(name_expr.name, name_expr)

            # Struct definitions are permitted in let: they have no recursive
            # semantics and the desugarer hoists them to let automatically.
            if (isinstance(value_expr, MenaiASTList) and
                    not value_expr.is_empty() and
                    isinstance(value_expr.elements[0], MenaiASTSymbol) and
                    value_expr.elements[0].name == 'struct'):
                struct_node = self._analyze_struct(value_expr, name_expr.name)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, struct_node),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            # Enum definitions are permitted in let for the same reason as
            # struct definitions: they have no recursive semantics and the
            # desugarer hoists them to let automatically.
            if (isinstance(value_expr, MenaiASTList) and
                    not value_expr.is_empty() and
                    isinstance(value_expr.elements[0], MenaiASTSymbol) and
                    value_expr.elements[0].name == 'enum'):
                enum_node = self._analyze_enum(value_expr, name_expr.name)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, enum_node),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            # A binding whose value is an import binds a namespace name.
            if self._is_import_expr(value_expr):
                self._analyze_import(value_expr)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, value_expr),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            var_names.append(name_expr.name)

            # Recursively analyze the value expression
            analyzed_value = self.analyze(value_expr, self.source)
            new_bindings.append(MenaiASTList(
                elements=(name_expr, analyzed_value),
                line=binding.line, column=binding.column, source_file=binding.source_file
            ))

        # Check for duplicate binding names
        try:
            if len(var_names) != len(set(var_names)):
                duplicates = [name for name in var_names if var_names.count(name) > 1]
                raise MenaiEvalError(
                    message="Let binding variables must be unique",
                    received=f"Duplicate variables: {duplicates}",
                    expected="All variable names should be different",
                    suggestion="Use different names for each variable",
                    line=expr.line,
                    column=expr.column,
                    source=self.source
                )

            # Analyze body
            analyzed_body = self.analyze(body, self.source)

        finally:
            self._pop_namespace_scope()
            self._pop_scope()

        new_bindings_list = MenaiASTList(
            elements=tuple(new_bindings),
            line=bindings_list.line, column=bindings_list.column,
            source_file=bindings_list.source_file
        )
        return MenaiASTList(
            elements=(expr.elements[0], new_bindings_list, analyzed_body),
            line=expr.line, column=expr.column, source_file=expr.source_file
        )

    def _analyze_lambda(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate lambda expression: (lambda (params...) body)"""
        _, params_list, body = expr.elements

        if not isinstance(params_list, MenaiASTList):
            self._raise_if_early_close(expr, "lambda")
            raise MenaiEvalError(
                message="Lambda parameters must be a list",
                received=f"Parameter list: {params_list.type_name()}",
                expected="List of symbols: (param1 param2 ...)",
                example="(lambda (x y z) (+ x y z))",
                suggestion="Parameters should be unquoted variable names",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Validate each parameter, allowing a single dot to introduce a rest parameter.
        # Valid forms:
        #   (lambda (a b) ...)          — fixed arity
        #   (lambda (a b . rest) ...)   — fixed prefix + rest
        #   (lambda (. rest) ...)       — pure variadic (dot as first element)
        param_names: list[str] = []
        elements = params_list.elements
        dot_index: int | None = None

        for i, param in enumerate(elements):
            if not isinstance(param, MenaiASTSymbol):
                self._raise_if_early_close(expr, "lambda")
                raise MenaiEvalError(
                    message=f"Lambda parameter {i+1} must be a symbol",
                    received=f"Parameter {i+1}: {param.type_name()}",
                    expected="Unquoted symbol (variable name)",
                    suggestion='Use unquoted names: x, not \"x\" or 1',
                    line=param.line,
                    column=param.column,
                    source=self.source
                )

            if param.name == '.':
                if dot_index is not None:
                    self._raise_if_early_close(expr, "lambda")
                    raise MenaiEvalError(
                        message="Lambda parameter list has more than one dot",
                        received=f"Second dot at parameter position {i+1}",
                        expected="At most one dot to introduce a rest parameter",
                        example="(lambda (a b . rest) body)",
                        suggestion="Use a single dot followed by one rest parameter name",
                        line=param.line,
                        column=param.column,
                        source=self.source
                    )

                dot_index = i

            else:
                self._reject_reserved_binding_name(param.name, param)
                param_names.append(param.name)

        if dot_index is not None:
            # Dot must be second-to-last: exactly one symbol must follow it
            if dot_index != len(elements) - 2:
                self._raise_if_early_close(expr, "lambda")
                raise MenaiEvalError(
                    message="Rest parameter must be the last element after the dot",
                    received=f"Dot at position {dot_index+1} with {len(elements) - dot_index - 1} element(s) after it",
                    expected="Exactly one symbol after the dot",
                    example="(lambda (a b . rest) body)",
                    suggestion="Place the dot second-to-last and the rest parameter name last",
                    line=params_list.line,
                    column=params_list.column,
                    source=self.source
                )
            # The rest param name was already appended to param_names (dot itself was skipped)

        # Check for duplicate parameters
        if len(param_names) != len(set(param_names)):
            duplicates = [p for p in param_names if param_names.count(p) > 1]
            self._raise_if_early_close(expr, "lambda")
            raise MenaiEvalError(
                message="Lambda parameters must be unique",
                received=f"Duplicate parameters: {duplicates}",
                expected="All parameter names should be different",
                suggestion="Use different names for each parameter",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Analyze body
        self._push_scope(set(param_names))
        try:
            self.analyze(body, self.source)

        finally:
            self._pop_scope()

        return expr

    def _analyze_quote(self, expr: MenaiASTList) -> MenaiASTList:
        """Return a quote form unchanged; its argument is data, not code."""
        # Note: We don't recursively analyze the quoted expression
        # because it's data, not code to be evaluated
        return expr

    def _analyze_match(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate match expression: (match value (pattern result) ...)"""
        value_expr = expr.elements[1]
        clauses = list(expr.elements[2:])

        # Analyze the value expression
        self.analyze(value_expr, self.source)

        # Walk each clause: analyse its pattern and result.
        for clause in clauses:
            # The parser produces each clause as a two-element (pattern result)
            # form, so the shape is already guaranteed here.
            assert isinstance(clause, MenaiASTList)
            pattern, result_expr = clause.elements

            # Validate the pattern
            self._analyze_match_pattern(pattern)

            # The result expression sees every name the pattern binds.
            self._push_scope(self._collect_pattern_names(pattern))
            try:
                self.analyze(result_expr, self.source)

            finally:
                self._pop_scope()

        return expr

    def _collect_pattern_names(self, pattern: MenaiASTNode) -> set[str]:
        """Return the set of variable names a match pattern binds."""
        if isinstance(pattern, MenaiASTSymbol):
            if pattern.name == '_':
                return set()

            self._reject_reserved_binding_name(pattern.name, pattern)
            return {pattern.name}

        if not isinstance(pattern, MenaiASTList) or pattern.is_empty():
            return set()

        # Predicate pattern (? pred var) binds only var.
        if (isinstance(pattern.elements[0], MenaiASTSymbol)
                and pattern.elements[0].name == '?'
                and len(pattern.elements) == 3):
            var_pattern = pattern.elements[2]
            if isinstance(var_pattern, MenaiASTSymbol) and var_pattern.name != '_':
                self._reject_reserved_binding_name(var_pattern.name, var_pattern)
                return {var_pattern.name}

            return set()

        # Type pattern (: TypeName ...).  The head and the type name bind
        # nothing.  A struct pattern's field patterns bind; an enum pattern's
        # variant is a quoted symbol, which binds nothing, so skipping quoted
        # symbols covers both without needing to know the type's kind.
        if (isinstance(pattern.elements[0], MenaiASTSymbol)
                and pattern.elements[0].name == ':'
                and len(pattern.elements) >= 2):
            type_pattern_names: set[str] = set()
            for elem in pattern.elements[2:]:
                if _is_quoted_symbol(elem):
                    continue

                type_pattern_names |= self._collect_pattern_names(elem)

            return type_pattern_names

        names: set[str] = set()
        for elem in pattern.elements:
            if isinstance(elem, MenaiASTSymbol) and elem.name == '.':
                continue

            # A quoted symbol is written 'name, which the reader produces as the
            # two-element form (quote name).  It binds nothing, so it must not be
            # walked as a list pattern: doing so would read the literal head
            # 'quote' as a binding name.
            if _is_quoted_symbol(elem):
                continue

            names |= self._collect_pattern_names(elem)

        return names

    def _analyze_match_pattern(self, pattern: MenaiASTNode) -> None:
        """
        Analyse the expressions embedded in a match pattern.

        The pattern's shape is validated when it is parsed, so this walk only
        analyses the predicate expression of a predicate pattern and recurses
        into sub-patterns.  A quoted symbol (an enum variant) binds nothing and
        is not a sub-pattern.

        Args:
            pattern: Pattern to walk.
        """
        if not isinstance(pattern, MenaiASTList):
            return

        if pattern.is_empty():
            return

        if (len(pattern.elements) >= 1 and
            isinstance(pattern.elements[0], MenaiASTSymbol) and
            pattern.elements[0].name == '?'):
            # Analyze the predicate expression — can be any valid expression
            self.analyze(pattern.elements[1], self.source)
            return

        if (len(pattern.elements) >= 1 and
            isinstance(pattern.elements[0], MenaiASTSymbol) and
            pattern.elements[0].name == ':'):
            for elem in pattern.elements[2:]:
                if _is_quoted_symbol(elem):
                    continue

                self._analyze_match_pattern(elem)

            return

        for elem_pattern in pattern.elements:
            self._analyze_match_pattern(elem_pattern)

    def _analyze_and(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate and expression: (and arg1 arg2 ...)"""
        # 'and' can have any number of arguments (including zero)
        # Just recursively analyze all arguments
        for arg in expr.elements[1:]:
            self.analyze(arg, self.source)

        return expr

    def _analyze_or(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate or expression: (or arg1 arg2 ...)"""
        # 'or' can have any number of arguments (including zero)
        # Just recursively analyze all arguments
        for arg in expr.elements[1:]:
            self.analyze(arg, self.source)

        return expr

    def _analyze_namespace_access(self, expr: MenaiASTList) -> MenaiASTList:
        """
        Validate member access on a namespace: (:: namespace member).

        The first argument must be a namespace in scope and the second the
        member symbol to resolve.  The member is validated for shape only;
        whether it names an actual export is checked by the module resolver,
        which has the module's export map.
        """
        namespace_expr = expr.elements[1]
        if not isinstance(namespace_expr, MenaiASTSymbol):
            raise MenaiEvalError(
                message="Namespace member access requires a namespace name",
                received=f"Namespace: {namespace_expr.type_name()}",
                expected="Unquoted namespace name",
                example="(:: shapes point-distance) not (:: \"shapes\" point-distance)",
                suggestion="Use the name of a namespace bound by an import",
                line=namespace_expr.line,
                column=namespace_expr.column,
                source=self.source
            )

        if not self._is_namespace(namespace_expr.name):
            raise MenaiEvalError(
                message=f"'{namespace_expr.name}' is not a namespace",
                received=f"Name: {namespace_expr.name}",
                expected="The name of a namespace bound by an import",
                example='(let ((shapes (import "shapes"))) (:: shapes point-distance))',
                suggestion="Bind the import to a name and access its members",
                line=namespace_expr.line,
                column=namespace_expr.column,
                source=self.source
            )

        member_expr = expr.elements[2]
        if not isinstance(member_expr, MenaiASTSymbol):
            raise MenaiEvalError(
                message="Namespace member must be a symbol",
                received=f"Member: {member_expr.type_name()}",
                expected="Unquoted member name",
                example="(:: shapes point-distance) not (:: shapes \"point-distance\")",
                suggestion="Use an unquoted name to access a namespace member",
                line=member_expr.line,
                column=member_expr.column,
                source=self.source
            )

        return expr

    @staticmethod
    def _is_import_expr(expr: MenaiASTNode) -> TypeGuard[MenaiASTList]:
        """Return True if expr is an (import "name") expression."""
        if not (isinstance(expr, MenaiASTList) and not expr.is_empty()):
            return False

        head = expr.first()
        return isinstance(head, MenaiASTSymbol) and head.name == 'import'

    def _reject_namespace_as_value(self, node: MenaiASTNode, context: str) -> None:
        """
        Reject a namespace name used anywhere other than the first argument of ::.

        Namespaces are second-class: a namespace name may only be bound and
        then accessed through member access.  Using it as an ordinary value
        (passing it, storing it, returning it) is an error.
        """
        if isinstance(node, MenaiASTSymbol) and self._is_namespace(node.name):
            raise MenaiEvalError(
                message=f"Namespace '{node.name}' cannot be used as a value",
                received=f"Namespace '{node.name}' used in {context}",
                expected="Member access on the namespace: (:: namespace member)",
                example=f"(:: {node.name} some-member)",
                suggestion="Namespaces are second-class; access a member instead of the namespace itself",
                line=node.line,
                column=node.column,
                source=self.source
            )

    def _reject_namespace_as_function(self, expr: MenaiASTList, name: str) -> None:
        """Reject a namespace name used as a call head."""
        raise MenaiEvalError(
            message=f"Namespace '{name}' cannot be used as a function",
            received=f"Namespace '{name}' used as a call head: {expr.describe()}",
            expected=f"Member access on the namespace: (:: {name} member)",
            example=f"(:: {name} some-member)",
            suggestion="Namespaces are second-class; access a member instead of calling the namespace",
            line=expr.line,
            column=expr.column,
            source=self.source
        )

    def _reject_import_outside_binding(self, expr: MenaiASTList) -> MenaiASTList:
        """Reject (import ...) used anywhere other than a let binding value."""
        raise MenaiEvalError(
            message="import is only valid as a binding value",
            received=f"import used in: {expr.describe()}",
            expected="(let ((name (import \"module-name\"))) body)",
            example='(let ((shapes (import "shapes"))) (:: shapes point-distance))',
            suggestion="Bind the import to a name and access its members",
            line=expr.line,
            column=expr.column,
            source=self.source
        )

    def _analyze_export(self, expr: MenaiASTList) -> MenaiASTList:
        """
        Validate an export form: (export name ...).

        Each argument must be a symbol naming a binding in the module.  The
        form is only meaningful as the final form of a module body; the module
        resolver enforces that placement.
        """
        for i, name_expr in enumerate(expr.elements[1:]):
            if not isinstance(name_expr, MenaiASTSymbol):
                raise MenaiEvalError(
                    message=f"Export name {i+1} must be a symbol",
                    received=f"Got {name_expr.type_name()}",
                    expected="Unquoted binding name",
                    example="(export point make-point)",
                    suggestion="Export names must be unquoted symbols naming module bindings",
                    line=name_expr.line,
                    column=name_expr.column,
                    source=self.source
                )

        return expr

    def _analyze_import(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate import expression: (import "module-name")"""
        _, module_name_expr = expr.elements

        if not isinstance(module_name_expr, MenaiASTString):
            raise MenaiEvalError(
                message="Import module name must be a string literal",
                received=f"Module name: {module_name_expr.type_name()}",
                expected="String literal with module name",
                example='(import "calendar") not (import calendar)',
                suggestion="Module names must be string literals in double quotes",
                line=module_name_expr.line,
                column=module_name_expr.column,
                source=self.source
            )

        # Validate module name is not empty
        if not module_name_expr.value:
            raise MenaiEvalError(
                message="Import module name cannot be empty",
                example='(import "calendar")',
                suggestion="Provide a valid module name",
                line=module_name_expr.line,
                column=module_name_expr.column,
                source=self.source
            )

        return expr

    def _analyze_apply(self, expr: MenaiASTList) -> MenaiASTList:
        """Analyse the subexpressions of an apply form."""
        _, func_expr, args_expr = expr.elements

        self.analyze(func_expr, self.source)
        self.analyze(args_expr, self.source)

        return expr

    def _analyze_call(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate function call: check builtin arity, then recurse into subexpressions."""
        first = expr.first()
        if isinstance(first, MenaiASTSymbol):
            name = first.name

            # A namespace name is not callable: member access is the only way
            # to reach a namespace's members, and it is written (:: ns member).
            if self._is_namespace(name):
                self._reject_namespace_as_function(expr, name)

            # $-prefixed names are opcode-backed primitives written explicitly
            # (e.g. inside prelude bodies or emitted by the desugarer).
            # Validate that the base name is known and that the call supplies
            # exactly the opcode's arity.  User-written $-calls must always be
            # fully saturated — there is no optional-argument handling for the
            # primitive form, so the only valid arity is the exact primitive
            # arity from the builtin registry.
            if name.startswith('$'):
                base = name[1:]
                if not MenaiBuiltinRegistry.is_primitive_name(base):
                    raise MenaiEvalError(
                        message=f"Unknown primitive '{name}'",
                        received=f"'{name}' is not a known opcode-backed primitive",
                        expected="A valid $-prefixed primitive name such as '$integer+'",
                        line=expr.line,
                        column=expr.column,
                        source=self.source
                    )

                primitive_arity = MenaiBuiltinRegistry.get_primitive_arity(base)
                n_args = len(expr.elements) - 1
                if n_args != primitive_arity:
                    raise MenaiEvalError(
                        message=f"Primitive '{name}' called with wrong number of arguments",
                        received=f"Got {n_args} argument{'s' if n_args != 1 else ''}",
                        expected=f"Exactly {primitive_arity} argument{'s' if primitive_arity != 1 else ''}",
                        line=expr.line,
                        column=expr.column,
                        source=self.source
                    )

                for elem in expr.elements[1:]:
                    self.analyze(elem, self.source)

                return expr

            arity = MenaiBuiltinRegistry.get_function_arity(name)

            # A name bound by an enclosing lexical binding shadows the builtin,
            # so its call arity must not be validated against the builtin table.
            if arity is not None and not self._is_shadowed(name):
                min_args, max_args = arity
                n_args = len(expr.elements) - 1

                if n_args < min_args:
                    if min_args == max_args:
                        expected_str = f"Exactly {min_args}"

                    else:
                        expected_str = f"At least {min_args}"

                    raise MenaiEvalError(
                        message=f"Function '{name}' has wrong number of arguments",
                        received=f"Got {n_args} argument{'s' if n_args != 1 else ''}",
                        expected=f"{expected_str} argument{'s' if min_args != 1 else ''}",
                        line=expr.line,
                        column=expr.column,
                        source=self.source
                    )

                if max_args is not None and n_args > max_args:
                    if min_args == max_args:
                        expected_str = f"Exactly {max_args}"

                    else:
                        expected_str = f"At most {max_args}"

                    raise MenaiEvalError(
                        message=f"Function '{name}' has wrong number of arguments",
                        received=f"Got {n_args} argument{'s' if n_args != 1 else ''}",
                        expected=f"{expected_str} argument{'s' if max_args != 1 else ''}",
                        line=expr.line,
                        column=expr.column,
                        source=self.source
                    )

        if isinstance(first, MenaiASTSymbol) and first.name == 'dict' and not self._is_shadowed('dict'):
            n_args = len(expr.elements) - 1
            if n_args % 2 != 0:
                raise MenaiEvalError(
                    message="Function 'dict' requires an even number of arguments",
                    received=f"Got {n_args} argument{'s' if n_args != 1 else ''}",
                    expected="An even number of arguments: (dict k1 v1 k2 v2 ...)",
                    example='(dict "name" "Alice" "age" 30)',
                    suggestion="Each key must be followed by its value",
                    line=expr.line,
                    column=expr.column,
                    source=self.source
                )

            self._check_duplicate_dict_keys(expr)

        # Recursively analyze all elements (function and arguments)
        for elem in expr.elements:
            self.analyze(elem, self.source)

        return expr

    def _check_duplicate_dict_keys(self, expr: MenaiASTList) -> None:
        """
        Reject a (dict ...) literal with duplicate constant keys.

        A dict cannot contain duplicate keys.  This is caught at compile time
        when both keys are constant literals.  Keys that are not constant
        literals cannot be compared statically; duplicates among them are
        collapsed at runtime (last value wins).
        """
        seen: set[tuple[str, object]] = set()
        elements = expr.elements[1:]
        for i in range(0, len(elements), 2):
            key = elements[i]
            key_id = _constant_key_id(key)
            if key_id is None:
                continue

            if key_id in seen:
                raise MenaiEvalError(
                    message="Duplicate key in dict literal",
                    received=f"Key {key.describe()} appears more than once",
                    expected="Each key in a dict literal must be unique",
                    example='(dict "name" "Alice" "age" 30)',
                    suggestion="Remove or rename the duplicate key",
                    line=expr.line,
                    column=expr.column,
                    source=self.source
                )

            seen.add(key_id)

    def _analyze_struct(self, expr: MenaiASTList, binding_name: str) -> MenaiASTStruct:
        """
        Validate and transform a (struct (field ...)) form into a MenaiASTStruct node.

        Called only when (struct ...) appears as the RHS of a let binding.
        Assigns a fresh compile-time tag and extracts the field names.

        Args:
            expr: The raw (struct (field ...)) AST list node
            binding_name: The name of the enclosing let binding (becomes the type name)

        Returns:
            A MenaiASTStruct node with name, tag, and field_names populated
        """
        _, fields_expr = expr.elements

        if not isinstance(fields_expr, MenaiASTList):
            self._raise_if_early_close(expr, "struct")
            raise MenaiEvalError(
                message="Struct field list must be a list",
                received=f"Got {fields_expr.type_name()}",
                expected="A list of field name symbols: (field1 field2 ...)",
                example="(let ((Point (struct (x y)))) ...)",
                suggestion="Wrap field names in parentheses: (struct (field1 field2 ...))",
                line=fields_expr.line,
                column=fields_expr.column,
                source=self.source
            )

        field_names: list[str] = []
        for i, field in enumerate(fields_expr.elements):
            if not isinstance(field, MenaiASTSymbol):
                self._raise_if_early_close(expr, "struct")
                raise MenaiEvalError(
                    message=f"Struct field {i+1} must be a symbol",
                    received=f"Got {field.type_name()}",
                    expected="Unquoted symbol (field name)",
                    example="(struct (x y)) not (struct (\"x\" \"y\"))",
                    suggestion="Use unquoted names for struct fields",
                    line=field.line,
                    column=field.column,
                    source=self.source
                )

            if field.name in field_names:
                raise MenaiEvalError(
                    message=f"Struct field name '{field.name}' is duplicated",
                    received=f"Field '{field.name}' appears more than once",
                    expected="All field names must be unique",
                    example="(struct (x y)) not (struct (x x))",
                    suggestion="Use distinct names for each field",
                    line=field.line,
                    column=field.column,
                    source=self.source
                )

            field_names.append(field.name)

        tag = self._next_struct_tag
        self._next_struct_tag += 1

        return MenaiASTStruct(
            name=binding_name,
            tag=tag,
            field_names=tuple(field_names),
            line=expr.line,
            column=expr.column,
            source_file=expr.source_file
        )

    def _reject_struct_outside_let(self, expr: MenaiASTList) -> MenaiASTList:
        """Reject (struct ...) used outside a let binding position."""
        raise MenaiEvalError(
            message="Struct definition must be the value in a let binding",
            received="(struct ...) used outside a let binding",
            expected="(let ((TypeName (struct (field1 field2 ...)))) ...)",
            example="(let ((Point (struct (x y)))) (Point 1 2))",
            suggestion="Wrap struct definitions in a let binding",
            line=expr.line,
            column=expr.column,
            source=self.source
        )

    def _analyze_enum(self, expr: MenaiASTList, binding_name: str) -> MenaiASTEnum:
        """
        Validate and transform an (enum (variant ...)) form into a MenaiASTEnum node.

        Called only when (enum ...) appears as the RHS of a let
        binding.  Assigns a fresh compile-time tag and extracts the variant names.

        Args:
            expr: The raw (enum (variant ...)) AST list node
            binding_name: The name of the enclosing binding (becomes the type name)

        Returns:
            A MenaiASTEnum node with name, tag, and variant_names populated
        """
        _, variants_expr = expr.elements

        if not isinstance(variants_expr, MenaiASTList):
            self._raise_if_early_close(expr, "enum")
            raise MenaiEvalError(
                message="Enum variant list must be a list",
                received=f"Got {variants_expr.type_name()}",
                expected="A list of variant name symbols: (variant1 variant2 ...)",
                example="(let ((State (enum (idle running)))) ...)",
                suggestion="Wrap variant names in parentheses: (enum (variant1 variant2 ...))",
                line=variants_expr.line,
                column=variants_expr.column,
                source=self.source
            )

        if variants_expr.is_empty():
            self._raise_if_early_close(expr, "enum")
            raise MenaiEvalError(
                message="Enum must declare at least one variant",
                received="An empty variant list",
                expected="One or more variant names: (enum (variant1 variant2 ...))",
                example="(let ((State (enum (idle running)))) ...)",
                suggestion="Declare at least one variant",
                line=variants_expr.line,
                column=variants_expr.column,
                source=self.source
            )

        variant_names: list[str] = []
        for i, variant in enumerate(variants_expr.elements):
            if not isinstance(variant, MenaiASTSymbol):
                self._raise_if_early_close(expr, "enum")
                raise MenaiEvalError(
                    message=f"Enum variant {i+1} must be a symbol",
                    received=f"Got {variant.type_name()}",
                    expected="Unquoted symbol (variant name)",
                    example="(enum (idle running)) not (enum (\"idle\" \"running\"))",
                    suggestion="Use unquoted names for enum variants",
                    line=variant.line,
                    column=variant.column,
                    source=self.source
                )

            if variant.name in variant_names:
                raise MenaiEvalError(
                    message=f"Enum variant name '{variant.name}' is duplicated",
                    received=f"Variant '{variant.name}' appears more than once",
                    expected="All variant names must be unique",
                    example="(enum (idle running)) not (enum (idle idle))",
                    suggestion="Use distinct names for each variant",
                    line=variant.line,
                    column=variant.column,
                    source=self.source
                )

            variant_names.append(variant.name)

        tag = self._next_enum_tag
        self._next_enum_tag += 1

        return MenaiASTEnum(
            name=binding_name,
            tag=tag,
            variant_names=tuple(variant_names),
            line=expr.line,
            column=expr.column,
            source_file=expr.source_file
        )

    def _reject_enum_outside_let(self, expr: MenaiASTList) -> MenaiASTList:
        """Reject (enum ...) used outside a let binding position."""
        raise MenaiEvalError(
            message="Enum definition must be the value in a let binding",
            received="(enum ...) used outside a binding",
            expected="(let ((TypeName (enum (variant1 variant2 ...)))) ...)",
            example="(let ((State (enum (idle running)))) (State 'idle))",
            suggestion="Wrap enum definitions in a let binding",
            line=expr.line,
            column=expr.column,
            source=self.source
        )


def _constant_key_id(node: MenaiASTNode) -> tuple[str, object] | None:
    """
    Return a comparable identity for a constant dict key, or None.

    The identity pairs the key's type name with its value, so that keys of
    different types with equal values (e.g. 1 and 1.0) are distinct, matching
    Menai's strict typing.  Non-literal keys return None and are not compared
    statically.
    """
    if isinstance(node, MenaiASTString):
        return ('string', node.value)

    if isinstance(node, MenaiASTBoolean):
        return ('boolean', node.value)

    if isinstance(node, MenaiASTInteger):
        return ('integer', node.value)

    if isinstance(node, MenaiASTFloat):
        return ('float', node.value)

    if isinstance(node, MenaiASTComplex):
        return ('complex', node.value)

    return None
