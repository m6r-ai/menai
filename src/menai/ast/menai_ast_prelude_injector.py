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

The prelude is desugared once and cached as a list of wrapper forms.  Because
the prelude is identical for every compilation, re-desugaring it on every
compile is pure overhead; instead the prelude's desugared ``let``/``letrec``
wrappers are cached and folded around the already-desugared user program.  The
desugared wrappers are immutable, so they are safely shared across
compilations.
"""

from importlib.resources import files

from menai.ast.menai_ast import MenaiASTList, MenaiASTNode, MenaiASTSymbol
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.menai_error import MenaiCodegenError


class MenaiASTPreludeInjector:
    """
    Wraps a program in the prelude's lexical bindings.

    The prelude is loaded, analysed, and desugared once and cached on the class,
    since every compilation injects the same prelude.
    """

    _prelude_wrappers: list[tuple[MenaiASTSymbol, MenaiASTList]] = []
    _prelude_temp_count: int = 0
    _prelude_loaded: bool = False

    @classmethod
    def prelude_source(cls) -> str:
        """Return the prelude's source text."""
        return (files("menai") / "prelude.menai").read_text()

    @classmethod
    def wrap(cls, program: MenaiASTNode) -> MenaiASTNode:
        """
        Return the program wrapped in the prelude's desugared lexical bindings.

        Args:
            program: The user program's desugared AST.

        Returns:
            The prelude's desugared wrappers folded around the program.
        """
        wrappers = cls._load_prelude_wrappers()
        result = program
        for kind, bindings in wrappers[::-1]:
            result = MenaiASTList((kind, bindings, result))

        return result

    @classmethod
    def prelude_temp_count(cls) -> int:
        """
        Return the number of temporary names the prelude's desugaring consumed.

        The user program's desugaring must start its temporary counter above
        this so its generated names cannot collide with the prelude's.
        """
        cls._load_prelude_wrappers()
        return cls._prelude_temp_count

    @classmethod
    def prelude_names(cls) -> frozenset[str]:
        """
        Return the set of names the prelude binds.

        These are the names every program can call without importing anything.
        A host-injected binding must not use one of them, because it would
        shadow the prelude binding and break every prelude call in the program.
        """
        names: set[str] = set()
        for _, bindings in cls._load_prelude_wrappers():
            for binding in bindings.elements:
                assert isinstance(binding, MenaiASTList) and len(binding.elements) == 2
                name_expr = binding.elements[0]
                assert isinstance(name_expr, MenaiASTSymbol)
                names.add(name_expr.name)

        return frozenset(names)

    @classmethod
    def _load_prelude_wrappers(cls) -> list[tuple[MenaiASTSymbol, MenaiASTList]]:
        """
        Load the prelude and return its desugared ``let``/``letrec`` wrappers.

        The prelude's AST is cached on the class: it is identical for every
        compilation.

        Raises:
            MenaiCodegenError: If the prelude's top-level form is not a
                ``letrec``, or if it contains an import.  Both are invariants
                this pass depends on, so they fail loudly rather than producing
                a silently broken program.
        """
        if not cls._prelude_loaded:
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

            desugarer = MenaiASTDesugarer()
            desugared = desugarer.desugar(analysed)
            cls._prelude_temp_count = desugarer.temp_counter
            cls._prelude_wrappers = cls._collect_wrappers(desugared)
            cls._prelude_loaded = True

        return cls._prelude_wrappers

    @staticmethod
    def _collect_wrappers(desugared: MenaiASTNode) -> list[tuple[MenaiASTSymbol, MenaiASTList]]:
        """
        Collect the desugared prelude's ``let``/``letrec`` wrappers.

        Desugaring the prelude's top-level ``letrec`` produces a chain of
        ``let``/``letrec`` forms whose innermost body is the (discarded) export
        dict.  The wrappers are returned outermost first; each is a
        ``(kind-symbol, bindings-list)`` pair.
        """
        wrappers: list[tuple[MenaiASTSymbol, MenaiASTList]] = []
        node = desugared
        while (
            isinstance(node, MenaiASTList)
            and len(node.elements) == 3
            and isinstance(node.elements[0], MenaiASTSymbol)
            and node.elements[0].name in ('let', 'letrec')
            and isinstance(node.elements[1], MenaiASTList)
        ):
            wrappers.append((node.elements[0], node.elements[1]))
            node = node.elements[2]

        return wrappers

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
