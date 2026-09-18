"""
Menai Prelude Injector Pass - splices the prelude into a program as lexical bindings.

The prelude is a Menai source file whose top-level form is a ``letrec`` whose
bindings define every prelude function and whose body is a dict of exports.
This pass takes that ``letrec``'s bindings and wraps the user program in them::

    (letrec ((map-list ...) (integer+ ...) ...)
      <user program>)

The prelude's own body (the export dict) is discarded — the program needs the
bindings, not the dict.

The result is that every prelude name is an ordinary lexical binding in the
program.  No prelude-specific resolution exists anywhere downstream: the IR
inliner resolves prelude functions through its ordinary scope stack, the CFG
builder emits local reads rather than global reads, and the interprocedural
type analysis sees the prelude's functions as ordinary closures whose call
sites are all present in the program.

Injection happens once, at the top level, before module resolution.  Module
ASTs are inlined into the parent by the module resolver, so resolving modules
after injection places them inside the prelude's lexical scope and they see
the prelude bindings without carrying their own copy.

The prelude is loaded through lexing, parsing, and semantic analysis but not
module resolution: it must not import anything, because an import would be
resolved against the wrong search path and would reintroduce the duplication
this pass exists to avoid.
"""

from importlib.resources import files

from menai.ast.menai_ast import MenaiASTList, MenaiASTNode, MenaiASTSymbol
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.menai_error import MenaiCodegenError


class MenaiASTPreludeInjector:
    """
    Wraps a program in the prelude's lexical bindings.

    The prelude is loaded and compiled to an analysed AST once and cached on
    the class, since every compilation injects the same prelude.
    """

    _prelude_bindings: MenaiASTList | None = None

    @classmethod
    def prelude_source(cls) -> str:
        """Return the prelude's source text."""
        return (files("menai") / "prelude.menai").read_text()

    def inject(self, program: MenaiASTNode) -> MenaiASTNode:
        """
        Return the program wrapped in the prelude's lexical bindings.

        Args:
            program: The user program's AST (lexed, parsed, and analysed).

        Returns:
            A ``letrec`` whose bindings are the prelude's and whose body is the
            program.
        """
        bindings = self._load_prelude_bindings()
        return MenaiASTList((
            MenaiASTSymbol('letrec'),
            bindings,
            program,
        ))

    @classmethod
    def _load_prelude_bindings(cls) -> MenaiASTList:
        """
        Load the prelude and return its top-level ``letrec`` binding list.

        The prelude's AST is cached on the class: it is identical for every
        compilation.

        Raises:
            MenaiCodegenError: If the prelude's top-level form is not a
                ``letrec``, or if it contains an import.  Both are invariants
                this pass depends on, so they fail loudly rather than producing
                a silently broken program.
        """
        if cls._prelude_bindings is None:
            source = (files("menai") / "prelude.menai").read_text()
            tokens = MenaiLexer().lex(source)
            ast = MenaiASTBuilder().build(tokens, source, "<prelude>")
            analysed = MenaiASTSemanticAnalyzer().analyze(ast, source)

            if not cls._is_letrec(analysed):
                raise MenaiCodegenError(
                    message="The prelude's top-level form is not a letrec",
                    context="The prelude injector splices the prelude's letrec bindings into every program",
                    suggestion="Ensure prelude.menai is a single top-level letrec form",
                )

            assert isinstance(analysed, MenaiASTList)
            bindings = analysed.elements[1]
            assert isinstance(bindings, MenaiASTList)

            if cls._contains_import(bindings):
                raise MenaiCodegenError(
                    message="The prelude contains an import",
                    context="The prelude is injected before module resolution, so its imports cannot be resolved",
                    suggestion="Remove the import from prelude.menai, or resolve it before injection",
                )

            cls._prelude_bindings = bindings

        return cls._prelude_bindings

    @staticmethod
    def _is_letrec(expr: MenaiASTNode) -> bool:
        """Return True if the expression is a ``(letrec (...) body)`` form."""
        return (
            isinstance(expr, MenaiASTList)
            and len(expr.elements) == 3
            and isinstance(expr.elements[0], MenaiASTSymbol)
            and expr.elements[0].name == 'letrec'
            and isinstance(expr.elements[1], MenaiASTList)
        )

    @staticmethod
    def _contains_import(expr: MenaiASTNode) -> bool:
        """Return True if the expression contains an ``import`` form anywhere."""
        if isinstance(expr, MenaiASTList):
            if not expr.is_empty():
                first = expr.first()
                if isinstance(first, MenaiASTSymbol) and first.name == 'import':
                    return True

            return any(MenaiASTPreludeInjector._contains_import(elem) for elem in expr.elements)

        return False
