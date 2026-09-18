"""Tests for constant formatting in the disassembler.

Every constant in the disassembler's Constants table and LOAD_CONST annotations
must be self-identifying: it must show its Menai type, and for a struct type it
must show the type's name and fields rather than an opaque descriptor.  Without
this a symbol constant is indistinguishable from a bare name and a struct type
gives no clue which struct it describes.
"""

from menai.menai_value import (
    MenaiBoolean,
    MenaiBytes,
    MenaiDict,
    MenaiFunction,
    MenaiInteger,
    MenaiList,
    MenaiNone,
    MenaiSet,
    MenaiString,
    MenaiStruct,
    MenaiStructType,
    MenaiSymbol,
)
from menai_disassemble.disassemble import format_constant


class TestScalarConstantFormatting:
    """Scalar constants show their Menai type name and value."""

    def test_integer(self):
        assert format_constant(MenaiInteger(1)) == "integer 1"

    def test_string(self):
        assert format_constant(MenaiString("hi")) == 'string "hi"'

    def test_boolean(self):
        assert format_constant(MenaiBoolean(True)) == "boolean #t"

    def test_none(self):
        assert format_constant(MenaiNone()) == "none #none"

    def test_bytes(self):
        assert format_constant(MenaiBytes(b"abc")) == 'bytes #bytes"616263"'


class TestSymbolConstantFormatting:
    """A symbol constant is identified as a symbol, not shown as a bare name."""

    def test_symbol_shows_type_and_name(self):
        assert format_constant(MenaiSymbol("found")) == "symbol found"

    def test_symbol_name_is_preserved(self):
        assert "value" in format_constant(MenaiSymbol("value"))


class TestStructTypeConstantFormatting:
    """A struct type constant is identified by its name and fields."""

    def test_struct_type_shows_name_and_fields(self):
        st = MenaiStructType("cube-repr", 1, ("U", "D", "F", "B", "L", "R"))
        assert format_constant(st) == "structtype <structtype cube-repr (U D F B L R)>"

    def test_struct_type_with_two_fields(self):
        st = MenaiStructType("search-result", 2, ("found", "value"))
        assert "search-result" in format_constant(st)
        assert "found value" in format_constant(st)

    def test_struct_type_is_not_empty_descriptor(self):
        st = MenaiStructType("point", 3, ("x", "y"))
        assert format_constant(st) != "structtype <structtype point ()>"


class TestCompositeConstantFormatting:
    """Composite constants show their type name and described contents."""

    def test_list(self):
        assert format_constant(MenaiList([MenaiInteger(1), MenaiInteger(2)])) == "list (1 2)"

    def test_struct_instance(self):
        st = MenaiStructType("point", 1, ("x", "y"))
        value = MenaiStruct(st, (MenaiInteger(1), MenaiInteger(2)))
        assert format_constant(value) == "struct (point 1 2)"

    def test_function(self):
        value = MenaiFunction(("x", "y"), name="foo")
        assert format_constant(value) == "function <foo (x, y)>"

    def test_anonymous_function(self):
        value = MenaiFunction(("x", "y"))
        assert format_constant(value) == "function <lambda (x, y)>"

    def test_dict(self):
        value = MenaiDict(((MenaiString("a"), MenaiInteger(1)),))
        assert format_constant(value) == 'dict {("a" 1)}'

    def test_set(self):
        value = MenaiSet((MenaiInteger(1),))
        assert format_constant(value) == "set #{1}"


class TestNonMenaiConstants:
    """Non-Menai constants keep their existing formatting."""

    def test_python_string_is_quoted(self):
        assert format_constant("solved-cube") == '"solved-cube"'

    def test_long_python_string_is_truncated(self):
        formatted = format_constant("x" * 100)
        assert formatted == '"' + "x" * 61 + '..."'

    def test_long_menai_value_is_truncated(self):
        st = MenaiStructType("a-very-long-struct-type-name-here", 1, ("field-one", "field-two"))
        assert format_constant(st).endswith("...")
