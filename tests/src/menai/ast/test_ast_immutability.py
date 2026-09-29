"""
Tests that the AST model is immutable and that AST passes do not mutate input.

The AST is a value: a pass returns a new tree rather than mutating the tree it
was given.  This is what makes a pass safe to run more than once and what makes
the AST a viable interface for passes written in Menai (see ADR-0034).

Two properties are checked:

1. Model immutability — a node's sequence fields are tuples, so they cannot be
   mutated in place.
2. Pass purity — running a pass leaves the input tree structurally identical.

The lexer and parser are not covered here: they are stateful and are the
remaining front-end work (see HANDOFF.md).  The passes covered are the ones
downstream of parsing.
"""

import pytest

from menai.ast.menai_ast import (
    MenaiASTDict,
    MenaiASTList,
    MenaiASTNode,
    MenaiASTStruct,
)
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer


_SOURCES = {
    "const-fold": "(integer+ 1 2)",
    "let-star": "(let* ((x 1) (y (integer+ x 1))) (integer+ x y))",
    "match": "(lambda (n) (match n (0 100) (1 200) (_ 0)))",
    "and-or": "(lambda (a b) (and a (or b #t)))",
    "list-literal": "(list 1 2 3)",
    "struct": "(let ((point (struct (x y)))) (point 1 2))",
}


def _parse(source: str) -> MenaiASTNode:
    """Lex and parse source to an AST."""
    tokens = MenaiLexer().lex(source)
    return MenaiASTBuilder().build(tokens, source, "")


def _resolved_ast(source: str) -> MenaiASTNode:
    """Parse and semantically analyse source, ready for the later passes."""
    ast = _parse(source)
    return MenaiASTSemanticAnalyzer().analyze(ast, source)


def _snapshot(node: MenaiASTNode) -> str:
    """
    Render an AST to a string capturing its value-level structure.

    Any change to a node's fields, or to the shape of the tree, changes this
    string.  AST nodes are dataclasses, so reprs recurse deterministically and
    no addresses appear.
    """
    return repr(node)


class TestModelIsImmutable:
    """
    An AST node is a value: its sequence fields are tuples, so they cannot be
    mutated in place.
    """

    def test_list_elements_is_a_tuple(self):
        """A list node's element sequence is a tuple."""
        node = MenaiASTList(elements=(_parse("1"), _parse("2")))

        assert isinstance(node.elements, tuple)
        assert not hasattr(node.elements, "append")

    def test_dict_pairs_is_a_tuple(self):
        """A dict node's pair sequence is a tuple."""
        node = MenaiASTDict(pairs=((_parse("1"), _parse("2")),))

        assert isinstance(node.pairs, tuple)
        assert not hasattr(node.pairs, "append")

    def test_struct_field_names_is_a_tuple(self):
        """A struct node's field-name sequence is a tuple."""
        node = MenaiASTStruct(name="point", tag=0, field_names=("x", "y"))

        assert isinstance(node.field_names, tuple)
        assert not hasattr(node.field_names, "append")


class TestPassesDoNotMutateInput:
    """A pass must leave the AST it was handed unchanged."""

    @pytest.mark.parametrize(
        "name,run,source_key",
        [
            ("SemanticAnalyzer", lambda ast, src: MenaiASTSemanticAnalyzer().analyze(ast, src), "const-fold"),
            ("ModuleResolver", lambda ast, src: MenaiASTModuleResolver(None).resolve_program(ast), "const-fold"),
            ("Desugarer", lambda ast, src: MenaiASTDesugarer().desugar(ast), "let-star"),
            ("Desugarer-match", lambda ast, src: MenaiASTDesugarer().desugar(ast), "match"),
            ("Desugarer-and-or", lambda ast, src: MenaiASTDesugarer().desugar(ast), "and-or"),
            ("PreludeInjector", lambda ast, src: MenaiASTPreludeInjector.wrap(ast), "const-fold"),
            ("ConstantFolder", lambda ast, src: MenaiASTConstantFolder().optimize(ast), "const-fold"),
            ("ConstantFolder-list", lambda ast, src: MenaiASTConstantFolder().optimize(ast), "list-literal"),
            ("ConstantFolder-struct", lambda ast, src: MenaiASTConstantFolder().optimize(ast), "struct"),
        ],
        ids=[
            "SemanticAnalyzer",
            "ModuleResolver",
            "Desugarer",
            "Desugarer-match",
            "Desugarer-and-or",
            "PreludeInjector",
            "ConstantFolder",
            "ConstantFolder-list",
            "ConstantFolder-struct",
        ],
    )
    def test_pass_does_not_mutate_input(self, name, run, source_key):
        """
        Running a pass leaves the input AST structurally identical.

        The pass may return a new tree; it must not modify the one it received.
        """
        ast = _resolved_ast(_SOURCES[source_key])
        before = _snapshot(ast)

        run(ast, _SOURCES[source_key])

        assert _snapshot(ast) == before, f"{name} mutated its input AST"
