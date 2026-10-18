"""An empty binding () is rejected when it is parsed."""

import pytest

from menai import MenaiLexer, MenaiASTBuilder, MenaiASTBuildError


def test_empty_binding_in_let():
    """An empty binding () is not a (name value) form, so it is rejected."""
    lexer = MenaiLexer()
    tokens = lexer.lex("(let (() 5))")
    ast_builder = MenaiASTBuilder()

    with pytest.raises(MenaiASTBuildError, match="let binding 1 must be a"):
        ast_builder.build(tokens, "(let (() 5))")


def test_multiple_bindings_with_empty():
    """An empty binding among valid ones is still rejected."""
    lexer = MenaiLexer()
    tokens = lexer.lex("(let ((x 5) () (y 10)) 42)")
    ast_builder = MenaiASTBuilder()

    with pytest.raises(MenaiASTBuildError, match="let binding 2 must be a"):
        ast_builder.build(tokens, "(let ((x 5) () (y 10)) 42)")
