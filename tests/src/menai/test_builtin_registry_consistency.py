"""Tests for the builtin registry's arity-table/opcode-map consistency invariant."""

import pytest

from menai.bytecode.menai_bytecode import BUILTIN_OPCODE_MAP
from menai.menai_builtin_registry import (
    MenaiBuiltinRegistry,
    _validate_arity_table_consistency,
)


class TestArityTableConsistency:
    """The arity table must only contain opcode-backed builtins."""

    def test_every_arity_entry_has_an_opcode(self):
        """Every name in BUILTIN_FUNCTION_ARITIES has a BUILTIN_OPCODE_MAP entry."""
        orphans = [
            name
            for name in MenaiBuiltinRegistry.BUILTIN_FUNCTION_ARITIES
            if name not in BUILTIN_OPCODE_MAP
        ]
        assert orphans == []

    def test_validation_passes_for_the_real_table(self):
        """The real arity table satisfies the consistency check."""
        _validate_arity_table_consistency()

    def test_validation_rejects_a_prelude_only_name(self, monkeypatch):
        """A prelude-only name added to the arity table is rejected."""
        monkeypatch.setitem(
            MenaiBuiltinRegistry.BUILTIN_FUNCTION_ARITIES, 'map-list', (2, 2)
        )
        with pytest.raises(AssertionError, match='map-list'):
            _validate_arity_table_consistency()

    def test_validation_reports_every_offending_name(self, monkeypatch):
        """The error message names all offending entries, sorted."""
        monkeypatch.setitem(
            MenaiBuiltinRegistry.BUILTIN_FUNCTION_ARITIES, 'filter-list', (2, 2)
        )
        monkeypatch.setitem(
            MenaiBuiltinRegistry.BUILTIN_FUNCTION_ARITIES, 'fold-list', (3, 3)
        )
        with pytest.raises(AssertionError) as exc_info:
            _validate_arity_table_consistency()

        message = str(exc_info.value)
        assert 'filter-list' in message
        assert 'fold-list' in message
        assert message.index('filter-list') < message.index('fold-list')

    def test_prelude_constructors_are_not_in_the_arity_table(self):
        """Variadic prelude constructors are not opcode-backed and stay out of the table."""
        for name in ('list', 'set', 'vector'):
            assert name not in MenaiBuiltinRegistry.BUILTIN_FUNCTION_ARITIES
