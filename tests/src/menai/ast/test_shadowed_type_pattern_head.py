"""Tests for resolution of a builtin sequence type pattern head under shadowing.

A type pattern head must name a type.  `list` and `vector` name builtin sequence
types when unshadowed, but they are ordinary binding names, so an enclosing
lexical binder can shadow them with a value that is not a type.  A `(: list ...)`
head that resolves to such a binding names no type at all, so the pattern is
rejected at compile time rather than being resolved to the builtin type the
binding replaced.
"""

import re

import pytest

from menai import Menai, MenaiError


@pytest.fixture
def menai():
    return Menai()


def ev(menai, expr):
    return menai.evaluate_and_format(expr)


class TestShadowedBuiltinTypePatternHead:
    """A shadowed builtin sequence type head is a compile-time error."""

    @pytest.mark.parametrize("type_name", ["list", "vector"])
    def test_shadowed_head_is_rejected(self, menai, type_name):
        """A head naming a binding of the same name is rejected."""
        with pytest.raises(
            MenaiError,
            match=re.escape(f"Type pattern head '{type_name}' is shadowed by a binding"),
        ):
            menai.evaluate(
                f"(let (({type_name} (lambda (a b) \"not-a-{type_name}\")))"
                f"  (match ({type_name} 1 2)"
                f"    ((: {type_name} a b) (integer+ a b))"
                f"    (_ 0)))"
            )

    def test_shadowed_head_error_points_at_the_pattern(self, menai):
        """The error location is the pattern, not the enclosing let."""
        with pytest.raises(MenaiError) as exc_info:
            menai.evaluate(
                '(let ((list (lambda (a b) "x")))\n'
                '  (match (list 1 2)\n'
                '    ((: list a b) 1)\n'
                '    (_ 0)))'
            )

        assert exc_info.value.line == 3

    def test_shadowed_head_does_not_match_the_builtin_type(self, menai):
        """A shadowed head must not silently match against the builtin type."""
        with pytest.raises(MenaiError):
            ev(
                menai,
                '(let ((list (lambda (a b) "not-a-list")))'
                '  (match (list 1 2)'
                '    ((: list a b) (list a b))'
                '    (_ "no match")))',
            )


class TestUnshadowedBuiltinTypePatternHead:
    """An unshadowed builtin sequence type head still names the builtin type."""

    def test_list_head_matches_a_list(self, menai):
        """(: list a b) destructures a two-element list."""
        assert ev(menai, "(match (list 1 2) ((: list a b) (integer+ a b)) (_ 0))") == "3"

    def test_vector_head_matches_a_vector(self, menai):
        """(: vector a b) destructures a two-element vector."""
        assert ev(menai, "(match (vector 1 2) ((: vector a b) (integer+ a b)) (_ 0))") == "3"

    def test_list_head_falls_through_for_a_non_list(self, menai):
        """(: list a b) does not match a value that is not a list."""
        assert ev(menai, '(match "hi" ((: list a b) "list") (_ "other"))') == '"other"'

    def test_head_in_a_scope_that_does_not_shadow_it(self, menai):
        """An inner binder of another name leaves the builtin head resolvable."""
        assert ev(
            menai,
            "(let ((xs (list 1 2)))"
            "  (match xs ((: list a b) (integer+ a b)) (_ 0)))",
        ) == "3"

    def test_shadowing_does_not_leak_out_of_its_scope(self, menai):
        """A sibling let that does not shadow the name keeps the builtin head."""
        assert ev(
            menai,
            "(let ((f (lambda (xs) (match xs ((: list a b) (integer+ a b)) (_ 0)))))"
            "  (f (list 3 4)))",
        ) == "7"
