"""
Menai Semantic Analyzer - validates AST structure and semantics.

This module performs semantic validation after parsing but before desugaring.
It checks:
- Special form arity (correct number of arguments)
- Binding structure validity (let, letrec)
- Lambda parameter validity
- Pattern validity (for match expressions)
- Duplicate binding detection

All validation happens in a single pass to provide clear, precise error messages
before any transformations occur.
"""

from typing import cast, TypeGuard

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
from menai.ast.menai_ast import MenaiASTStruct


class MenaiASTSemanticAnalyzer:
    """
    Validates Menai AST structure and semantics.

    This analyzer runs after parsing to check that all special forms
    are well-formed before any transformations (desugaring, compilation).
    """

    def __init__(self) -> None:
        """Initialize the semantic analyzer."""
        self.source = ""
        self._next_struct_tag: int = 0

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

    def analyze(self, expr: MenaiASTNode, source: str = "") -> MenaiASTNode:
        """
        Analyze an expression recursively, validating all special forms.

        Args:
            expr: AST to analyze
            source: Original source code (for error reporting with line/column)

        Returns:
            The same AST (unmodified) if validation passes

        Raises:
            MenaiEvalError: If validation fails with detailed error message
        """
        # Store source for error reporting
        self.source = source

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
                return self._analyze_let(expr)

            if name == 'let*':
                return self._analyze_let_star(expr)

            if name == 'letrec':
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
                # import is only valid as the value of a let/let*/letrec
                # binding.  The binding forms validate it directly, so
                # reaching here means it is used in any other position.
                return self._reject_import_outside_binding(expr)

            if name == 'struct':
                return self._reject_struct_outside_let(expr)

            if name == 'export':
                return self._analyze_export(expr)

            if name == 'apply':
                return self._analyze_apply(expr)

        # Regular function call - recursively analyze all subexpressions
        return self._analyze_call(expr)

    def _analyze_if(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate if expression: (if condition then else)"""
        if len(expr.elements) != 4:
            raise MenaiEvalError(
                message="If expression has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments: {expr.describe()}",
                expected="Exactly 3 arguments: (if condition then else)",
                example="(if (> x 0) \"positive\" \"negative\")",
                suggestion="If needs condition, then-branch, and else-branch",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        _, condition, then_expr, else_expr = expr.elements

        # Recursively analyze subexpressions
        self.analyze(condition, self.source)
        self.analyze(then_expr, self.source)
        self.analyze(else_expr, self.source)

        return expr

    def _analyze_let(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate let expression: (let ((var val) ...) body)"""
        if len(expr.elements) < 3:
            raise MenaiEvalError(
                message="Let expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (let ((bindings...)) body)",
                example="(let ((x 5) (y 10)) (+ x y))",
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
            raise MenaiEvalError(
                message="Let binding list must be a list",
                received=f"Binding list: {bindings_list.type_name()}",
                expected="List of bindings: ((var1 val1) (var2 val2) ...)",
                example="(let ((x 5) (y (* x 2))) (+ x y))",
                suggestion="Wrap bindings in parentheses: ((var val) (var val) ...)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Validate each binding
        var_names: list[str] = []
        namespace_names: set[str] = set()
        new_bindings: list[MenaiASTNode] = []
        for i, binding in enumerate(bindings_list.elements):
            if not isinstance(binding, MenaiASTList):
                raise MenaiEvalError(
                    message=f"Let binding {i+1} must be a list",
                    received=f"Binding {i+1}: {binding.type_name()}",
                    expected="Each binding: (variable value-expression)",
                    example='(x 5)',
                    suggestion="Wrap each binding in parentheses: (variable-name value-expression)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            if len(binding.elements) != 2:
                binding_str = f"{len(binding.elements)} elements"
                raise MenaiEvalError(
                    message=f"Let binding {i+1} has wrong number of elements",
                    received=f"Binding {i+1}: {binding_str}",
                    expected="Each binding: (variable value-expression)",
                    example='Correct: (x 5)\nIncorrect: (x) or (x 1 2)',
                    suggestion="Each binding needs exactly variable name and value: (var value)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            name_expr, value_expr = binding.elements

            # Check if this binding's value is a struct definition
            if (isinstance(name_expr, MenaiASTSymbol) and
                    isinstance(value_expr, MenaiASTList) and
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

            # A binding whose value is an import binds a namespace name.
            if isinstance(name_expr, MenaiASTSymbol) and self._is_import_expr(value_expr):
                self._analyze_import(value_expr)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, value_expr),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                namespace_names.add(name_expr.name)
                continue

            if not isinstance(name_expr, MenaiASTSymbol):
                raise MenaiEvalError(
                    message=f"Let binding {i+1} variable must be a symbol",
                    received=f"Variable: {name_expr.type_name()}",
                    expected="Unquoted symbol (variable name)",
                    example='Correct: (x 5)\nIncorrect: ("x" 5)',
                    suggestion="Use unquoted variable names in bindings",
                    line=name_expr.line,
                    column=name_expr.column,
                    source=self.source
                )

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
                example='Correct: (let ((x 1) (y 2)) ...)\nIncorrect: (let ((x 1) (x 2)) ...)',
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

    def _analyze_let_star(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate let* expression: (let* ((var val) ...) body)"""
        if len(expr.elements) < 3:
            raise MenaiEvalError(
                message="Let* expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (let* ((bindings...)) body)",
                example="(let* ((x 5) (y (* x 2))) (+ x y))",
                suggestion="Let* needs binding list and body: (let* ((var1 val1) (var2 val2) ...) body)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # _parse_let_with_tracking hard-caps 'let*' at exactly 3 elements
        # (keyword + bindings + body) before consuming ')' — no loop after body.
        assert len(expr.elements) <= 3, (
            f"Parser invariant violated: 'let*' produced {len(expr.elements)} elements "
            f"(expected \u2264 3); _parse_let_with_tracking should cap at 3"
        )

        _, bindings_list, body = expr.elements

        if not isinstance(bindings_list, MenaiASTList):
            raise MenaiEvalError(
                message="Let* binding list must be a list",
                received=f"Binding list: {bindings_list.type_name()}",
                expected="List of bindings: ((var1 val1) (var2 val2) ...)",
                example="(let* ((x 5) (y (* x 2))) (+ x y))",
                suggestion="Wrap bindings in parentheses: ((var val) (var val) ...)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Validate each binding
        var_names: list[str] = []
        namespace_count = 0
        new_bindings: list[MenaiASTNode] = []
        for i, binding in enumerate(bindings_list.elements):
            if not isinstance(binding, MenaiASTList):
                raise MenaiEvalError(
                    message=f"Let* binding {i+1} must be a list",
                    received=f"Binding {i+1}: {binding.type_name()}",
                    expected="Each binding: (variable value-expression)",
                    example='(x 5)',
                    suggestion="Wrap each binding in parentheses: (variable-name value-expression)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            if len(binding.elements) != 2:
                binding_str = f"{len(binding.elements)} elements"
                raise MenaiEvalError(
                    message=f"Let* binding {i+1} has wrong number of elements",
                    received=f"Binding {i+1}: {binding_str}",
                    expected="Each binding: (variable value-expression)",
                    example='Correct: (x 5)\nIncorrect: (x) or (x 1 2)',
                    suggestion="Each binding needs exactly variable name and value: (var value)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            name_expr, value_expr = binding.elements

            # Check if this binding's value is a struct definition
            if (isinstance(name_expr, MenaiASTSymbol) and
                    isinstance(value_expr, MenaiASTList) and
                    not value_expr.is_empty() and
                    isinstance(value_expr.elements[0], MenaiASTSymbol) and
                    value_expr.elements[0].name == 'struct'):
                struct_node = self._analyze_struct(value_expr, name_expr.name)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, struct_node),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                self._push_scope({name_expr.name})
                continue

            # A binding whose value is an import binds a namespace name.
            if isinstance(name_expr, MenaiASTSymbol) and self._is_import_expr(value_expr):
                self._analyze_import(value_expr)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, value_expr),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                self._push_scope({name_expr.name})
                self._push_namespace_scope({name_expr.name})
                namespace_count += 1
                continue

            if not isinstance(name_expr, MenaiASTSymbol):
                raise MenaiEvalError(
                    message=f"Let* binding {i+1} variable must be a symbol",
                    received=f"Variable: {name_expr.type_name()}",
                    expected="Unquoted symbol (variable name)",
                    example='Correct: (x 5)\nIncorrect: ("x" 5)',
                    suggestion="Use unquoted variable names in bindings",
                    line=name_expr.line,
                    column=name_expr.column,
                    source=self.source
                )

            var_names.append(name_expr.name)

            # Recursively analyze the value expression
            analyzed_value = self.analyze(value_expr, self.source)
            new_bindings.append(MenaiASTList(
                elements=(name_expr, analyzed_value),
                line=binding.line, column=binding.column, source_file=binding.source_file
            ))

            # let* is sequential: this binding's name is in scope for every
            # subsequent binding value and for the body.
            self._push_scope({name_expr.name})

        # Note: Unlike 'let', we allow duplicate binding names (shadowing) in let*.
        # This is because let* has sequential semantics where later bindings
        # can shadow earlier ones, and we also don't check if later bindings reference earlier ones.
        # That's the whole point of let* - sequential bindings are allowed and expected.

        # Analyze body
        try:
            analyzed_body = self.analyze(body, self.source)

        finally:
            for _ in range(namespace_count):
                self._pop_namespace_scope()

            for _ in var_names:
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
        """Validate letrec expression: (letrec ((var val) ...) body)"""
        if len(expr.elements) < 3:
            raise MenaiEvalError(
                message="Letrec expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (letrec ((bindings...)) body)",
                example="(letrec ((fact (lambda (n) (if (<= n 1) 1 (* n (fact (- n 1))))))) (fact 5))",
                suggestion="Letrec needs binding list and body: (letrec ((var1 val1) (var2 val2) ...) body)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # _parse_let_with_tracking hard-caps 'letrec' at exactly 3 elements
        # (keyword + bindings + body) before consuming ')' — no loop after body.
        assert len(expr.elements) <= 3, (
            f"Parser invariant violated: 'letrec' produced {len(expr.elements)} elements "
            f"(expected ≤ 3); _parse_let_with_tracking should cap at 3"
        )

        _, bindings_list, body = expr.elements
        if not isinstance(bindings_list, MenaiASTList):
            raise MenaiEvalError(
                message="Letrec binding list must be a list",
                received=f"Binding list: {bindings_list.type_name()}",
                expected="List of bindings: ((var1 val1) (var2 val2) ...)",
                example="(letrec ((f (lambda (n) (if (= n 0) 1 (* n (f (- n 1))))))) (f 5))",
                suggestion="Wrap bindings in parentheses: ((var val) (var val) ...)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Validate each binding
        var_names: list[str] = []
        new_bindings: list[MenaiASTNode] = []

        # Every letrec binding is visible to every sibling value and to the body,
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
        for i, binding in enumerate(bindings_list.elements):
            if not isinstance(binding, MenaiASTList):
                raise MenaiEvalError(
                    message=f"Letrec binding {i+1} must be a list",
                    received=f"Binding {i+1}: {binding.type_name()}",
                    expected="List with variable and value: (var val)",
                    example='Correct: (x 5)\nIncorrect: x or "x"',
                    suggestion="Wrap each binding in parentheses: (variable value)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            if len(binding.elements) != 2:
                raise MenaiEvalError(
                    message=f"Letrec binding {i+1} has wrong number of elements",
                    received=f"Binding {i+1}: has {len(binding.elements)} elements",
                    expected="Each binding needs exactly 2 elements: (variable value)",
                    example='Correct: (x 5)\nIncorrect: (x) or (x 5 6)',
                    suggestion="Each binding: (variable-name value-expression)",
                    line=binding.line,
                    column=binding.column,
                    source=self.source
                )

            name_expr, value_expr = binding.elements

            # Struct definitions are permitted in letrec: they have no recursive
            # semantics and the desugarer hoists them to let automatically.
            if (isinstance(name_expr, MenaiASTSymbol) and
                    isinstance(value_expr, MenaiASTList) and
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

            # A binding whose value is an import binds a namespace name.
            if isinstance(name_expr, MenaiASTSymbol) and self._is_import_expr(value_expr):
                self._analyze_import(value_expr)
                new_bindings.append(MenaiASTList(
                    elements=(name_expr, value_expr),
                    line=binding.line, column=binding.column, source_file=binding.source_file
                ))
                var_names.append(name_expr.name)
                continue

            if not isinstance(name_expr, MenaiASTSymbol):
                raise MenaiEvalError(
                    message=f"Letrec binding {i+1} variable must be a symbol",
                    received=f"Variable: {name_expr.type_name()}",
                    expected="Unquoted symbol (variable name)",
                    example='Correct: (x 5)\nIncorrect: ("x" 5) or (1 5)',
                    suggestion='Use unquoted variable names: x, not \"x\"',
                    line=name_expr.line,
                    column=name_expr.column,
                    source=self.source
                )

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
                    message="Letrec binding variables must be unique",
                    received=f"Duplicate variables: {duplicates}",
                    expected="All variable names should be different",
                    example='Correct: (letrec ((x 1) (y 2)) ...)\nIncorrect: (letrec ((x 1) (x 2)) ...)',
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
        if len(expr.elements) != 3:
            raise MenaiEvalError(
                message="Lambda expression structure is incorrect",
                received=f"Got {len(expr.elements)} elements",
                expected="Exactly 3 elements: (lambda (params...) body)",
                example="(lambda (x y) (+ x y))",
                suggestion="Lambda needs parameter list and body: (lambda (param1 param2 ...) body-expression)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        _, params_list, body = expr.elements

        if not isinstance(params_list, MenaiASTList):
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
                raise MenaiEvalError(
                    message=f"Lambda parameter {i+1} must be a symbol",
                    received=f"Parameter {i+1}: {param.type_name()}",
                    expected="Unquoted symbol (variable name)",
                    example='Correct: (lambda (x y) (+ x y))\nIncorrect: (lambda ("x" 1) ...)',
                    suggestion='Use unquoted names: x, not \"x\" or 1',
                    line=param.line,
                    column=param.column,
                    source=self.source
                )

            if param.name == '.':
                if dot_index is not None:
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
                param_names.append(param.name)

        if dot_index is not None:
            # Dot must be second-to-last: exactly one symbol must follow it
            if dot_index != len(elements) - 2:
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
            raise MenaiEvalError(
                message="Lambda parameters must be unique",
                received=f"Duplicate parameters: {duplicates}",
                expected="All parameter names should be different",
                example='Correct: (lambda (x y z) ...)\nIncorrect: (lambda (x y x) ...)',
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
        """Validate quote expression: (quote expr)"""
        if len(expr.elements) != 2:
            raise MenaiEvalError(
                message="Quote expression has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments: {expr.describe()}",
                expected="Exactly 1 argument",
                example="(quote expr) or 'expr",
                suggestion="Quote requires exactly one expression to quote",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        # Note: We don't recursively analyze the quoted expression
        # because it's data, not code to be evaluated
        return expr

    def _analyze_match(self, expr: MenaiASTList) -> MenaiASTList:
        """Validate match expression: (match value (pattern result) ...)"""
        if len(expr.elements) < 3:
            raise MenaiEvalError(
                message="Match expression has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments",
                expected="At least 2 arguments: (match value (pattern1 result1) ...)",
                example="(match x ((? integer? n) (* n 2)) (_ \"not a number\"))",
                suggestion="Match needs a value and at least one pattern clause",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        value_expr = expr.elements[1]
        clauses = list(expr.elements[2:])

        # Analyze the value expression
        self.analyze(value_expr, self.source)

        # Validate all clauses
        for i, clause in enumerate(clauses):
            if not isinstance(clause, MenaiASTList):
                raise MenaiEvalError(
                    message=f"Match clause {i+1} must be a list",
                    received=f"Clause {i+1}: {clause.type_name()}",
                    expected="Each clause: (pattern result-expression)",
                    example="((? integer? n) (* n 2))",
                    suggestion="Wrap each clause in parentheses: (pattern result)",
                    line=clause.line,
                    column=clause.column,
                    source=self.source
                )

            if len(clause.elements) != 2:
                raise MenaiEvalError(
                    message=f"Match clause {i+1} has wrong number of elements",
                    received=f"Clause {i+1}: {clause}",
                    expected="Each clause needs exactly 2 elements: (pattern result)",
                    example="Correct: ((? integer? n) (* n 2))\nIncorrect: ((? integer? n)) or ((? integer? n) result1 result2)",
                    suggestion="Each clause: (pattern result-expression)",
                    line=clause.line,
                    column=clause.column,
                    source=self.source
                )

            pattern, result_expr = clause.elements

            # Validate the pattern
            self._analyze_match_pattern(pattern, i + 1)

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
            return set() if pattern.name == '_' else {pattern.name}

        if not isinstance(pattern, MenaiASTList) or pattern.is_empty():
            return set()

        # Predicate pattern (? pred var) binds only var.
        if (isinstance(pattern.elements[0], MenaiASTSymbol)
                and pattern.elements[0].name == '?'
                and len(pattern.elements) == 3):
            var_pattern = pattern.elements[2]
            if isinstance(var_pattern, MenaiASTSymbol) and var_pattern.name != '_':
                return {var_pattern.name}

            return set()

        names: set[str] = set()
        for elem in pattern.elements:
            if isinstance(elem, MenaiASTSymbol) and elem.name == '.':
                continue

            names |= self._collect_pattern_names(elem)

        return names

    def _analyze_match_pattern(self, pattern: MenaiASTNode, clause_num: int) -> None:
        """
        Validate a match pattern.

        Args:
            pattern: Pattern to validate
            clause_num: Clause number (for error messages)
        """
        if not isinstance(pattern, MenaiASTList):
            return

        # Empty list pattern is valid
        if pattern.is_empty():
            return

        # Check for predicate test pattern: (? pred var)
        if (len(pattern.elements) >= 1 and
            isinstance(pattern.elements[0], MenaiASTSymbol) and
            pattern.elements[0].name == '?'):

            if len(pattern.elements) != 3:
                raise MenaiEvalError(
                    message=f"Invalid predicate pattern in clause {clause_num}",
                    received=f"Pattern: {pattern}",
                    expected="Exactly 3 elements: (? predicate variable)",
                    example="(? integer? n) or (? my-pred? x)",
                    suggestion="Use (? predicate variable) for predicate test patterns",
                    line=pattern.line,
                    column=pattern.column,
                    source=self.source
                )

            pred_expr = pattern.elements[1]
            var_pattern = pattern.elements[2]
            if not isinstance(var_pattern, MenaiASTSymbol):
                raise MenaiEvalError(
                    message=f"Pattern variable must be a symbol in clause {clause_num}",
                    received=f"Variable in predicate pattern: {var_pattern}",
                    expected="Symbol (variable name)",
                    example="(? integer? x) not (? integer? 42)",
                    suggestion="Use an unquoted variable name as the third element of a predicate pattern",
                    line=var_pattern.line,
                    column=var_pattern.column,
                    source=self.source
                )

            # Analyze the predicate expression — can be any valid expression
            self.analyze(pred_expr, self.source)
            return

        # Check for cons pattern: (head . tail) or (a b . rest)
        dot_positions = []
        for i, elem in enumerate(pattern.elements):
            if isinstance(elem, MenaiASTSymbol) and elem.name == '.':
                dot_positions.append(i)

        # Validate: at most one dot
        if len(dot_positions) > 1:
            raise MenaiEvalError(
                message=f"Invalid pattern in clause {clause_num}",
                received=f"Pattern: {pattern} - multiple dots",
                expected="At most one dot in cons pattern",
                example="(head . tail) or (a b . rest)",
                suggestion="Use only one dot to separate head from tail",
                line=pattern.line,
                column=pattern.column,
                source=self.source
            )

        # If we have a dot, validate cons pattern structure
        if dot_positions:
            dot_position = dot_positions[0]

            if dot_position == 0:
                raise MenaiEvalError(
                    message=f"Invalid pattern in clause {clause_num}",
                    received=f"Pattern: {pattern} - dot at beginning",
                    expected="Dot must come after at least one element",
                    example="(head . tail) not (. tail)",
                    suggestion="Put at least one pattern before the dot",
                    line=pattern.line,
                    column=pattern.column,
                    source=self.source
                )

            if dot_position == len(pattern.elements) - 1:
                raise MenaiEvalError(
                    message=f"Invalid pattern in clause {clause_num}",
                    received=f"Pattern: {pattern} - dot at end",
                    expected="Dot must be followed by tail pattern",
                    example="(head . tail) not (head .)",
                    suggestion="Add a tail pattern after the dot",
                    line=pattern.line,
                    column=pattern.column,
                    source=self.source
                )

            if dot_position != len(pattern.elements) - 2:
                raise MenaiEvalError(
                    message=f"Invalid pattern in clause {clause_num}",
                    received=f"Pattern: {pattern} - multiple elements after dot",
                    expected="Exactly one tail pattern after dot",
                    example="(a b . rest) not (a . b c)",
                    suggestion="Use only one pattern after the dot for the tail",
                    line=pattern.line,
                    column=pattern.column,
                    source=self.source
                )

            # Recursively validate head patterns
            for i in range(dot_position):
                self._analyze_match_pattern(pattern.elements[i], clause_num)

            # Validate tail pattern
            tail_pattern = cast(MenaiASTNode, pattern.elements[dot_position + 1])
            self._analyze_match_pattern(tail_pattern, clause_num)

            return

        # Fixed-length list pattern: (p1 p2 p3)
        # Recursively validate each element pattern
        for elem_pattern in pattern.elements:
            self._analyze_match_pattern(elem_pattern, clause_num)

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
        if len(expr.elements) != 3:
            raise MenaiEvalError(
                message="Namespace member access has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments: {expr.describe()}",
                expected="Exactly 2 arguments: (:: namespace member)",
                example="(:: shapes point-distance)",
                suggestion="Access a single member of the namespace by name",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

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
        """Reject (import ...) used anywhere other than a let/let*/letrec binding value."""
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
        if len(expr.elements) != 2:
            raise MenaiEvalError(
                message="Import expression has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments: {expr.describe()}",
                expected="Exactly 1 argument: (import \"module-name\")",
                example='(import "calendar") or (import "lib/validation")',
                suggestion="Import needs exactly one module name as a string",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

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
        """Validate apply expression: (apply f args)"""
        if len(expr.elements) != 3:
            raise MenaiEvalError(
                message="Apply expression has wrong number of arguments",
                received=f"Got {len(expr.elements) - 1} arguments: {expr.describe()}",
                expected="Exactly 2 arguments: (apply f args)",
                example="(apply integer+ (list 1 2 3))",
                suggestion="Apply needs a function and an argument list: (apply f args)",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        _, func_expr, args_expr = expr.elements

        # Recursively analyse subexpressions
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

        Called only when (struct ...) appears as the RHS of a let or let* binding.
        Assigns a fresh compile-time tag and extracts the field names.

        Args:
            expr: The raw (struct (field ...)) AST list node
            binding_name: The name of the enclosing let binding (becomes the type name)

        Returns:
            A MenaiASTStruct node with name, tag, and field_names populated
        """
        if len(expr.elements) != 2:
            raise MenaiEvalError(
                message="Struct definition has wrong number of elements",
                received=f"Got {len(expr.elements) - 1} argument(s)",
                expected="Exactly 1 argument: (struct (field1 field2 ...))",
                example="(let ((Point (struct (x y)))) ...)",
                suggestion="Provide exactly one field list to struct",
                line=expr.line,
                column=expr.column,
                source=self.source
            )

        _, fields_expr = expr.elements

        if not isinstance(fields_expr, MenaiASTList):
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
        """Reject (struct ...) used outside a let/let* binding position."""
        raise MenaiEvalError(
            message="Struct definition must be the value in a let or let* binding",
            received="(struct ...) used outside a let or let* binding",
            expected="(let ((TypeName (struct (field1 field2 ...)))) ...)",
            example="(let ((Point (struct (x y)))) (Point 1 2))",
            suggestion="Wrap struct definitions in a let or let* binding",
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
