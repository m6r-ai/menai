"""Tests for missing parenthesis error detection and reporting."""

import pytest
from menai import Menai, MenaiASTBuildError, MenaiEvalError


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


class TestEarlyBindingsClose:
    """
    A bindings list closed one binding too early is reported at that ')'.

    When a ')' that should have closed a binding's value instead closes the
    bindings list, the remaining bindings are read as body expressions and the
    parser reports a spurious "second body expression" at one of them.  The
    message must point at the misplaced ')' — the line the reader has to change
    — not at the form the parser happened to flag.
    """

    def test_early_close_reports_the_closing_paren(self, menai):
        """The error location is the ')' that closed the bindings list early."""
        source = (
            "(letrec\n"
            "  ((a (lambda () 1))\n"
            "   (b (lambda () 2)))\n"
            "  (c (lambda () 3))\n"
            "  (d (lambda () 4))\n"
            "  (export a))\n"
        )
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate(source)

        error = exc_info.value
        assert error.line == 3
        assert error.column == 21
        assert "bindings list closed early" in str(error)

    def test_early_close_includes_depth_table(self, menai):
        """The message includes the per-line depth profile."""
        source = (
            "(letrec\n"
            "  ((a (lambda () 1))\n"
            "   (b (lambda () 2)))\n"
            "  (c (lambda () 3))\n"
            "  (d (lambda () 4))\n"
            "  (export a))\n"
        )
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate(source)

        error = str(exc_info.value)
        assert "Parenthesis depth by line" in error
        assert "2->1" in error

    def test_early_close_suggests_moving_the_paren(self, menai):
        """The suggestion names the ')' to move and where it belongs."""
        source = (
            "(letrec\n"
            "  ((a (lambda () 1))\n"
            "   (b (lambda () 2)))\n"
            "  (c (lambda () 3))\n"
            "  (d (lambda () 4))\n"
            "  (export a))\n"
        )
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate(source)

        error = str(exc_info.value)
        assert "Move the ')' at line 3, column 21" in error
        assert "wrong place" in error

    def test_genuine_second_body_is_not_an_early_close(self, menai):
        """
        A genuine second body expression keeps the original message.

        ``(let ((x 1)) x x)`` has two body expressions and no misplaced ')'.
        The extra form is not binding-shaped, so it must not be reported as an
        early bindings-list close.
        """
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((x 1)) x x)")

    def test_genuine_second_body_with_call_shape(self, menai):
        """
        A body that looks like a binding does not trigger the early-close error.

        ``(let ((x 1)) (f x) (g x))`` has a first body ``(f x)`` that is
        binding-shaped, but the final form ``(g x)`` is the genuine second body,
        so this is not an early close.
        """
        with pytest.raises(MenaiASTBuildError, match="let body must be a single expression"):
            menai.evaluate("(let ((x 1)) (integer+ x 1) (integer+ x 2))")


class TestEnclosingFormClosedEarly:
    """
    A structural error caused by a ')' that closed an enclosing form early is
    reported at that ')'.

    A form with the wrong number of elements is often the symptom of a ')' that
    closed an enclosing form before its body, so the elements that follow are
    read as further elements of the form.  The message must point at the ')' to
    change, not at the form the analyzer happened to flag.
    """

    def test_if_arity_error_points_at_enclosing_close(self, menai):
        """
        An 'if' with four arguments because a 'let' closed early is reported at
        the ')' that closed the 'let'.

        The 'let' at line 2 has no body before its ')' at line 2, so the 'if'
        absorbs the following expressions as further arguments.
        """
        source = (
            "(if #t\n"
            "    (let ((x 5)))\n"
            "    (integer+ x 1)\n"
            "    (integer+ x 2))\n"
        )
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate(source)

        error = exc_info.value
        assert error.line == 2
        assert "'let' closed before its body" in str(error)

    def test_enclosing_close_includes_depth_table(self, menai):
        """The message includes the per-line depth profile."""
        source = (
            "(if #t\n"
            "    (let ((x 5)))\n"
            "    (integer+ x 1)\n"
            "    (integer+ x 2))\n"
        )
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate(source)

        error = str(exc_info.value)
        assert "Parenthesis depth by line" in error

    def test_genuine_if_arity_error_is_not_an_early_close(self, menai):
        """
        A genuine 'if' arity error keeps the original message.

        ``(if 1 2 3 4)`` has four arguments but no enclosing form closed early,
        so it must not be reported as an early close.
        """
        with pytest.raises(MenaiEvalError, match="If expression has wrong number of arguments"):
            menai.evaluate("(if 1 2 3 4)")

    def test_genuine_letrec_too_few_elements_is_not_an_early_close(self, menai):
        """
        A 'letrec' with too few elements keeps the original message.

        ``(letrec ())`` has no body, but the 'letrec' itself is the form that
        closed early, so this is its own structural error, not a misplaced ')'.
        """
        with pytest.raises(MenaiEvalError, match="Letrec expression structure is incorrect"):
            menai.evaluate("(letrec ())")

    def test_binding_third_element_points_at_enclosing_close(self, menai):
        """
        A binding read with a third element because a 'let' closed early is
        reported at the ')' that closed the 'let'.

        The inner 'let' at line 2 has no body before its ')', so the following
        expression lands as a third element of binding 'b'.
        """
        source = (
            "(let ((a 1)\n"
            "      (b (let ((c 2))) (integer+ c 1))\n"
            "  (integer+ a b))\n"
        )
        with pytest.raises(MenaiASTBuildError) as exc_info:
            menai.evaluate(source)

        error = exc_info.value
        assert error.line == 2
        assert error.column == 22
        assert "'let' closed before its body" in str(error)

    def test_nested_structural_error_points_at_enclosing_close(self, menai):
        """
        A structural error in a nested form is still reported at the misplaced
        ')'.

        The 'if' at line 2 is inside the outer 'let'.  The inner 'let' at line 3
        closes early, so the 'if' absorbs its body as a fourth argument.  The
        diagnosis must survive the recursive descent into the outer 'let' body.
        """
        source = (
            "(let ((x 5))\n"
            "  (if #t\n"
            "      (let ((y 1)))\n"
            "      (integer+ y 1)\n"
            "      (integer+ x 2)))\n"
        )
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate(source)

        error = exc_info.value
        assert error.line == 3
        assert "'let' closed before its body" in str(error)


@pytest.fixture
def menai():
    """Create a fresh Menai instance for each test."""
    return Menai()
