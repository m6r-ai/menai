"""
Tests for the rejection of reserved words as binding names.

A reserved word is syntax, not a name: a form headed by one is interpreted as
that form whatever any enclosing binding says, so a binding of the same name
could never be referenced.  Every binding site rejects the name at compile time.
"""

import re

import pytest

from menai import MenaiEvalError
from menai.ast.menai_ast_semantic_analyzer import RESERVED_NAMES


class TestReservedNamesInBindingForms:
    """let, let* and letrec reject a reserved word as a binding name."""

    @pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
    def test_let_rejects_reserved_binding_name(self, menai, name):
        """(let ((<reserved> 5)) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match=re.escape(f"Binding name '{name}' is a reserved word")):
            menai.evaluate(f"(let (({name} 5)) {name})")

    @pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
    def test_let_star_rejects_reserved_binding_name(self, menai, name):
        """(let* ((<reserved> 5)) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match=re.escape(f"Binding name '{name}' is a reserved word")):
            menai.evaluate(f"(let* (({name} 5)) {name})")

    @pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
    def test_letrec_rejects_reserved_binding_name(self, menai, name):
        """(letrec ((<reserved> 5)) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match=re.escape(f"Binding name '{name}' is a reserved word")):
            menai.evaluate(f"(letrec (({name} 5)) {name})")

    def test_error_points_at_the_binding_name(self, menai):
        """The error location is the offending name, not the enclosing form."""
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate("(let ((match 5)) match)")

        assert exc_info.value.line == 1
        assert exc_info.value.column == 8

    def test_reserved_name_in_later_binding_is_reported(self, menai):
        """A reserved name in the second binding is still rejected."""
        with pytest.raises(MenaiEvalError, match="Binding name 'if' is a reserved word"):
            menai.evaluate("(let ((x 1) (if 2)) x)")

    def test_non_reserved_binding_name_is_accepted(self, menai):
        """An ordinary name that merely resembles syntax is accepted."""
        assert menai.evaluate("(let ((loop 5)) (integer+ loop 1))") == 6


class TestReservedNamesInLambdaParameters:
    """lambda rejects a reserved word as a parameter name."""

    @pytest.mark.parametrize("name", sorted(RESERVED_NAMES - {'.'}))
    def test_lambda_rejects_reserved_parameter_name(self, menai, name):
        """(lambda (<reserved>) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match=re.escape(f"Binding name '{name}' is a reserved word")):
            menai.evaluate(f"(lambda ({name}) {name})")

    def test_lambda_rest_parameter_rejects_reserved_name(self, menai):
        """A reserved name after the dot is rejected."""
        with pytest.raises(MenaiEvalError, match="Binding name 'match' is a reserved word"):
            menai.evaluate("(lambda (. match) match)")

    def test_non_reserved_parameter_name_is_accepted(self, menai):
        """An ordinary parameter name is accepted."""
        assert menai.evaluate("((lambda (loop) (integer+ loop 1)) 5)") == 6


class TestReservedNamesInMatchPatterns:
    """match rejects a reserved word as a pattern variable."""

    @pytest.mark.parametrize("name", sorted(RESERVED_NAMES - {'.'}))
    def test_match_rejects_reserved_pattern_variable(self, menai, name):
        """A reserved word as a bare pattern variable is a compile-time error."""
        with pytest.raises(MenaiEvalError, match=re.escape(f"Binding name '{name}' is a reserved word")):
            menai.evaluate(f"(match 1 ({name} {name}) (_ 0))")

    def test_match_rejects_reserved_name_in_predicate_pattern(self, menai):
        """A reserved word as a predicate pattern variable is rejected."""
        with pytest.raises(MenaiEvalError, match="Binding name 'match' is a reserved word"):
            menai.evaluate("(match 1 ((? integer? match) match) (_ 0))")

    def test_match_rejects_reserved_name_in_nested_pattern(self, menai):
        """A reserved word nested inside a list pattern is rejected."""
        with pytest.raises(MenaiEvalError, match="Binding name 'if' is a reserved word"):
            menai.evaluate("(match (list 1 2) ((x if) x) (_ 0))")

    def test_quoted_symbol_in_pattern_is_not_a_binding(self, menai):
        """A quoted variant in an enum pattern binds nothing and is not walked as a list."""
        assert menai.evaluate_and_format(
            "(let ((A (enum (idle)))"
            "      (B (enum (idle))))"
            "  (match (B 'idle)"
            "    ((A 'idle) \"a\")"
            "    (_ \"other\")))"
        ) == '"other"'

    def test_non_reserved_pattern_variable_is_accepted(self, menai):
        """An ordinary pattern variable name is accepted."""
        assert menai.evaluate("(match 1 (loop (integer+ loop 1)) (_ 0))") == 2


class TestReservedNamesInStructAndEnumBindings:
    """struct and enum type names reject a reserved word."""

    def test_struct_type_name_rejects_reserved_word(self, menai):
        """(let ((match (struct (x)))) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match="Binding name 'match' is a reserved word"):
            menai.evaluate("(let ((match (struct (x)))) match)")

    def test_enum_type_name_rejects_reserved_word(self, menai):
        """(let ((if (enum (a)))) ...) is a compile-time error."""
        with pytest.raises(MenaiEvalError, match="Binding name 'if' is a reserved word"):
            menai.evaluate("(let ((if (enum (a)))) if)")

    def test_ordinary_struct_type_name_is_accepted(self, menai):
        """A non-reserved struct type name is accepted."""
        assert menai.evaluate("(let ((Point (struct (x)))) (struct-get (Point 3) 'x))") == 3


class TestReservedNamesInInjectedBindings:
    """A host-supplied binding name rejects a reserved word."""

    def test_inject_rejects_reserved_name(self):
        """MenaiASTBindingInjector rejects a reserved word as a binding name."""
        from menai.ast.menai_ast_binding_injector import MenaiASTBindingInjector
        from menai.menai_error import MenaiCodegenError

        with pytest.raises(MenaiCodegenError, match="Binding name 'match' is a reserved word"):
            MenaiASTBindingInjector._validate_name("match")

    def test_inject_accepts_ordinary_name(self):
        """MenaiASTBindingInjector accepts an ordinary binding name."""
        from menai.ast.menai_ast_binding_injector import MenaiASTBindingInjector

        MenaiASTBindingInjector._validate_name("inputs")


class TestReservedNameSet:
    """The reserved-name set covers every form head the compiler dispatches on."""

    @pytest.mark.parametrize("name", [
        "::", "if", "let", "let*", "letrec", "lambda", "quote", "match",
        "and", "or", "import", "struct", "enum", "export", "apply", ":",
    ])
    def test_reserved_name_is_in_the_set(self, name):
        """Each syntax head is a member of RESERVED_NAMES."""
        assert name in RESERVED_NAMES
