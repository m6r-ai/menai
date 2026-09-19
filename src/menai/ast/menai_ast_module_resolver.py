"""
Menai Module Resolver Pass - resolves import expressions at compile-time.

This pass walks the AST looking for (import "module-name") expressions and
replaces them with a MenaiASTNamespace carrying the module's bindings under
fresh, collision-free names and an export map pairing each export key with the
renamed binding that produced it.  Member access therefore resolves to a
specific binding and the compiler keeps full static knowledge of it.

A module's exports are the constant string keys of its top-level export dict.
The dict's values must be symbols bound by an enclosing let/let*/letrec in the
module body, so that the declaration behind each export can be identified.

The module's bindings are alpha-renamed with a prefix derived from the module
name and the import site, so importing two modules that both bind the same
private name does not collide, and importing the same module twice under
different names keeps the two copies distinct.

This pass uses a MenaiASTModuleLoader interface to delegate the actual module
loading logic.
"""

from typing import Protocol, ContextManager

from menai.menai_error import MenaiModuleError
from menai.ast.menai_ast import (
    MenaiASTNode, MenaiASTSymbol, MenaiASTList, MenaiASTString, MenaiASTNamespace, MenaiASTStruct,
)


class MenaiASTModuleLoader(Protocol):
    """
    Interface for loading Menai modules at compile-time.

    The module loader is responsible for:
    - Locating module files in the search path
    - Reading and compiling module source code
    - Caching compiled modules
    - Detecting and preventing circular import dependencies
    """

    def begin_loading(self, module_name: str) -> ContextManager[None]:
        """
        Begin loading a module and return a context manager for tracking.

        This method is called before load_module() to enable circular import detection.
        The context manager should track the module in a loading stack and automatically
        clean up when exiting (even on exception).

        Args:
            module_name: Name of module being loaded

        Returns:
            Context manager that tracks the loading state

        Raises:
            MenaiCircularImportError: If this module is already being loaded (circular dependency)
        """
        ...  # pylint: disable=unnecessary-ellipsis

    def load_module(self, module_name: str) -> MenaiASTNode:
        """
        Load and compile a module to a fully resolved AST.

        This should compile the module through the full front-end pipeline:
        - Lexing, parsing, semantic analysis, and module resolution

        The returned AST should have all imports already resolved.

        Note: Callers should use begin_loading() before calling this method
        to enable circular import detection.

        Args:
            module_name: Name of module (e.g., "calendar", "lib/validation")

        Returns:
            Fully resolved AST of the module (ready for inlining into parent AST)

        Raises:
            MenaiModuleError: If module not found or fails to load
            MenaiCircularImportError: If circular dependency detected
        """
        ...  # pylint: disable=unnecessary-ellipsis


class MenaiASTModuleResolver:
    """
    Resolves import expressions by loading modules and building their namespaces.

    This pass transforms:
        (import "calendar")
    Into:
        a MenaiASTNamespace mapping each export key to its declaration.

    The actual module loading is delegated to a MenaiASTModuleLoader interface.
    """

    def __init__(self, module_loader: MenaiASTModuleLoader | None = None):
        """
        Initialize module resolver pass.

        Args:
            module_loader: Optional module loader interface. If None, imports will fail.
        """
        self.module_loader = module_loader
        self._import_counter = 0

    def resolve(self, expr: MenaiASTNode) -> MenaiASTNode:
        """
        Resolve imports in an expression recursively.

        Args:
            expr: AST to resolve imports in

        Returns:
            AST with all imports replaced by namespace nodes
        """
        # Only lists need inspection
        if not isinstance(expr, MenaiASTList):
            return expr

        if expr.is_empty():
            return expr

        first = expr.first()

        # Check for import special form
        if isinstance(first, MenaiASTSymbol) and first.name == 'import':
            return self._resolve_import(expr)

        # Check for quote - don't resolve imports inside quoted expressions
        if isinstance(first, MenaiASTSymbol) and first.name == 'quote':
            return expr

        # Recursively resolve imports in all subexpressions
        resolved_elements = tuple(self.resolve(elem) for elem in expr.elements)
        return MenaiASTList(resolved_elements, line=expr.line, column=expr.column, source_file=expr.source_file)

    def _resolve_import(self, expr: MenaiASTList) -> MenaiASTNode:
        """
        Resolve an import expression by loading the module and building its namespace.

        Args:
            expr: Import expression (validated by semantic analyzer)

        Returns:
            A MenaiASTNamespace carrying the module's export map

        Raises:
            MenaiModuleError: If module cannot be loaded or has no resolvable exports
        """
        # Validation already done by semantic analyzer
        assert len(expr.elements) == 2, "Import should have exactly 2 elements (validated by semantic analyzer)"

        _, module_name_expr = expr.elements
        assert isinstance(module_name_expr, MenaiASTString), "Module name should be a string (validated by semantic analyzer)"

        module_name = module_name_expr.value

        # Delegate to module loader
        if self.module_loader is None:
            raise MenaiModuleError(
                message="No module loader configured",
                context=f"Attempted to import module '{module_name}'",
                suggestion="Module loader must be provided to compiler to use import"
            )

        # Use the module loader's context manager for circular import detection
        with self.module_loader.begin_loading(module_name):
            # Load the module (this will recursively compile if the module has imports)
            # The module loader handles circular detection and will raise MenaiCircularImportError if needed
            # The returned AST has all imports already resolved
            module_ast = self.module_loader.load_module(module_name)

        rename = self._make_rename(module_name)
        bindings, members = _extract_exports(module_ast, module_name, rename)
        return MenaiASTNamespace(
            bindings=bindings,
            members=members,
            line=expr.line,
            column=expr.column,
            source_file=expr.source_file,
        )

    def _make_rename(self, module_name: str) -> str:
        """
        Return a fresh rename prefix for one import site.

        The prefix is derived from the module name and a per-resolver counter,
        so importing the same module twice under different names, or importing
        two modules that share a private name, never collides.
        """
        self._import_counter += 1
        safe = module_name.replace('/', '-')
        return f"{safe}#{self._import_counter}#"


def _is_binding_form(node: MenaiASTNode) -> bool:
    """Return True if node is a (let/let*/letrec (bindings) body) form."""
    if not (isinstance(node, MenaiASTList) and len(node.elements) == 3):
        return False

    head = node.first()
    return isinstance(head, MenaiASTSymbol) and head.name in ('let', 'let*', 'letrec')


def _extract_exports(
    module_ast: MenaiASTNode, module_name: str, rename: str
) -> tuple[tuple[tuple[str, MenaiASTNode], ...], tuple[tuple[str, str], ...]]:
    """
    Extract a module's renamed bindings and its export map.

    A module is a let/let*/letrec chain whose body is an (export name ...) form.
    Every module binding is renamed with the given prefix, and every reference
    to a module binding inside a binding value is renamed to match, so a
    declaration that uses a sibling still resolves after the module is lifted
    into the importer.

    Returns:
        (bindings, members) where bindings is the renamed (name, value) pairs
        and members maps each export name to its renamed binding name.

    Raises:
        MenaiModuleError: If the module's shape does not expose resolvable exports
    """
    raw_bindings = _collect_bindings(module_ast)
    body = _unwrap_bindings(module_ast)

    renaming = {name: rename + name for name in raw_bindings}
    struct_names = {name for name, value in raw_bindings.items() if isinstance(value, MenaiASTStruct)}
    namespace_names = {name for name, value in raw_bindings.items() if isinstance(value, MenaiASTNamespace)}
    renamer = _ModuleRenamer(renaming, struct_names, namespace_names)

    bindings: list[tuple[str, MenaiASTNode]] = []
    for name, value in raw_bindings.items():
        bindings.append((rename + name, renamer.rename(value)))

    members = _extract_member_map(body, raw_bindings, renaming, module_name)
    return tuple(bindings), members


def _extract_member_map(
    body: MenaiASTNode,
    raw_bindings: dict[str, MenaiASTNode],
    renaming: dict[str, str],
    module_name: str,
) -> tuple[tuple[str, str], ...]:
    """Build the export map (name -> renamed binding name) from the export form."""
    body_head = body.first() if isinstance(body, MenaiASTList) and not body.is_empty() else None
    if not (isinstance(body_head, MenaiASTSymbol) and body_head.name == 'export'):
        raise MenaiModuleError(
            message=f"Module '{module_name}' does not declare its exports",
            context="A module's body must be an (export name ...) form",
            suggestion="End the module with (export name1 name2 ...)",
        )

    assert isinstance(body, MenaiASTList)
    members: list[tuple[str, str]] = []
    for name_expr in body.elements[1:]:
        if not isinstance(name_expr, MenaiASTSymbol):
            raise MenaiModuleError(
                message=f"Module '{module_name}' has a non-symbol export name",
                context="Export names must be symbols naming the module's bindings",
                suggestion="Use unquoted binding names: (export point make-point)",
            )

        if name_expr.name not in raw_bindings:
            raise MenaiModuleError(
                message=f"Module '{module_name}' exports unbound name '{name_expr.name}'",
                context="The exported name is not bound by an enclosing let/let*/letrec in the module",
                suggestion=f"Bind '{name_expr.name}' in the module before exporting it",
            )

        members.append((name_expr.name, renaming[name_expr.name]))

    return tuple(members)


def _collect_bindings(module_ast: MenaiASTNode) -> dict[str, MenaiASTNode]:
    """
    Collect every name bound by the module's top-level binding forms.

    Walks the chain of let/let*/letrec forms at the module's top level and
    records the value expression bound to each name.  A later binding of the
    same name shadows an earlier one, matching lexical semantics.
    """
    bindings: dict[str, MenaiASTNode] = {}
    node = module_ast
    while _is_binding_form(node):
        assert isinstance(node, MenaiASTList)
        bindings_list = node.elements[1]
        assert isinstance(bindings_list, MenaiASTList)
        for binding in bindings_list.elements:
            assert isinstance(binding, MenaiASTList) and len(binding.elements) == 2
            name_expr, value_expr = binding.elements
            assert isinstance(name_expr, MenaiASTSymbol)
            bindings[name_expr.name] = value_expr

        node = node.elements[2]

    return bindings


def _unwrap_bindings(module_ast: MenaiASTNode) -> MenaiASTNode:
    """Return the module's body with all top-level binding forms stripped."""
    node = module_ast
    while _is_binding_form(node):
        assert isinstance(node, MenaiASTList)
        node = node.elements[2]

    return node


class _ModuleRenamer:
    """
    Renames references to a module's top-level bindings within a module AST.

    Only free references to the module's own top-level binding names are
    renamed.  Inner binders (lambda parameters, inner let/let*/letrec bindings,
    match pattern variables) shadow a top-level name and suppress the rename
    within their scope, exactly as lexical semantics require.
    """

    def __init__(self, renaming: dict[str, str], struct_names: set[str], namespace_names: set[str]):
        """
        Initialize the renamer.

        Args:
            renaming: Map from original top-level name to renamed name.
            struct_names: Names of the module's struct-type bindings.  A struct
                pattern head names a type, not a bound variable, so it is
                renamed like any other reference to a module binding.
            namespace_names: Names of the module's namespace bindings (imports).
                In a member access (namespace member), the namespace name is
                renamed but the member name is a key, not a variable reference,
                so it is left alone.
        """
        self._renaming = renaming
        self._struct_names = struct_names
        self._namespace_names = namespace_names

    def rename(self, expr: MenaiASTNode) -> MenaiASTNode:
        """Return expr with free references to module bindings renamed."""
        return self._rename(expr, set())

    def _rename(self, expr: MenaiASTNode, shadowed: set[str]) -> MenaiASTNode:
        """Rename within expr, given the set of names shadowed by inner binders."""
        if isinstance(expr, MenaiASTSymbol):
            if expr.name in self._renaming and expr.name not in shadowed:
                return MenaiASTSymbol(
                    self._renaming[expr.name],
                    line=expr.line, column=expr.column, source_file=expr.source_file,
                )

            return expr

        if not isinstance(expr, MenaiASTList) or expr.is_empty():
            return expr

        head = expr.first()
        head_name = head.name if isinstance(head, MenaiASTSymbol) else None

        # Quote: contents are data, not code to rename.
        if head_name == 'quote':
            return expr

        # Namespace member access: (namespace member).  Rename the namespace
        # name but leave the member name, which is a key into the namespace's
        # export map rather than a reference to a module binding.
        if (head_name in self._namespace_names and head_name not in shadowed
                and len(expr.elements) == 2):
            new_head = MenaiASTSymbol(
                self._renaming[head_name],
                line=head.line, column=head.column, source_file=head.source_file,
            )
            return MenaiASTList(
                (new_head, expr.elements[1]),
                line=expr.line, column=expr.column, source_file=expr.source_file,
            )

        if head_name in ('let', 'let*', 'letrec'):
            return self._rename_binding_form(expr, head_name, shadowed)

        if head_name == 'lambda':
            return self._rename_lambda(expr, shadowed)

        if head_name == 'match':
            return self._rename_match(expr, shadowed)

        elements = tuple(self._rename(elem, shadowed) for elem in expr.elements)
        return MenaiASTList(elements, line=expr.line, column=expr.column, source_file=expr.source_file)

    def _rename_binding_form(
        self, expr: MenaiASTList, kind: str, shadowed: set[str]
    ) -> MenaiASTNode:
        """Rename a let/let*/letrec form, respecting its binding scope."""
        bindings_list = expr.elements[1]
        body = expr.elements[2]
        assert isinstance(bindings_list, MenaiASTList)

        binding_names = {
            binding.elements[0].name for binding in bindings_list.elements
            if isinstance(binding, MenaiASTList) and len(binding.elements) == 2
            and isinstance(binding.elements[0], MenaiASTSymbol)
        }

        if kind == 'let*':
            # Sequential: each binding value sees the preceding binding names.
            new_bindings: list[MenaiASTNode] = []
            inner_shadowed = set(shadowed)
            for binding in bindings_list.elements:
                assert isinstance(binding, MenaiASTList) and len(binding.elements) == 2
                name_expr, value_expr = binding.elements
                assert isinstance(name_expr, MenaiASTSymbol)
                new_value = self._rename(value_expr, inner_shadowed)
                new_bindings.append(MenaiASTList(
                    (name_expr, new_value),
                    line=binding.line, column=binding.column, source_file=binding.source_file,
                ))
                inner_shadowed.add(name_expr.name)

            body_shadowed = inner_shadowed

        else:
            # let/letrec: every binding name is in scope for every binding value
            # and for the body.
            body_shadowed = shadowed | binding_names
            new_bindings = []
            for binding in bindings_list.elements:
                assert isinstance(binding, MenaiASTList) and len(binding.elements) == 2
                name_expr, value_expr = binding.elements
                assert isinstance(name_expr, MenaiASTSymbol)
                new_value = self._rename(value_expr, body_shadowed)
                new_bindings.append(MenaiASTList(
                    (name_expr, new_value),
                    line=binding.line, column=binding.column, source_file=binding.source_file,
                ))

        new_bindings_list = MenaiASTList(
            tuple(new_bindings),
            line=bindings_list.line, column=bindings_list.column,
            source_file=bindings_list.source_file,
        )
        new_body = self._rename(body, body_shadowed)
        return MenaiASTList(
            (expr.elements[0], new_bindings_list, new_body),
            line=expr.line, column=expr.column, source_file=expr.source_file,
        )

    def _rename_lambda(self, expr: MenaiASTList, shadowed: set[str]) -> MenaiASTNode:
        """Rename a lambda, with its parameters shadowing module bindings."""
        params_list = expr.elements[1]
        body = expr.elements[2]
        assert isinstance(params_list, MenaiASTList)

        param_names = {
            param.name for param in params_list.elements
            if isinstance(param, MenaiASTSymbol) and param.name != '.'
        }
        new_body = self._rename(body, shadowed | param_names)
        return MenaiASTList(
            (expr.elements[0], params_list, new_body),
            line=expr.line, column=expr.column, source_file=expr.source_file,
        )

    def _rename_match(self, expr: MenaiASTList, shadowed: set[str]) -> MenaiASTNode:
        """Rename a match, with each clause's pattern variables shadowing."""
        value_expr = expr.elements[1]
        clauses = expr.elements[2:]

        new_value = self._rename(value_expr, shadowed)
        new_clauses: list[MenaiASTNode] = []
        for clause in clauses:
            assert isinstance(clause, MenaiASTList) and len(clause.elements) == 2
            pattern, result = clause.elements
            new_pattern = self._rename_pattern(pattern, shadowed)
            pattern_names = _pattern_names(new_pattern)
            new_result = self._rename(result, shadowed | pattern_names)
            new_clauses.append(MenaiASTList(
                (new_pattern, new_result),
                line=clause.line, column=clause.column, source_file=clause.source_file,
            ))

        return MenaiASTList(
            (expr.elements[0], new_value, *new_clauses),
            line=expr.line, column=expr.column, source_file=expr.source_file,
        )

    def _rename_pattern(self, pattern: MenaiASTNode, shadowed: set[str]) -> MenaiASTNode:
        """
        Rename references to module bindings within a match pattern.

        Only struct pattern heads name a module binding; every other symbol in
        a pattern is a variable the pattern binds, so it is left alone.
        """
        if not isinstance(pattern, MenaiASTList) or pattern.is_empty():
            return pattern

        head = pattern.first()
        if isinstance(head, MenaiASTSymbol) and head.name == '?':
            # Predicate pattern (? pred var): pred is an expression, var binds.
            pred = pattern.elements[1]
            new_pred = self._rename(pred, shadowed)
            return MenaiASTList(
                (head, new_pred, *pattern.elements[2:]),
                line=pattern.line, column=pattern.column, source_file=pattern.source_file,
            )

        if (isinstance(head, MenaiASTSymbol) and head.name in self._struct_names
                and head.name not in shadowed):
            new_head = MenaiASTSymbol(
                self._renaming[head.name],
                line=head.line, column=head.column, source_file=head.source_file,
            )
            new_elements = (new_head,) + tuple(
                self._rename_pattern(elem, shadowed) for elem in pattern.elements[1:]
            )
            return MenaiASTList(
                new_elements,
                line=pattern.line, column=pattern.column, source_file=pattern.source_file,
            )

        new_elements = tuple(self._rename_pattern(elem, shadowed) for elem in pattern.elements)
        return MenaiASTList(
            new_elements,
            line=pattern.line, column=pattern.column, source_file=pattern.source_file,
        )


def _pattern_names(pattern: MenaiASTNode) -> set[str]:
    """Return the variable names a match pattern binds."""
    if isinstance(pattern, MenaiASTSymbol):
        return set() if pattern.name == '_' else {pattern.name}

    if not isinstance(pattern, MenaiASTList) or pattern.is_empty():
        return set()

    head = pattern.first()
    if isinstance(head, MenaiASTSymbol) and head.name == '?' and len(pattern.elements) == 3:
        var = pattern.elements[2]
        if isinstance(var, MenaiASTSymbol) and var.name != '_':
            return {var.name}

        return set()

    names: set[str] = set()
    for elem in pattern.elements:
        if isinstance(elem, MenaiASTSymbol) and elem.name == '.':
            continue

        names |= _pattern_names(elem)

    return names
