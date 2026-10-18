"""Tests for let and letrec binding semantics.

Menai has two binding forms:

- ``let`` binds in parallel: no binding value may reference a sibling, and the
  body sees every name.
- ``letrec`` binds recursively: every binding name is in scope for every binding
  value and for the body.  This subsumes the sequential-binding behaviour that
  other Lisps spell ``let*``, so a chain of dependent bindings is written as a
  single ``letrec``.
"""

import pytest

from menai import MenaiASTBuildError, MenaiEvalError


class TestLetrecSequentialBindings:
    """Test letrec binding semantics where later bindings reference earlier ones."""

    def test_letrec_sequential_bindings(self, menai, helpers):
        """A later binding may reference an earlier one."""
        helpers.assert_evaluates_to(menai, "(letrec ((x 5) (y (integer* x 2))) (integer+ x y))", "15")

        helpers.assert_evaluates_to(menai, "(letrec ((a 1) (b (integer+ a 1)) (c (integer+ b 1))) (integer+ a b c))", "6")

        helpers.assert_evaluates_to(menai, "(letrec ((x 10) (y (integer+ x 5)) (z (integer* y 2))) z)", "30")

    def test_letrec_empty_bindings(self, menai, helpers):
        """letrec with no bindings is valid."""
        helpers.assert_evaluates_to(menai, "(letrec () 42)", "42")
        helpers.assert_evaluates_to(menai, "(letrec () (integer+ 1 2))", "3")

    def test_letrec_single_binding(self, menai, helpers):
        """letrec with a single binding."""
        helpers.assert_evaluates_to(menai, "(letrec ((x 10)) (integer* x x))", "100")
        helpers.assert_evaluates_to(menai, "(letrec ((s \"hello\")) (string-upcase s))", '"HELLO"')

    def test_letrec_shadowing_outer_binding(self, menai, helpers):
        """A letrec binding shadows an enclosing binding of the same name."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 5)) (letrec ((x 10) (y (integer* x 2))) y))",
            "20"
        )

    def test_letrec_rejects_duplicate_binding_names(self, menai):
        """letrec binding names must be unique within one form.

        A recursive group defines each name once, so a repeated name is
        ambiguous rather than a shadow.  Nest a letrec to shadow.
        """
        with pytest.raises(MenaiEvalError, match="Letrec binding variables must be unique"):
            menai.evaluate("(letrec ((x 1) (x (integer+ x 10))) x)")

    def test_nested_letrec_shadows(self, menai, helpers):
        """A letrec binding shadows an enclosing let binding of the same name."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 1)) (letrec ((x 10)) x))",
            "10"
        )

    def test_nested_letrec_cannot_shadow_same_name(self, menai):
        """A nested letrec cannot shadow a letrec binding of the same name.

        The inner group's own binding wins, so a value referring to that name is
        a self-reference rather than a reference to the outer binding.  Bind the
        outer value under a different name to pass it down.
        """
        with pytest.raises(MenaiEvalError, match="cannot refer to itself"):
            menai.evaluate("(letrec ((x 1)) (letrec ((x (integer+ x 10))) x))")

    def test_letrec_with_lambdas(self, menai, helpers):
        """letrec with lambda expressions."""
        helpers.assert_evaluates_to(
            menai,
            "(letrec ((f (lambda (x) (integer* x 2))) (result (f 5))) result)",
            "10"
        )

        helpers.assert_evaluates_to(
            menai,
            "(letrec ((x 10) (f (lambda (y) (integer+ x y)))) (f 5))",
            "15"
        )

    def test_letrec_independent_bindings_match_nested_lets(self, menai):
        """A letrec of independent bindings evaluates like nested lets."""
        letrec_result = menai.evaluate("(letrec ((x 1) (y 2) (z 3)) (integer+ x y z))")

        nested_let_result = menai.evaluate(
            "(let ((x 1)) (let ((y 2)) (let ((z 3)) (integer+ x y z))))"
        )

        assert letrec_result == nested_let_result

    def test_letrec_with_complex_expressions(self, menai, helpers):
        """letrec with complex nested expressions."""
        helpers.assert_evaluates_to(
            menai,
            """(letrec ((nums (list 1 2 3 4 5))
                      (doubled (map-list (lambda (x) (integer* x 2)) nums))
                      (sum (fold-list integer+ 0 doubled)))
                sum)""",
            "30"
        )

    def test_letrec_in_recursion(self, menai, helpers):
        """letrec used within recursive functions."""
        helpers.assert_evaluates_to(
            menai,
            """(letrec ((factorial
                       (lambda (n)
                         (letrec ((is-base (integer<=? n 1))
                                (result (if is-base 1 (integer* n (factorial (integer- n 1))))))
                           result))))
                 (factorial 5))""",
            "120"
        )

    def test_nested_let_and_letrec(self, menai, helpers):
        """let and letrec may be nested in either order."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 10)) (letrec ((y x) (z (integer* y 2))) z))",
            "20"
        )
        helpers.assert_evaluates_to(
            menai,
            "(letrec ((x 10)) (let ((y 5) (z 3)) (integer+ x y z)))",
            "18"
        )

    def test_letrec_error_messages(self, menai):
        """letrec reports structural errors clearly."""
        with pytest.raises(MenaiEvalError, match="Letrec expression structure is incorrect"):
            menai.evaluate("(letrec ((x 1)))")  # Missing body

        with pytest.raises(MenaiEvalError, match="Letrec binding list must be a list"):
            menai.evaluate("(letrec x 42)")

        with pytest.raises(MenaiASTBuildError, match="letrec binding .* must be a \\(name value\\) form"):
            menai.evaluate("(letrec (x) 42)")

    def test_letrec_self_reference_is_compile_error(self, menai):
        """A non-lambda binding referring to itself is rejected before execution."""
        with pytest.raises(MenaiEvalError, match="cannot refer to itself"):
            menai.evaluate("(letrec ((x (integer+ x 1))) x)")

    def test_letrec_self_reference_through_lambda_is_allowed(self, menai):
        """A self-reference shielded by a lambda is deferred and therefore valid."""
        assert menai.evaluate("(list? (letrec ((x (list (lambda () x)))) x))") is True


class TestLetParallelBindings:
    """Test that let binds in parallel."""

    def test_let_parallel_bindings_cannot_reference_each_other(self, menai):
        """A let binding value cannot reference a sibling."""
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate("(let ((x 5) (y (integer* x 2))) (integer+ x y))")

        assert "Unbound variable" in exc_info.value.message

    def test_let_parallel_bindings_all_use_outer_scope(self, menai, helpers):
        """Every let binding value sees the enclosing scope."""
        helpers.assert_evaluates_to(
            menai,
            "(let ((x 10)) (let ((x 1) (y x)) (integer+ x y)))",
            "11"  # x=1, y=10 (from outer scope)
        )

    def test_let_parallel_allows_independent_bindings(self, menai, helpers):
        """Parallel let works with independent bindings."""
        helpers.assert_evaluates_to(menai, "(let ((x 5) (y 10)) (integer+ x y))", "15")
        helpers.assert_evaluates_to(menai, "(let ((a 1) (b 2) (c 3)) (integer+ a b c))", "6")

    def test_let_rejects_duplicate_binding_names(self, menai):
        """let binding names must be unique."""
        with pytest.raises(MenaiEvalError, match="Let binding variables must be unique"):
            menai.evaluate("(let ((x 1) (x 2)) x)")


class TestLetVersusLetrec:
    """Test the difference between let and letrec binding scope."""

    def test_letrec_binding_value_sees_new_binding(self, menai):
        """In letrec, a binding value sees the letrec's own bindings."""
        outer_x = 100
        result = menai.evaluate(
            f"(let ((x {outer_x})) (letrec ((x 5) (y x)) y))"
        )
        assert result == 5

    def test_let_binding_value_sees_outer_binding(self, menai):
        """In let, a binding value sees the enclosing scope only."""
        outer_x = 100
        result = menai.evaluate(
            f"(let ((x {outer_x})) (let ((x 5) (y x)) y))"
        )
        assert result == outer_x
