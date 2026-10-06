"""Tests for missing parenthesis error detection and reporting."""

import pytest
from menai import Menai, MenaiASTBuildError


class TestMissingParens:
    """Test enhanced error messages for missing parentheses."""

    def test_simple_missing_closing_paren(self, menai):
        """Test error message for simple missing closing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(+ 1 2")

        error = str(exc_info.value)
        assert "missing 1 closing parenthesis" in error.lower()
        assert "line" in error.lower()
        assert "column" in error.lower()

    def test_nested_missing_closing_parens(self, menai):
        """Test error message for nested missing closing parens."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(+ (* 2 3) (- 5 1")

        error = str(exc_info.value)
        assert "missing 2 closing" in error.lower()
        assert "unclosed expressions" in error.lower()

    def test_let_with_missing_paren_simple(self, menai):
        """Test error for simple let with missing closing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)) (+ x 2")

        error = str(exc_info.value)
        assert "missing" in error.lower()
        assert "closing" in error.lower()

    def test_let_binding_missing_closing_paren(self, menai):
        """Test error when a binding value is missing closing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("""(let (
  (add (lambda (x y) (+ x y)))
  (mul (lambda (x y) (* x y))
  (sub (lambda (x y) (- x y)))
)
  (add 5 3)
)""")

        error = str(exc_info.value)
        # Should mention it's in a let binding context
        assert "let" in error.lower() or "binding" in error.lower()
        # Should give some indication about which binding
        assert "mul" in error.lower() or "binding" in error.lower()

    def test_let_binding_missing_paren_shows_position(self, menai):
        """Test that error shows helpful position information."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((a 1) (b (+ 2 3) (c 4)) (+ a b c))")

        error = str(exc_info.value)
        # Should have position information
        assert "line" in error.lower()
        assert "column" in error.lower()

    def test_deeply_nested_let_bindings(self, menai):
        """A missing close paren inside a binding value is reported at the binding level."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("""(let (
  (x 5)
  (y (let ((a 1) (b 2)) (+ a b))
  (z 10)
)
  (+ x y z)
)""")

        error = str(exc_info.value)
        assert "missing" in error.lower()
        # Should point at the binding with the missing close paren
        assert "binding 'y'" in error

    def test_if_expression_missing_paren(self, menai):
        """Test error for if expression missing closing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(if (> x 5) \"big\" \"small\"")

        error = str(exc_info.value)
        assert "missing" in error.lower()
        assert "if" in error.lower()

    def test_lambda_missing_closing_paren(self, menai):
        """Test error for lambda missing closing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(lambda (x y) (+ x y")

        error = str(exc_info.value)
        assert "missing" in error.lower()
        assert "lambda" in error.lower()

    def test_multiple_missing_parens_shows_count(self, menai):
        """Test that error shows correct count of missing parens."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)) (if (> x 0) (+ x 1")

        error = str(exc_info.value)
        assert "missing 3 closing" in error.lower()

    def test_error_shows_unclosed_expression_list(self, menai):
        """Test that error lists all unclosed expressions."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)) (+ x 2")

        error = str(exc_info.value)
        # Should show both the let and the function call as unclosed
        assert "unclosed expressions" in error.lower()

    def test_error_suggests_closing_parens(self, menai):
        """Test that error suggests the closing parens to add."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(+ 1 (+ 2 3")

        error = str(exc_info.value)
        # Should suggest adding ) )
        assert ")" in error

    def test_binding_with_complex_value_missing_paren(self, menai):
        """Test error when binding has complex value expression missing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("""(let (
  (x 5)
  (y (+ (* 2 3) (- 10 5))
  (z 7)
)
  (+ x y z)
)""")

        error = str(exc_info.value)
        # Should identify it's in a let binding
        assert "let" in error.lower() or "binding" in error.lower()

    def test_last_complete_position_tracking(self, menai):
        """Test that parser tracks where last complete element was."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5) (y 10)) (+ x y")

        error = str(exc_info.value)
        # Should have information about where things are
        assert "line" in error.lower()
        assert "column" in error.lower()

    def test_empty_binding_list_missing_paren(self, menai):
        """Test error for let with empty bindings missing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ( (+ 1 2)")

        error = str(exc_info.value)
        assert "missing" in error.lower()

    def test_single_binding_missing_paren(self, menai):
        """Test error for let with single binding missing paren."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5) (+ x 1))")

        error = str(exc_info.value)
        assert "missing" in error.lower()

    def test_error_message_shows_expression_type(self, menai):
        """Test that error identifies the type of expression (let, lambda, if, etc)."""
        test_cases = [
            ("(let ((x 5)) x", "let"),
            ("(lambda (x) x", "lambda"),
            ("(if (> 1 0) 1 0", "if"),
        ]

        for expr, expected_type in test_cases:
            with pytest.raises(MenaiASTBuildError) as exc_info:
                menai.evaluate(expr)

            error = str(exc_info.value)
            assert expected_type in error.lower(), f"Expected '{expected_type}' in error for: {expr}"

    def test_multiple_levels_of_nesting(self, menai):
        """Test error reporting with multiple levels of nesting."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("""(let (
  (outer 1)
  (middle (let ((inner 2)) inner)
)
  outer
)""")

        error = str(exc_info.value)
        # Should show nested structure
        assert "unclosed" in error.lower()
        assert "let" in error.lower()


class TestMissingCloseLocation:
    """
    A missing ')' is reported where it belongs, not where the form opened.

    The location must be the insertion point — immediately after the last token
    — because that is the line the reader has to change.  Reporting the opening
    paren of the unclosed form sends the reader to the wrong line entirely.
    """

    def test_single_line_reports_end_of_line(self, menai):
        """A missing ')' on a one-line expression is reported at the end of it."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(integer+ 1 2")

        error = exc_info.value
        assert error.line == 1
        assert error.column == 14

    def test_missing_bindings_list_close_reported_after_last_binding(self, menai):
        """
        A missing bindings-list close is reported after the last binding.

        The body being read as a further binding means the bindings list never
        closed, and the absent ')' belongs immediately after the last binding —
        not at the end of the file, which is where the parse ran out of input.
        """
        source = (
            "(letrec\n"
            "  ((a 1)\n"
            "   (b (lambda (n) (integer+ n 1)))\n"
            "  (export b))\n"
        )
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate(source)

        error = exc_info.value
        assert error.line == 3
        assert "immediately after the last binding" in str(error)

    def test_multiline_reports_last_content_line(self, menai):
        """A missing ')' is reported on the last line carrying content."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)\n      (y 10))\n  (integer+ x\n            y\n")

        error = exc_info.value
        assert error.line == 4
        assert error.column == 14

    def test_error_names_the_insertion_point(self, menai):
        """The message states the exact position to insert the missing ')'."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)\n      (y 10))\n  (integer+ x\n            y\n")

        error = str(exc_info.value)
        assert "Insert )) at line 4, column 14" in error

    def test_error_includes_depth_table(self, menai):
        """The message includes the per-line depth profile."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)\n      (y 10))\n  (integer+ x\n            y\n")

        error = str(exc_info.value)
        assert "Parenthesis depth by line" in error
        assert "2->2" in error

    def test_failed_parse_does_not_inflate_next_depth(self, menai):
        """A failed parse leaves no state behind for the next one."""
        with pytest.raises(MenaiASTBuildError):
            menai.evaluate("(integer+ 1 2")

        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)\n      (y 10))\n  (integer+ x\n            y\n")

        assert "missing 2 closing parentheses" in str(exc_info.value)


class TestExtraCloseLocation:
    """An extra ')' is reported at the first ')' that cannot be matched."""

    def test_extra_close_at_end(self, menai):
        """A trailing ')' is reported at its own position."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(integer+ 1 2))")

        error = exc_info.value
        assert error.line == 1
        assert error.column == 15
        assert "Extra closing parenthesis" in str(error)

    def test_extra_close_names_earlier_culprit(self, menai):
        """
        A ')' that closed a form before its body is named as the likely culprit.

        The provable extra ')' is on the last line, but the mistake the reader
        has to fix is the earlier ')' that ended the 'let' early.
        """
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5)\n      (y 10)))\n  (integer+ x y))\n")

        error = str(exc_info.value)
        assert "Likely culprit" in error
        assert "line 2, column 14" in error
        assert "'let'" in error

    def test_balanced_structure_error_points_at_early_close(self, menai):
        """A form closed before its body is reported at that close."""
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate("(let ((x 5))) (integer+ x 1)")

        error = exc_info.value
        assert error.line == 1
        assert error.column == 13
        assert "'let' closed before its body" in str(error)

    def test_two_top_level_expressions_are_not_an_extra_close(self, menai):
        """Two adjacent expressions are reported as such, not as an extra ')'."""
        with pytest.raises(MenaiASTBuildError, match="Unexpected token after complete expression"):
            menai.evaluate("(integer+ 1 2) (integer+ 3 4)")


@pytest.fixture
def menai():
    """Create a fresh Menai instance for each test."""
    return Menai()
