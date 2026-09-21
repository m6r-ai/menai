"""Tests for the builtin registry."""

import pytest

from menai.menai_builtin_registry import BUILTINS, MenaiBuiltinRegistry


class TestBuiltinRegistryAccessors:
    """The registry's accessors report the arity the compiler depends on."""

    def test_fixed_arity_builtin(self):
        """A fixed-arity builtin reports matching min and max."""
        assert MenaiBuiltinRegistry.get_function_arity('integer-abs') == (1, 1)

    def test_variadic_builtin(self):
        """A variadic builtin reports an unbounded max."""
        assert MenaiBuiltinRegistry.get_function_arity('integer+') == (0, None)

    def test_optional_argument_builtin(self):
        """A builtin with an optional trailing argument reports a range."""
        assert MenaiBuiltinRegistry.get_function_arity('list-slice') == (2, 3)

    def test_unknown_name_has_no_arity(self):
        """A prelude-only function has no entry in the builtin table."""
        assert MenaiBuiltinRegistry.get_function_arity('map-list') is None

    def test_primitive_arity_is_the_opcode_operand_count(self):
        """The primitive arity is the number of operands the opcode takes."""
        assert MenaiBuiltinRegistry.get_primitive_arity('integer-abs') == 1
        assert MenaiBuiltinRegistry.get_primitive_arity('list-slice') == 3
        assert MenaiBuiltinRegistry.get_primitive_arity('map-list') is None

    def test_is_primitive_name_distinguishes_prelude_functions(self):
        """Only opcode-backed builtins are primitive names."""
        assert MenaiBuiltinRegistry.is_primitive_name('integer-abs') is True
        assert MenaiBuiltinRegistry.is_primitive_name('map-list') is False


class TestPreludeFunctionsAreNotOpcodeBacked:
    """Prelude-only functions must not appear in the builtin table."""

    @pytest.mark.parametrize("name", [
        'map-list', 'filter-list', 'fold-list', 'list', 'set', 'vector',
    ])
    def test_prelude_function_absent_from_table(self, name):
        """A prelude-only function is not an opcode-backed builtin."""
        assert name not in BUILTINS


class TestCodepointBuiltinArity:
    """integer-codepoint->string is arity-checked like every other builtin."""

    def test_wrong_arity_is_a_compile_time_error(self, menai):
        """A wrong-arity call is rejected with a source-located error."""
        with pytest.raises(Exception, match="wrong number of arguments"):
            menai.evaluate('(integer-codepoint->string 65 66)')

    def test_correct_arity_evaluates(self, menai):
        """A correct call evaluates to the expected string."""
        assert menai.evaluate('(integer-codepoint->string 65)') == "A"
