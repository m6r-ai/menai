"""Main Menai (AI Functional Programming Language) class with enhanced error messages."""

from collections.abc import Iterator
import hashlib
from importlib.resources import files
from pathlib import Path
import os

from contextlib import contextmanager

from menai.bytecode.menai_bytecode import CodeObject
from menai.menai_compiler import MenaiCompiler
from menai.ast.menai_ast import MenaiASTNode
from menai.ir.menai_ir import MenaiIRLambda
from menai.menai_value import MenaiFunction, MenaiValue
from menai.vm.menai_vm import MenaiVM
from menai.menai_error import MenaiModuleNotFoundError, MenaiModuleError, MenaiCircularImportError


class Menai:
    """
    Menai (AI Functional Programming Language) calculator with LISP-like syntax and enhanced error messages.

    This version provides comprehensive error reporting with:
    - Clear explanations of what went wrong
    - Context showing the problematic input
    - Suggestions for how to fix the problem
    - Examples of correct usage
    - Position information where helpful

    Designed specifically to help LLMs understand and self-correct errors.

    Execution Model:
    - Uses bytecode ir_builder and VM for all evaluation
    - Tail-call optimized for recursive functions
    - High performance through bytecode compilation and optimized VM
    """

    @classmethod
    def _load_prelude_source(cls) -> str:
        """Load the prelude source from the bundled prelude.menai file."""
        if cls._prelude_source is None:
            cls._prelude_source = (files("menai") / "prelude.menai").read_text()

        return cls._prelude_source

    _prelude_source: str | None = None
    _prelude_code: CodeObject | None = None
    _prelude_lambdas: dict[str, MenaiIRLambda] | None = None

    def __init__(self, module_path: list[str] | None = None):
        """
        Initialize Menai calculator.

        Args:
            module_path: List of directories to search for modules (default: ["."])
        """
        self._module_path = module_path or ["."]

        # Module system state
        self.module_cache: dict[str, MenaiASTNode] = {}  # module_name -> dict
        self.module_hashes: dict[str, str] = {}  # module_name -> sha256 hex digest
        self.loading_stack: list[str] = []  # Track currently-loading modules for circular detection

        # Compiler and VM
        self.compiler = MenaiCompiler(module_loader=self)
        self.vm = MenaiVM()

        prelude_source = Menai._load_prelude_source()

        if Menai._prelude_code is None:
            Menai._prelude_code = self.compiler.compile(prelude_source, name="<prelude>")

        if Menai._prelude_lambdas is None:
            prelude_ir = self.compiler.compile_to_ir(prelude_source, name="<prelude>")
            Menai._prelude_lambdas = MenaiCompiler._extract_prelude_lambdas(prelude_ir)

        self.compiler.set_prelude_lambdas(Menai._prelude_lambdas)

        self.vm.set_prelude(Menai._prelude_code)

    def prelude_code(self) -> CodeObject:
        """
        Return the compiled prelude CodeObject.

        The prelude is compiled once on first instantiation and shared across
        all Menai instances.  This accessor exposes it for tools (e.g. the
        disassembler) that need to inspect the prelude bytecode.
        """
        assert Menai._prelude_code is not None
        return Menai._prelude_code

    def compile(self, expression: str) -> CodeObject:
        """
        Compile a Menai expression to bytecode without executing it.

        The returned CodeObject can be passed to `execute_raw` for
        execution without re-compilation, allowing the compilation cost to
        be separated from execution cost (useful for benchmarking).

        Args:
            expression: Menai expression string to compile

        Returns:
            Compiled bytecode ready for execution
        """
        return self.compiler.compile(expression)

    def execute_raw(self, code: CodeObject) -> 'MenaiValue':
        """
        Execute compiled bytecode and return the raw MenaiValue.

        Unlike `evaluate`, this does not convert the result to
        Python types via to_python(), avoiding the traversal overhead.

        Args:
            code: Compiled code object (from `compile`)

        Returns:
            The result as a raw MenaiValue
        """
        return self.vm.execute(code)

    def evaluate_raw(self, expression: str) -> 'MenaiValue':
        """
        Compile and evaluate a Menai expression, returning the raw MenaiValue.

        Args:
            expression: Menai expression string to evaluate

        Returns:
            The result of evaluating the expression as MenaiValue
        """
        # Compile (lexing, parsing, semantic analysis, IR building, code generation)
        code = self.compiler.compile(expression)

        # Execute
        result = self.vm.execute(code)
        return result

    def evaluate(self, expression: str) -> int | float | complex | str | bool | list | MenaiFunction:
        """
        Evaluate an Menai expression with comprehensive enhanced error reporting.

        Args:
            expression: Menai expression string to evaluate

        Returns:
            The result of evaluating the expression converted to Python types

        Raises:
            MenaiTokenError: If tokenization fails (with detailed context and suggestions)
            MenaiParseError: If parsing fails (with detailed context and suggestions)
            MenaiEvalError: If evaluation fails (with detailed context and suggestions)
        """
        result = self.evaluate_raw(expression)
        return result.to_python()

    def evaluate_and_format(self, expression: str) -> str:
        """
        Evaluate an Menai expression and return formatted result with comprehensive enhanced error reporting.

        Args:
            expression: Menai expression string to evaluate

        Returns:
            String representation of the result using LISP conventions

        Raises:
            MenaiTokenError: If tokenization fails (with detailed context and suggestions)
            MenaiParseError: If parsing fails (with detailed context and suggestions)
            MenaiEvalError: If evaluation fails (with detailed context and suggestions)
        """
        result = self.evaluate_raw(expression)
        return result.describe()

    def evaluate_raw_with_bindings(
        self,
        expression: str,
        bindings: dict[str, MenaiValue]
    ) -> MenaiValue:
        """
        Evaluate a Menai expression with additional pre-bound name bindings.

        The bindings are merged with the prelude globals so the expression can
        reference both prelude functions and the injected names.  Bindings shadow
        prelude names on collision.

        This is the primary engine entry point for the transform harness.  The
        caller reads file content, constructs MenaiValue bindings (e.g.
        MenaiString for 'input-text', MenaiList of MenaiString for
        'input-lines'), then passes a Menai expression that transforms them.

        Args:
            expression: Menai source expression to compile and evaluate.
            bindings: Extra name-to-value bindings available to the expression
                      as top-level globals alongside the prelude.

        Returns:
            The raw MenaiValue result (caller inspects type and extracts value).
        """
        code = self.compiler.compile(expression)
        return self.vm.execute(code, bindings)

    def evaluate_and_format_with_bindings(
        self,
        expression: str,
        bindings: dict[str, MenaiValue]
    ) -> str:
        """
        Evaluate a Menai expression with pre-bound bindings, returning a formatted string.

        Equivalent to evaluate_raw_with_bindings but returns the Menai describe()
        string representation of the result, matching the format returned by
        evaluate_and_format.

        Args:
            expression: Menai source expression to compile and evaluate.
            bindings: Extra name-to-value bindings available to the expression
                      as top-level globals alongside the prelude.

        Returns:
            String representation of the result using Menai describe() conventions.
        """
        return self.evaluate_raw_with_bindings(expression, bindings).describe()

    # Module System Implementation (MenaiASTModuleLoader interface)

    @contextmanager
    def begin_loading(self, module_name: str) -> Iterator[None]:
        """
        Begin loading a module with circular import detection.

        This context manager tracks the module in the loading stack and
        automatically cleans up when exiting (even on exception).

        Args:
            module_name: Name of module being loaded

        Yields:
            None

        Raises:
            MenaiCircularImportError: If this module is already being loaded
        """
        # Check for circular dependency BEFORE adding to stack
        if module_name in self.loading_stack:
            cycle = self.loading_stack + [module_name]
            raise MenaiCircularImportError(import_chain=cycle)

        # Add to loading stack
        self.loading_stack.append(module_name)
        try:
            yield

        finally:
            # Always remove from stack, even if loading fails
            self.loading_stack.pop()

    def _compute_file_hash(self, file_path: str) -> str:
        """
        Compute SHA256 hash of file content.

        Uses chunked reading for memory efficiency with large files.

        Args:
            file_path: Path to file to hash

        Returns:
            SHA256 hash as hex string
        """
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)

        return hasher.hexdigest()

    def resolve_module(self, module_name: str) -> str:
        """
        Find module file in search path.

        Security: Module names must not use absolute or relative path navigation.
        Only simple names (e.g., "calendar") or subdirectory paths (e.g., "lib/validation")
        are allowed. This prevents escaping the configured module directories.

        Args:
            module_name: Name like "calendar" or "lib/validation"

        Returns:
            Full path to module file

        Raises:
            MenaiModuleNotFoundError: If module not found in search path
            MenaiModuleError: If module name contains invalid path components
        """
        # Reject absolute paths
        if module_name.startswith('/') or (os.sep != '/' and module_name.startswith(os.sep)):
            raise MenaiModuleError(
                message=f"Absolute module paths are not allowed: '{module_name}'",
                context="Module names must be relative to the module search path",
                suggestion="Use a simple module name like 'calendar' or 'lib/validation'"
            )

        # Reject relative path navigation (. or ..)
        if module_name.startswith('./') or module_name.startswith('../') or '/./' in module_name or '/../' in module_name:
            raise MenaiModuleError(
                message=f"Relative path navigation is not allowed in module names: '{module_name}'",
                context="Module names must not contain './' or '../' path components",
                suggestion="Use a simple module name like 'calendar' or 'lib/validation'"
            )

        # Search for module in configured paths
        for directory in self._module_path:
            module_path = Path(directory) / f"{module_name}.menai"
            if module_path.exists():
                return str(module_path)

        raise MenaiModuleNotFoundError(
            module_name=module_name,
            search_paths=self._module_path
        )

    def load_module(self, module_name: str) -> MenaiASTNode:
        """
        Load and compile a module to a fully resolved AST.

        This implements the MenaiASTModuleLoader interface. It compiles the module through
        the full front-end pipeline (lex, parse, semantic analysis, module resolution).
        The result is cached for subsequent imports. Cache is automatically invalidated
        when the module file content changes (detected via SHA256 hash).

        Note: Callers should use begin_loading() before calling this method to enable
        circular import detection. The module resolver handles this automatically.

        Args:
            module_name: Name of module to load

        Returns:
            Fully resolved AST of the module (all imports already resolved)

        Raises:
            MenaiModuleNotFoundError: If module file not found
            MenaiCircularImportError: If circular dependency detected (via begin_loading)
            MenaiError: If module compilation fails
        """
        # Resolve to file path
        try:
            module_path = self.resolve_module(module_name)

        except MenaiModuleNotFoundError:
            # File doesn't exist - clean up any stale cache entries
            self.module_cache.pop(module_name, None)
            self.module_hashes.pop(module_name, None)
            raise

        # Compute current file hash for cache invalidation
        try:
            current_hash = self._compute_file_hash(module_path)

        except OSError as e:
            # File disappeared after resolve - clean up cache and raise
            self.module_cache.pop(module_name, None)
            self.module_hashes.pop(module_name, None)
            raise MenaiModuleNotFoundError(
                module_name=module_name,
                search_paths=self._module_path
            ) from e

        # Check cache validity using content hash
        if module_name in self.module_cache:
            cached_hash = self.module_hashes.get(module_name)
            if cached_hash == current_hash:
                # Cache is valid - return cached AST
                return self.module_cache[module_name]

            # Cache is stale - will reload below

        # Load source code
        with open(module_path, 'r', encoding='utf-8') as f:
            code = f.read()

        # Compile through the front-end pipeline (lex, parse, analyze, resolve imports)
        # This will recursively handle any imports within this module.
        # The module resolver will call begin_loading() for each nested import,
        # which provides circular import detection.
        # Use module name with .menai extension for source_file (relative path)
        resolved_ast = self.compiler.compile_to_resolved_ast(code, f"{module_name}.menai")

        # Cache the resolved module and update hash after successful compilation
        self.module_cache[module_name] = resolved_ast
        self.module_hashes[module_name] = current_hash

        return resolved_ast

    def clear_module_cache(self) -> None:
        """Clear the module cache and hashes. Useful for development/testing."""
        self.module_cache.clear()
        self.module_hashes.clear()

    def invalidate_module(self, module_name: str) -> None:
        """
        Invalidate a specific module in the cache, forcing reload on next import.

        Args:
            module_name: Name of module to invalidate (e.g., "calendar" or "lib/validation")
        """
        self.module_cache.pop(module_name, None)
        self.module_hashes.pop(module_name, None)

    def reload_module(self, module_name: str) -> MenaiASTNode:
        """
        Force reload a module, bypassing cache.

        Args:
            module_name: Name of module to reload

        Returns:
            Fully resolved AST of the reloaded module
        """
        self.invalidate_module(module_name)
        return self.load_module(module_name)

    def set_module_path(self, module_path: list[str]) -> None:
        """
        Set the module search path and clear the module cache.

        This should be called when the base directory of the project changes, to
        ensure modules are loaded from the correct location and stale cached modules
        are discarded.

        Args:
            module_path: List of directories to search for modules
        """
        self._module_path = module_path

        # Clear the cache since modules from the old path are no longer valid
        self.clear_module_cache()

        # Also clear the loading stack to ensure clean state
        self.loading_stack.clear()

    def module_path(self) -> list[str]:
        """
        Get the current module search path.

        Returns:
            List of directories in the module search path
        """
        return self._module_path
