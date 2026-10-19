"""
Tests for previously uncovered branches in MenaiSemanticAnalyzer.
"""

import pytest

from menai import MenaiASTBuildError, MenaiEvalError

class TestBindingFormMultipleBodyExpressions:
    """A binding form accepts exactly one body expression."""

    def test_let_extra_body_expression(self, menai):
        """(let ((x 1)) expr1 expr2) — the second body expression is rejected."""
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((x 1)) x x)")

    def test_let_extra_body_expression_with_plain_binding(self, menai):
        """(let ((x 1)) expr1 expr2) — the second body expression is rejected."""
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((x 1)) x x)")

    def test_let_extra_body_expression(self, menai):
        """(let ((f (lambda () 1))) expr1 expr2) — the second body expression is rejected."""
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((f (lambda () 1))) (f) (f))")

    def test_let_multiple_extra_elements(self, menai):
        """(let ((x 1)) a b c) — the first extra body expression is reported."""
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((x 1)) x x x)")

    def test_let_no_bindings_two_bodies(self, menai):
        """(let () expr1 expr2) — the second body expression is rejected."""
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let () 1 2)")

    def test_error_points_at_extra_body_expression(self, menai):
        """The error location is the extra body expression, not the enclosing form."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 1))\n  x\n  x)")

        assert exc_info.value.line == 3
        assert exc_info.value.column == 3

    def test_single_body_expression_is_accepted(self, menai):
        """(let ((x 1)) (integer+ x 1)) — a single body expression evaluates."""
        assert menai.evaluate("(let ((x 1)) (integer+ x 1))") == 2


class TestLetBindingWrongCount:
    """A let binding must have exactly 2 elements."""

    def test_let_binding_missing_value(self, menai):
        """(let ((x)) body) — binding has only 1 element."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let ((x)) x)")

    def test_let_binding_too_many_values(self, menai):
        """(let ((x 1 2)) body) — parser catches the third element as a missing close paren."""
        with pytest.raises(MenaiASTBuildError, match="Missing closing parenthesis"):
            menai.evaluate("(let ((x 1 2)) x)")

    def test_let_empty_binding(self, menai):
        """(let (()) body) — binding has 0 elements."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let (()) 42)")

    def test_let_second_binding_wrong_count(self, menai):
        """Error reported for the second binding when first is fine."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let ((x 1) (y)) (+ x y))")


class TestLetBindingVarNotSymbol:
    """A let binding variable must be a symbol."""

    def test_let_binding_integer_variable(self, menai):
        """(let ((1 5)) 1) — integer as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate("(let ((1 5)) 1)")

    def test_let_binding_string_variable(self, menai):
        """(let (("x" 5)) x) — string as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate('(let (("x" 5)) "x")')

    def test_let_binding_boolean_variable(self, menai):
        """(let ((#t 5)) #t) — boolean as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate("(let ((#t 5)) #t)")

    def test_let_star_second_binding_non_symbol_variable(self, menai):
        """Error on the second binding's variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate('(let ((x 1) ("y" 2)) x)')


class TestLetTooFewElements:
    """Line 333: let expression with fewer than 3 elements."""

    def test_let_no_bindings_no_body(self, menai):
        """(let) — only the keyword, 1 element."""
        with pytest.raises(MenaiEvalError, match="Let expression structure is incorrect"):
            menai.evaluate("(let)")

    def test_let_bindings_but_no_body(self, menai):
        """(let ()) — keyword + empty bindings, 2 elements, no body."""
        with pytest.raises(MenaiEvalError, match="Let expression structure is incorrect"):
            menai.evaluate("(let ())")


class TestLetBindingsNotList:
    """Line 359: let binding list must be a list."""

    def test_let_symbol_as_bindings(self, menai):
        """(let x body) — symbol in bindings position."""
        with pytest.raises(MenaiEvalError, match="Let binding list must be a list"):
            menai.evaluate("(let x 42)")

    def test_let_integer_as_bindings(self, menai):
        """(let 5 body) — integer in bindings position."""
        with pytest.raises(MenaiEvalError, match="Let binding list must be a list"):
            menai.evaluate("(let 5 42)")

    def test_let_string_as_bindings(self, menai):
        """(let "x" body) — string in bindings position."""
        with pytest.raises(MenaiEvalError, match="Let binding list must be a list"):
            menai.evaluate('(let "x" 42)')


class TestLetIndividualBindingNotList:
    """Line 374: each let binding must be a list."""

    def test_let_bare_symbol_as_binding(self, menai):
        """(let (x 5) body) — 'x' is a symbol, not a (var val) list."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let (x 5) x)")

    def test_let_bare_integer_as_binding(self, menai):
        """(let (42) body) — integer as a binding slot."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let (42) 42)")

    def test_let_bare_string_as_binding(self, menai):
        """(let ("x") body) — string as a binding slot."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate('(let ("x") "x")')

    def test_let_second_binding_not_list(self, menai):
        """Error reported for the second binding slot."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let ((x 1) y) x)")


class TestLetBindingWrongCount:
    """Line 386: each let binding must have exactly 2 elements."""

    def test_let_binding_missing_value(self, menai):
        """(let ((x)) body) — binding has only 1 element."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let ((x)) x)")

    def test_let_binding_too_many_values(self, menai):
        """(let ((x 1 2)) body) — parser catches the third element as a missing close paren."""
        with pytest.raises(MenaiASTBuildError, match="Missing closing parenthesis"):
            menai.evaluate("(let ((x 1 2)) x)")

    def test_let_empty_binding(self, menai):
        """(let (()) body) — binding has 0 elements."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let (()) 42)")

    def test_let_second_binding_wrong_count(self, menai):
        """Error reported for the second binding."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let ((x 1) (y)) x)")


class TestLetBindingVarNotSymbol:
    """Line 400: let binding variable must be a symbol."""

    def test_let_binding_integer_variable(self, menai):
        """(let ((1 5)) 1) — integer as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate("(let ((1 5)) 1)")

    def test_let_binding_string_variable(self, menai):
        """(let (("x" 5)) "x") — string as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate('(let (("x" 5)) "x")')

    def test_let_binding_boolean_variable(self, menai):
        """(let ((#t 5)) #t) — boolean as binding variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate("(let ((#t 5)) #t)")

    def test_let_second_binding_non_symbol_variable(self, menai):
        """Error on the second binding's variable."""
        with pytest.raises(MenaiASTBuildError, match="let binding .* variable must be a symbol"):
            menai.evaluate('(let ((x 1) (2 3)) x)')


class TestLetDuplicateBindings:
    """Line 418: let binding variables must be unique."""

    def test_let_two_identical_bindings(self, menai):
        """(let ((x 1) (x 2)) x) — duplicate 'x'."""
        with pytest.raises(MenaiEvalError, match="Let binding variables must be unique"):
            menai.evaluate("(let ((x 1) (x 2)) x)")

    def test_let_three_bindings_one_duplicate(self, menai):
        """(let ((x 1) (y 2) (x 3)) x) — 'x' appears twice."""
        with pytest.raises(MenaiEvalError, match="Let binding variables must be unique"):
            menai.evaluate("(let ((x 1) (y 2) (x 3)) x)")

    def test_let_all_bindings_same_name(self, menai):
        """(let ((x 1) (x 2) (x 3)) x) — all three are 'x'."""
        with pytest.raises(MenaiEvalError, match="Let binding variables must be unique"):
            menai.evaluate("(let ((x 1) (x 2) (x 3)) x)")
