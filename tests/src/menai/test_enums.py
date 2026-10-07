"""Tests for the enum type."""

import pytest

from menai import MenaiEvalError


class TestEnumDeclaration:
    """Test enum type declaration and the enumtype descriptor."""

    def test_enum_binding_produces_an_enumtype(self, menai):
        """An enum binding produces an enumtype value, not an instance."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enumtype? State))'
        ) == '#t'

    def test_enumtype_is_not_an_enum(self, menai):
        """An enumtype value is not itself an enum instance."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enum? State))'
        ) == '#f'

    def test_enum_declared_in_letrec(self, menai):
        """An enum may be declared in letrec alongside functions."""
        assert menai.evaluate_and_format(
            '(letrec ((State (enum (idle running)))'
            '         (first-state (lambda () (State \'idle))))'
            '  (first-state))'
        ) == '(State idle)'

    def test_enum_definition_outside_binding_raises(self, menai):
        """An enum definition outside a binding is rejected."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(enum (idle running))')

    def test_enum_with_no_variants_raises(self, menai):
        """An enum must declare at least one variant."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum ()))) State)')

    def test_duplicate_variant_raises(self, menai):
        """Duplicate variant names are rejected."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle idle)))) State)')

    def test_non_symbol_variant_raises(self, menai):
        """A variant name must be a symbol."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum ("idle")))) State)')


class TestEnumConstruction:
    """Test enum value construction."""

    def test_construct_each_variant(self, menai):
        """Each declared variant can be constructed."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running stopped))))'
            '  (list (State \'idle) (State \'running) (State \'stopped)))'
        ) == '((State idle) (State running) (State stopped))'

    def test_constructed_value_is_an_enum(self, menai):
        """A constructed value is an enum instance."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enum? (State \'idle)))'
        ) == '#t'

    def test_undeclared_variant_raises(self, menai):
        """Constructing an undeclared variant is rejected."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle running)))) (State \'stopped))')

    def test_unquoted_variant_raises(self, menai):
        """The variant name must be quoted."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle running)))) (State idle))')

    def test_wrong_arity_raises(self, menai):
        """An enum constructor takes exactly one argument."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle running)))) (State))')


class TestEnumEquality:
    """Test enum equality."""

    @pytest.mark.parametrize("expression,expected", [
        ("(enum=? (State 'idle) (State 'idle))", '#t'),
        ("(enum=? (State 'idle) (State 'running))", '#f'),
        ("(enum!=? (State 'idle) (State 'running))", '#t'),
        ("(enum!=? (State 'idle) (State 'idle))", '#f'),
    ])
    def test_equality(self, menai, expression, expected):
        """Two enum values are equal iff same type and same variant."""
        wrapped = f'(let ((State (enum (idle running)))) {expression})'
        assert menai.evaluate_and_format(wrapped) == expected

    def test_equality_across_distinct_types_is_false(self, menai):
        """Two enums with the same variant name are distinct types."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle)))'
            '      (B (enum (idle))))'
            '  (enum=? (A \'idle) (B \'idle)))'
        ) == '#f'

    def test_equality_requires_enums(self, menai):
        """enum=? rejects a non-enum argument."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle)))) (enum=? (State \'idle) 5))')


class TestEnumIntrospection:
    """Test the enum introspection operations."""

    def test_enumtype_name(self, menai):
        """enumtype-name returns the type name."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enumtype-name State))'
        ) == '"State"'

    def test_enumtype_variants(self, menai):
        """enumtype-variants returns the variant names in declaration order."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running stopped)))) (enumtype-variants State))'
        ) == '(idle running stopped)'

    def test_enum_variant(self, menai):
        """enum-variant returns the variant name of a value."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enum-variant (State \'running)))'
        ) == 'running'

    def test_enum_predicate_rejects_non_enums(self, menai):
        """enum? is total and returns #f for non-enum values."""
        assert menai.evaluate_and_format('(enum? 5)') == '#f'
        assert menai.evaluate_and_format('(enum? "x")') == '#f'
        assert menai.evaluate_and_format('(enum? (list 1 2))') == '#f'
        assert menai.evaluate_and_format('(enum? #none)') == '#f'

    def test_enumtype_predicate_rejects_non_enumtypes(self, menai):
        """enumtype? is total and returns #f for non-enumtype values."""
        assert menai.evaluate_and_format('(enumtype? 5)') == '#f'
        assert menai.evaluate_and_format('(enumtype? "x")') == '#f'


class TestEnumHashability:
    """Test that enum values are usable as dict and set keys."""

    def test_enum_as_dict_key(self, menai):
        """A dict may be keyed by enum variant."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (dict-get (dict (State \'idle) "IDLE" (State \'running) "RUNNING")'
            '            (State \'running)))'
        ) == '"RUNNING"'

    def test_enum_as_set_member(self, menai):
        """An enum value is a valid set member."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (set-member? (set (State \'idle) (State \'running)) (State \'idle)))'
        ) == '#t'

    def test_distinct_variants_are_distinct_keys(self, menai):
        """Distinct variants occupy distinct dict entries."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (dict-length (dict (State \'idle) 1 (State \'running) 2)))'
        ) == '2'


class TestEnumPatternMatching:
    """Test matching on enum values."""

    def test_match_selects_the_right_arm(self, menai):
        """Each variant dispatches to its own arm."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running stopped))))'
            '  (list'
            '    (match (State \'idle)'
            '      ((State \'idle) "i") ((State \'running) "r") ((State \'stopped) "s"))'
            '    (match (State \'running)'
            '      ((State \'idle) "i") ((State \'running) "r") ((State \'stopped) "s"))'
            '    (match (State \'stopped)'
            '      ((State \'idle) "i") ((State \'running) "r") ((State \'stopped) "s"))))'
        ) == '("i" "r" "s")'

    def test_match_with_wildcard_default(self, menai):
        """A wildcard arm catches an unmatched value."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (match (State \'running)'
            '    ((State \'idle) "i")'
            '    (_ "other")))'
        ) == '"other"'

    def test_match_on_a_non_enum_falls_through(self, menai):
        """An enum pattern does not match a value of another type."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (match 5'
            '    ((State \'idle) "i")'
            '    (_ "other")))'
        ) == '"other"'

    def test_match_arms_of_distinct_types_do_not_cross(self, menai):
        """A pattern for one enum type does not match another enum type."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle)))'
            '      (B (enum (idle))))'
            '  (match (B \'idle)'
            '    ((A \'idle) "a")'
            '    (_ "other")))'
        ) == '"other"'

    def test_match_in_a_recursive_function(self, menai):
        """Enums dispatch correctly inside a recursive function."""
        assert menai.evaluate_and_format(
            '(letrec ((State (enum (idle running stopped)))'
            '         (describe (lambda (s)'
            '                     (match s'
            '                       ((State \'idle) "idle")'
            '                       ((State \'running) "running")'
            '                       ((State \'stopped) "stopped")))))'
            '  (list (describe (State \'idle))'
            '        (describe (State \'stopped))))'
        ) == '("idle" "stopped")'
