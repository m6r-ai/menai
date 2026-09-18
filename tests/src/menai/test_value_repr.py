"""Tests for the repr of Menai value types.

Every value type must render a repr that names the class and shows its state.
Types that are not dataclasses do not get a generated repr, so they must define
their own; otherwise they inherit the fieldless dataclass repr from MenaiValue
and render as an empty descriptor that discards all their state.
"""

from menai.menai_value import (
    MenaiDict,
    MenaiFunction,
    MenaiInteger,
    MenaiSet,
    MenaiString,
    MenaiStruct,
    MenaiStructType,
    MenaiSymbol,
)


class TestSymbolRepr:
    """A symbol's repr names the class and quotes the name."""

    def test_repr(self):
        assert repr(MenaiSymbol("found")) == "MenaiSymbol('found')"


class TestFunctionRepr:
    """A function's repr names the class and leads with its name and parameters."""

    def test_repr_puts_name_before_parameters(self):
        value = MenaiFunction(("x", "y"), name="foo")
        assert repr(value) == (
            "MenaiFunction(name='foo', parameters=('x', 'y'), is_variadic=False)"
        )

    def test_nameless_function_repr(self):
        value = MenaiFunction(("pred", "lst"))
        assert repr(value) == (
            "MenaiFunction(name=None, parameters=('pred', 'lst'), is_variadic=False)"
        )

    def test_variadic_function_repr(self):
        value = MenaiFunction(("a", "rest"), name="f", is_variadic=True)
        assert repr(value) == (
            "MenaiFunction(name='f', parameters=('a', 'rest'), is_variadic=True)"
        )


class TestDictRepr:
    """A dict's repr names the class and shows its pairs."""

    def test_repr_shows_pairs(self):
        value = MenaiDict(((MenaiString("a"), MenaiInteger(1)),))
        assert repr(value) == (
            "MenaiDict(pairs=((MenaiString(value='a'), MenaiInteger(value=1)),))"
        )

    def test_empty_dict_repr_is_not_bare_descriptor(self):
        assert repr(MenaiDict()) == "MenaiDict(pairs=())"


class TestSetRepr:
    """A set's repr names the class and shows its elements."""

    def test_repr_shows_elements(self):
        value = MenaiSet((MenaiInteger(1), MenaiInteger(2)))
        assert repr(value) == "MenaiSet(elements=(MenaiInteger(value=1), MenaiInteger(value=2)))"

    def test_empty_set_repr_is_not_bare_descriptor(self):
        assert repr(MenaiSet()) == "MenaiSet(elements=())"


class TestStructTypeRepr:
    """A struct type's repr names the class and shows its name, tag, and fields."""

    def test_repr_shows_name_tag_and_fields(self):
        value = MenaiStructType("point", 7, ("x", "y"))
        assert repr(value) == "MenaiStructType(name='point', tag=7, field_names=('x', 'y'))"

    def test_repr_is_not_bare_descriptor(self):
        assert repr(MenaiStructType("point", 7, ("x", "y"))) != "MenaiStructType()"


class TestStructRepr:
    """A struct instance's repr names the class and shows its type and fields."""

    def test_repr_shows_type_and_fields(self):
        st = MenaiStructType("point", 1, ("x", "y"))
        value = MenaiStruct(st, (MenaiInteger(1), MenaiInteger(2)))
        assert repr(value) == (
            "MenaiStruct(struct_type=MenaiStructType(name='point', tag=1, field_names=('x', 'y')), "
            "fields=(MenaiInteger(value=1), MenaiInteger(value=2)))"
        )

    def test_repr_is_not_bare_descriptor(self):
        st = MenaiStructType("point", 1, ("x", "y"))
        value = MenaiStruct(st, (MenaiInteger(1), MenaiInteger(2)))
        assert repr(value) != "MenaiStruct()"


class TestDataclassReprsUnchanged:
    """Dataclass-backed value types keep their generated reprs."""

    def test_integer(self):
        assert repr(MenaiInteger(1)) == "MenaiInteger(value=1)"

    def test_string(self):
        assert repr(MenaiString("hi")) == "MenaiString(value='hi')"
