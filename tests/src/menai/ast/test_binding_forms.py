"""Tests for let binding semantics.

Menai has one binding form, ``let``.  It binds recursively: every binding name is
in scope for every binding value and for the body.  This subsumes the
sequential-binding behaviour that other Lisps spell ``let*``, so a chain of
dependent bindings is written as a single ``let``.  There is no parallel-binding
form, and no ``letrec``.
"""

import pytest

from menai import MenaiASTBuildError, MenaiEvalError


class TestLetSequentialBindings:
    """Test let binding semantics where later bindings reference earlier ones."""

    def test_let_sequential_bindings(self, menai, helpers):
        """A later binding may reference an earlier one."""
        helpers.assert_evaluates_to(menai, "(let ((x 5) (y (integer* x 2))) (integer+ x y))", "15")

        helpers.assert_evaluates_to(menai, "(let ((a 1) (b (integer+ a 1)) (c (integer+ b 1))) (integer+ a b c))", "6")

        helpers.assert_evaluates_to(menai, "(let ((x 10) (y (integer+ x 5)) (z (integer* y 2))) z)", "30")

    def test_let_empty_bindings(self, menai, helpers):
        """let with no bindings is valid."""
        helpers.assert_evaluates_to(menai, "(let () 42)", "42")
        helpers.assert_evaluates_to(menai, "(let () (integer+ 1 2))", "3")

    def test_let_single_binding(self, menai, helpers):
        """let with a single binding."""
        helpers.assert_evaluates_to(menai, "(let ((x 10)) (integer* x x))", "100")
        helpers.assert_evaluates_to(menai, "(let ((s \"hello\")) (string-upcase s))", '"HELLO"')

    def test_let_shadowing_outer_binding(self, menai, helpers):
        """A let binding shadows an enclosing binding of the same name."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 5)) (let ((x 10) (y (integer* x 2))) y))",
            "20"
        )

    def test_let_rejects_duplicate_binding_names(self, menai):
        """let binding names must be unique within one form.

        A recursive group defines each name once, so a repeated name is
        ambiguous rather than a shadow.  Nest a let to shadow.
        """
        with pytest.raises(MenaiEvalError, match="Let binding variables must be unique"):
            menai.evaluate("(let ((x 1) (x (integer+ x 10))) x)")

    def test_nested_let_shadows(self, menai, helpers):
        """A let binding shadows an enclosing let binding of the same name."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 1)) (let ((x 10)) x))",
            "10"
        )

    def test_nested_let_cannot_shadow_same_name_in_value(self, menai):
        """A nested let cannot reference an outer binding of the same name.

        The inner group's own binding wins, so a value referring to that name is
        a self-reference rather than a reference to the outer binding.  Bind the
        outer value under a different name to pass it down.
        """
        with pytest.raises(MenaiEvalError, match="cannot refer to itself"):
            menai.evaluate("(let ((x 1)) (let ((x (integer+ x 10))) x))")

    def test_let_with_lambdas(self, menai, helpers):
        """let with lambda expressions."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((f (lambda (x) (integer* x 2))) (result (f 5))) result)",
            "10"
        )

        helpers.assert_evaluates_to(
            menai,
            "(let ((x 10) (f (lambda (y) (integer+ x y)))) (f 5))",
            "15"
        )

    def test_let_independent_bindings_match_nested_lets(self, menai):
        """A let of independent bindings evaluates like nested lets."""
        flat = menai.evaluate("(let ((x 1) (y 2) (z 3)) (integer+ x y z))")

        nested = menai.evaluate(
            "(let ((x 1)) (let ((y 2)) (let ((z 3)) (integer+ x y z))))"
        )

        assert flat == nested

    def test_let_with_complex_expressions(self, menai, helpers):
        """let with complex nested expressions."""
        helpers.assert_evaluates_to(
            menai,
            """(let ((nums (list 1 2 3 4 5))
                      (doubled (map-list (lambda (x) (integer* x 2)) nums))
                      (sum (fold-list integer+ 0 doubled)))
                sum)""",
            "30"
        )

    def test_let_in_recursion(self, menai, helpers):
        """let used within recursive functions."""
        helpers.assert_evaluates_to(
            menai,
            """(let ((factorial
                       (lambda (n)
                         (let ((is-base (integer<=? n 1))
                               (result (if is-base 1 (integer* n (factorial (integer- n 1))))))
                           result))))
                 (factorial 5))""",
            "120"
        )

    def test_nested_let_forms(self, menai, helpers):
        """let forms may be nested."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 10)) (let ((y x) (z (integer* y 2))) z))",
            "20"
        )
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 10)) (let ((y 5) (z 3)) (integer+ x y z)))",
            "18"
        )

    def test_let_error_messages(self, menai):
        """let reports structural errors clearly."""
        with pytest.raises(MenaiEvalError, match="Let expression structure is incorrect"):
            menai.evaluate("(let ((x 1)))")  # Missing body

        with pytest.raises(MenaiEvalError, match="Let binding list must be a list"):
            menai.evaluate("(let x 42)")

        with pytest.raises(MenaiASTBuildError, match="let binding .* must be a \\(name value\\) form"):
            menai.evaluate("(let (x) 42)")

    def test_let_self_reference_is_compile_error(self, menai):
        """A non-lambda binding referring to itself is rejected before execution."""
        with pytest.raises(MenaiEvalError, match="cannot refer to itself"):
            menai.evaluate("(let ((x (integer+ x 1))) x)")

    def test_let_self_reference_through_lambda_is_allowed(self, menai):
        """A self-reference shielded by a lambda is deferred and therefore valid."""
        assert menai.evaluate("(list? (let ((x (list (lambda () x)))) x))") is True


class TestLetrecIsRemoved:
    """letrec is no longer a surface form."""

    def test_letrec_is_not_a_special_form(self, menai):
        """A (letrec ...) form is not recognised as a binding form."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate("(letrec ((x 1)) x)")
