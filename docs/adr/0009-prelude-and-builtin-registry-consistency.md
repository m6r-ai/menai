# ADR-0009: Prelude and builtin registry must stay consistent

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

Menai has two categories of builtin function: opcode-backed builtins (implemented
directly in the VM as opcodes) and prelude-only functions (implemented as Menai
lambdas in `prelude.menai`). These two categories must not be confused — adding a
prelude-only function to the opcode arity table would cause failures, and vice
versa. The question was how to enforce this consistency.

## Decision

Two categories of builtin must not be confused:

- **Opcode-backed builtins** have an entry in `BUILTIN_OPCODE_ARITIES` in
  `menai_builtin_registry.py`. The registry asserts that every entry in this table
  has a corresponding opcode in `BUILTIN_OPCODE_MAP`. Adding a name here without an
  opcode will cause an assertion failure at startup.

- **Prelude-only functions** (e.g. `map-list`, `filter-list`, `fold-list`) are
  implemented as Menai lambdas in `prelude.menai`. They must not be added to
  `BUILTIN_OPCODE_ARITIES`.

## Alternatives considered

### Single unified registry

This would avoid the two-category distinction but would require the registry to
know whether each function is opcode-backed or prelude-implemented, adding
complexity. The current split is simpler: the registry only tracks opcode-backed
builtins, and the prelude is self-contained.

### Runtime checks instead of startup assertions

This would catch inconsistencies at runtime rather than at startup, but would
allow invalid states to persist until the specific code path is exercised. The
startup assertion catches problems immediately and deterministically.

## Consequences

### Positive

- The assertion at startup provides a fast, deterministic check that the two
  tables are consistent.
- The two-category split keeps the registry simple — it only tracks opcode-backed
  builtins, and the prelude is self-contained.

### Negative

- When adding an opcode-backed builtin, it must be added to both
  `BUILTIN_OPCODE_ARITIES` and `BUILTIN_OPCODE_MAP`, or the startup assertion
  will fail.
- When adding a prelude-only function, it must not be added to
  `BUILTIN_OPCODE_ARITIES`.
- This invariant spans `menai_builtin_registry.py` and `prelude.menai`, which are
  maintained independently, making it easy to violate accidentally.
