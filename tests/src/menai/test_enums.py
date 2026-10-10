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

    def test_enum_type(self, menai):
        """enum-type returns the enumtype value for an instance."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enumtype-name (enum-type (State \'running))))'
        ) == '"State"'

    def test_enum_type_result_is_an_enumtype(self, menai):
        """The value enum-type returns is an enumtype, not an instance."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enumtype? (enum-type (State \'running))))'
        ) == '#t'

    def test_enum_type_round_trips_by_identity(self, menai):
        """The returned enumtype is identical to the type the value was built from."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running)))) (enumtype=? (enum-type (State \'running)) State))'
        ) == '#t'

    def test_enum_type_of_one_type_is_not_another(self, menai):
        """enum-type distinguishes nominally distinct enum types."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running)))'
            '      (B (enum (idle running))))'
            '  (enumtype=? (enum-type (A \'idle)) B))'
        ) == '#f'

    def test_enum_type_requires_an_enum(self, menai):
        """enum-type raises when its argument is not an enum."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(enum-type 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((State (enum (idle running)))) (enum-type State))')

    def test_enum_is_instance_true(self, menai):
        """enum-is-instance? is #t for an instance of the named type."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (let ((s (State \'idle)))'
            '    (enum-is-instance? s State)))'
        ) == '#t'

    def test_enum_is_instance_false_for_another_type(self, menai):
        """enum-is-instance? is #f for an instance of a nominally distinct type."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running)))'
            '      (B (enum (idle running))))'
            '  (let ((s (B \'idle)))'
            '    (enum-is-instance? s A)))'
        ) == '#f'

    def test_enum_is_instance_raises_on_non_enum(self, menai):
        """enum-is-instance? raises when the first argument is not an enum."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(
                '(let ((State (enum (idle running)))) (enum-is-instance? 42 State))'
            )

    def test_enum_is_instance_raises_on_non_enumtype(self, menai):
        """enum-is-instance? raises when the second argument is not an enumtype."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(
                '(let ((State (enum (idle running))))'
                '  (let ((s (State \'idle)))'
                '    (enum-is-instance? s 42)))'
            )

    def test_enum_is_instance_raises_on_enumtype_first_arg(self, menai):
        """enum-is-instance? raises when the first argument is a type, not an instance."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(
                '(let ((State (enum (idle running)))) (enum-is-instance? State State))'
            )

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

    def test_enumtype_equality(self, menai):
        """enumtype=? compares enum types by identity."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running)))'
            '      (B (enum (idle running))))'
            '  (enumtype=? A A))'
        ) == '#t'

        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running)))'
            '      (B (enum (idle running))))'
            '  (enumtype=? A B))'
        ) == '#f'

    def test_enumtype_inequality(self, menai):
        """enumtype!=? is the negation of enumtype=?."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running)))'
            '      (B (enum (idle running))))'
            '  (enumtype!=? A B))'
        ) == '#t'

        assert menai.evaluate_and_format(
            '(let ((A (enum (idle running))))'
            '  (enumtype!=? A A))'
        ) == '#f'


class TestEnumSwitchTypeSafety:
    """A fused enum switch must not match a value of a different enum type.

    SWITCH_ENUM dispatches on the variant index alone and never reads the enum
    type.  Variant indices are only meaningful relative to their type, so
    without a type-identity guard a value of another enum type whose index is
    in range would jump to the wrong arm.  These tests pin the guard.
    """

    def test_wrong_typed_scrutinee_falls_through(self, menai):
        """A match over one type, fed a value of another, falls through.

        Both types have the same variant names, so the variant indices collide:
        without the identity guard, B.y (index 1) would match A.y.
        """
        assert menai.evaluate_and_format(
            '(let ((A (enum (x y z)))'
            '      (B (enum (x y z))))'
            '  (match (B \'y)'
            '    ((: A \'x) "ax")'
            '    ((: A \'y) "ay")'
            '    ((: A \'z) "az")'
            '    (_ "fallthrough")))'
        ) == '"fallthrough"'

    def test_wrong_typed_scrutinee_with_different_names_falls_through(self, menai):
        """The guard tests type identity, not variant names.

        B.q has index 1, which is in range for A's table, and the name differs
        from A.y.  A type-blind switch would dispatch to A.y.
        """
        assert menai.evaluate_and_format(
            '(let ((A (enum (x y z)))'
            '      (B (enum (p q r))))'
            '  (match (B \'q)'
            '    ((: A \'x) "ax")'
            '    ((: A \'y) "ay")'
            '    ((: A \'z) "az")'
            '    (_ "fallthrough")))'
        ) == '"fallthrough"'

    def test_wrong_typed_scrutinee_through_a_parameter(self, menai):
        """The guard holds when the scrutinee's type is not known at the call site."""
        assert menai.evaluate_and_format(
            '(letrec ((A (enum (x y z)))'
            '         (B (enum (x y z)))'
            '         (classify (lambda (v)'
            '                     (match v'
            '                       ((: A \'x) "ax")'
            '                       ((: A \'y) "ay")'
            '                       ((: A \'z) "az")'
            '                       (_ "fallthrough")))))'
            '  (classify (B \'y)))'
        ) == '"fallthrough"'

    def test_mixed_type_match_reaches_the_right_group(self, menai):
        """A match naming two enum types dispatches to the group that owns the value.

        The desugarer builds one guard and one switch per enum type.  The first
        group's switch is type-blind, so without the guard it would shadow the
        second group and B.q would match A.y.
        """
        assert menai.evaluate_and_format(
            '(let ((A (enum (x y z)))'
            '      (B (enum (p q r))))'
            '  (match (B \'q)'
            '    ((: A \'x) "ax")'
            '    ((: A \'y) "ay")'
            '    ((: A \'z) "az")'
            '    ((: B \'p) "bp")'
            '    ((: B \'q) "bq")'
            '    ((: B \'r) "br")'
            '    (_ "fallthrough")))'
        ) == '"bq"'

    def test_mixed_type_match_reaches_the_first_group(self, menai):
        """The first group still matches its own values."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (x y z)))'
            '      (B (enum (p q r))))'
            '  (match (A \'z)'
            '    ((: A \'x) "ax")'
            '    ((: A \'y) "ay")'
            '    ((: A \'z) "az")'
            '    ((: B \'p) "bp")'
            '    ((: B \'q) "bq")'
            '    ((: B \'r) "br")'
            '    (_ "fallthrough")))'
        ) == '"az"'

    def test_every_variant_of_the_right_type_still_dispatches(self, menai):
        """The guard does not disturb correct dispatch for the match's own type."""
        assert menai.evaluate_and_format(
            '(letrec ((State (enum (idle running stopped paused)))'
            '         (classify (lambda (s)'
            '           (match s'
            '             ((: State \'idle) "i")'
            '             ((: State \'running) "r")'
            '             ((: State \'stopped) "s")'
            '             ((: State \'paused) "p")'
            '             (_ "other")))))'
            '  (list (classify (State \'idle))'
            '        (classify (State \'running))'
            '        (classify (State \'stopped))'
            '        (classify (State \'paused))))'
        ) == '("i" "r" "s" "p")'

    def test_non_enum_scrutinee_still_falls_through(self, menai):
        """The kind guard keeps the pattern total for a non-enum scrutinee."""
        assert menai.evaluate_and_format(
            '(let ((A (enum (x y z))))'
            '  (match 42'
            '    ((: A \'x) "ax")'
            '    ((: A \'y) "ay")'
            '    ((: A \'z) "az")'
            '    (_ "fallthrough")))'
        ) == '"fallthrough"'


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
            '      ((: State \'idle) "i") ((: State \'running) "r") ((: State \'stopped) "s"))'
            '    (match (State \'running)'
            '      ((: State \'idle) "i") ((: State \'running) "r") ((: State \'stopped) "s"))'
            '    (match (State \'stopped)'
            '      ((: State \'idle) "i") ((: State \'running) "r") ((: State \'stopped) "s"))))'
        ) == '("i" "r" "s")'

    def test_match_with_wildcard_default(self, menai):
        """A wildcard arm catches an unmatched value."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (match (State \'running)'
            '    ((: State \'idle) "i")'
            '    (_ "other")))'
        ) == '"other"'

    def test_match_on_a_non_enum_falls_through(self, menai):
        """An enum pattern does not match a value of another type."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (match 5'
            '    ((: State \'idle) "i")'
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
            '                       ((: State \'idle) "idle")'
            '                       ((: State \'running) "running")'
            '                       ((: State \'stopped) "stopped")))))'
            '  (list (describe (State \'idle))'
            '        (describe (State \'stopped))))'
        ) == '("idle" "stopped")'


class TestEnumtypeAsFirstClassValue:
    """Test calling an enumtype value that is not statically resolvable."""

    def test_call_a_passed_enumtype(self, menai):
        """An enumtype passed as an argument constructs a value when called."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (let ((make (lambda (ctor) (ctor \'idle))))'
            '    (enum-variant (make State))))'
        ) == 'idle'

    def test_call_a_passed_enumtype_in_tail_position(self, menai):
        """A passed enumtype is callable in tail position."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (letrec ((make (lambda (ctor n)'
            '                  (if (integer=? n 0)'
            '                      (ctor \'running)'
            '                      (make ctor (integer- n 1))))))'
            '    (enum-variant (make State 3))))'
        ) == 'running'

    def test_apply_a_passed_enumtype(self, menai):
        """An enumtype is callable through apply."""
        assert menai.evaluate_and_format(
            '(let ((State (enum (idle running))))'
            '  (let ((make (lambda (ctor) (apply ctor (list \'running)))))'
            '    (enum-variant (make State))))'
        ) == 'running'

    def test_call_a_passed_enumtype_with_undeclared_variant_raises(self, menai):
        """Calling a passed enumtype with an undeclared variant is an error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(
                '(let ((State (enum (idle running))))'
                '  (let ((make (lambda (ctor) (ctor \'stopped))))'
                '    (make State)))'
            )

    def test_call_a_passed_enumtype_with_non_symbol_raises(self, menai):
        """Calling a passed enumtype with a non-symbol argument is an error."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(
                '(let ((State (enum (idle running))))'
                '  (let ((make (lambda (ctor) (ctor 42))))'
                '    (make State)))'
            )
