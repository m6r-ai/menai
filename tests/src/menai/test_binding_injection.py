"""Tests for host-supplied lexical bindings.

Tests cover:
- evaluate_raw_with_dict / evaluate_and_format_with_dict
- Unpacking the bound dict with dict-get
- Caller-chosen binding names
- Rejection of $-prefixed and prelude names
- Binding a range of value types
- The binding sitting above the prelude and below the program
- MenaiASTConstant carrying a pre-built value through the pipeline
"""

import pytest

from menai import (
    Menai, MenaiBytes, MenaiDict, MenaiInteger, MenaiList, MenaiString,
)
from menai.ast.menai_ast import MenaiASTConstant
from menai.ast.menai_ast_binding_injector import MenaiASTBindingInjector
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.menai_error import MenaiCodegenError


def _inputs() -> MenaiDict:
    """Return a dict carrying a string and a list under known keys."""
    return MenaiDict((
        (MenaiString('input-text'), MenaiString('hello world')),
        (MenaiString('input-lines'), MenaiList((MenaiString('a'), MenaiString('b')))),
    ))


def _evaluate(menai: Menai, expression: str, name: str, value: MenaiDict) -> object:
    """Evaluate an expression with a bound dict and return a Python value."""
    return menai.evaluate_raw_with_dict(expression, name, value).to_python()


class TestEvaluateWithDict:
    """Test the public evaluate_*_with_dict API."""

    def test_reads_a_string_value(self, menai):
        """A bound dict value is readable by name and key."""
        result = menai.evaluate_and_format_with_dict(
            '(dict-get inputs "input-text")', 'inputs', _inputs()
        )
        assert result == '"hello world"'

    def test_reads_a_list_value(self, menai):
        """A bound list value is readable and usable by the expression."""
        result = _evaluate(
            menai,
            '(list-length (dict-get inputs "input-lines"))', 'inputs', _inputs()
        )
        assert result == 2

    def test_raw_result_is_a_menai_value(self, menai):
        """evaluate_raw_with_dict returns the raw MenaiValue."""
        result = menai.evaluate_raw_with_dict(
            '(dict-get inputs "input-text")', 'inputs', _inputs()
        )
        assert isinstance(result, MenaiString)
        assert result.value == 'hello world'

    def test_expression_can_use_prelude_functions(self, menai):
        """The expression sees prelude functions alongside the bound dict."""
        result = menai.evaluate_and_format_with_dict(
            '(string-upcase (dict-get inputs "input-text"))', 'inputs', _inputs()
        )
        assert result == '"HELLO WORLD"'

    def test_caller_chosen_name(self, menai):
        """The binding name is chosen by the caller, not fixed."""
        result = menai.evaluate_and_format_with_dict(
            '(dict-get bag "input-text")', 'bag', _inputs()
        )
        assert result == '"hello world"'

    def test_binding_is_not_visible_under_another_name(self, menai):
        """The dict is only reachable through the chosen name."""
        with pytest.raises(Exception):
            menai.evaluate_raw_with_dict(
                '(dict-get inputs "input-text")', 'bag', _inputs()
            )


class TestBoundValueTypes:
    """Test that a range of value types can be bound."""

    def test_binds_bytes(self, menai):
        """A bytes value can be bound and read back."""
        value = MenaiDict(((MenaiString('data'), MenaiBytes(b'\x01\x02\x03')),))
        result = menai.evaluate_raw_with_dict(
            '(dict-get payload "data")', 'payload', value
        )
        assert isinstance(result, MenaiBytes)
        assert result.value == b'\x01\x02\x03'

    def test_binds_integer(self, menai):
        """An integer value can be bound and used in arithmetic."""
        value = MenaiDict(((MenaiString('n'), MenaiInteger(41)),))
        result = _evaluate(
            menai,
            '(integer+ (dict-get nums "n") 1)', 'nums', value
        )
        assert result == 42

    def test_binds_nested_dict(self, menai):
        """A nested dict can be bound and traversed."""
        inner = MenaiDict(((MenaiString('k'), MenaiString('v')),))
        value = MenaiDict(((MenaiString('inner'), inner),))
        result = menai.evaluate_and_format_with_dict(
            '(dict-get (dict-get outer "inner") "k")', 'outer', value
        )
        assert result == '"v"'

    def test_binds_empty_dict(self, menai):
        """An empty dict can be bound."""
        result = _evaluate(
            menai,
            '(dict-length payload)', 'payload', MenaiDict()
        )
        assert result == 0


class TestNameValidation:
    """Test rejection of names that cannot be lexical bindings."""

    def test_rejects_dollar_prefixed_name(self, menai):
        """A $-prefixed name is reserved for opcode primitives."""
        with pytest.raises(MenaiCodegenError) as exc_info:
            menai.evaluate_raw_with_dict('1', '$integer+', MenaiDict())

        assert 'reserved prefix' in exc_info.value.message

    def test_rejects_prelude_name(self, menai):
        """A prelude name would shadow the prelude binding."""
        with pytest.raises(MenaiCodegenError) as exc_info:
            menai.evaluate_raw_with_dict('1', 'map-list', MenaiDict())

        assert 'collides with a prelude name' in exc_info.value.message

    def test_rejects_prelude_constant(self, menai):
        """A prelude constant name is rejected too."""
        with pytest.raises(MenaiCodegenError) as exc_info:
            menai.evaluate_raw_with_dict('1', 'pi', MenaiDict())

        assert 'collides with a prelude name' in exc_info.value.message

    def test_accepts_name_not_in_prelude(self, menai):
        """A name that is not a prelude name is accepted."""
        result = _evaluate(
            menai,
            '(dict-length x)', 'x', MenaiDict()
        )
        assert result == 0


class TestBindingInjector:
    """Test the injector in isolation."""

    def test_wrap_produces_a_let(self):
        """wrap returns a (let ((name value)) program) form."""
        from menai.ast.menai_ast import MenaiASTInteger, MenaiASTList, MenaiASTSymbol

        program = MenaiASTInteger(1)
        wrapped = MenaiASTBindingInjector.wrap(program, 'inputs', MenaiDict())

        assert isinstance(wrapped, MenaiASTList)
        assert isinstance(wrapped.elements[0], MenaiASTSymbol)
        assert wrapped.elements[0].name == 'let'
        assert wrapped.elements[2] is program

    def test_wrap_binding_carries_the_value(self):
        """The binding's RHS is a MenaiASTConstant holding the value."""
        from menai.ast.menai_ast import MenaiASTInteger, MenaiASTList

        value = MenaiDict(((MenaiString('k'), MenaiString('v')),))
        wrapped = MenaiASTBindingInjector.wrap(MenaiASTInteger(1), 'inputs', value)

        bindings = wrapped.elements[1]
        assert isinstance(bindings, MenaiASTList)
        binding = bindings.elements[0]
        assert isinstance(binding.elements[1], MenaiASTConstant)
        assert binding.elements[1].value is value

    def test_prelude_names_includes_a_known_function(self):
        """The prelude name set is available and contains known names."""
        names = MenaiASTPreludeInjector.prelude_names()
        assert 'map-list' in names
        assert 'pi' in names


class TestASTConstant:
    """Test the MenaiASTConstant node."""

    def test_to_runtime_value_returns_the_wrapped_value(self):
        """The node returns the exact value it was constructed with."""
        value = MenaiDict(((MenaiString('k'), MenaiString('v')),))
        node = MenaiASTConstant(value)
        assert node.to_runtime_value() is value

    def test_type_name_delegates_to_the_value(self):
        """The node reports the wrapped value's type name."""
        node = MenaiASTConstant(MenaiString('x'))
        assert node.type_name() == 'string'

    def test_describe_delegates_to_the_value(self):
        """The node describes the wrapped value."""
        node = MenaiASTConstant(MenaiString('x'))
        assert node.describe() == '"x"'


class TestScoping:
    """Test that the binding sits above the prelude and below the program."""

    def test_program_binding_shadows_the_injected_name(self, menai):
        """A binding in the program wins over the injected binding."""
        result = menai.evaluate_and_format_with_dict(
            '(let ((inputs "shadowed")) inputs)', 'inputs', _inputs()
        )
        assert result == '"shadowed"'

    def test_injected_name_is_not_a_prelude_name(self, menai):
        """Injecting a name does not disturb prelude resolution."""
        result = _evaluate(
            menai,
            '(integer+ (list-length (dict-get inputs "input-lines")) 1)',
            'inputs',
            _inputs(),
        )
        assert result == 3
