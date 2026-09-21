# ADR-0026: A single table describes each opcode-backed builtin

Date: 2026-09-21  
Status: Accepted

## Context

Menai distinguishes opcode-backed builtins (implemented directly in the VM as
opcodes) from prelude-only functions (implemented as Menai lambdas in
`prelude.menai`). ADR-0009 established that the two categories must not be
confused, and recorded a design in which opcode-backed builtins appeared in two
tables: an opcode mapping in the bytecode module and an arity table in the
registry, with an assertion that the arity table's names all had opcodes.

In practice the two tables covered the same set of names and, for most builtins,
recorded the same arity, because a fixed-arity builtin's surface arity is exactly
its opcode's operand count. The arity table was therefore largely a copy. It also
drifted: a builtin could be added to the opcode mapping and omitted from the
arity table, silently losing its compile-time arity check. One such omission had
already occurred.

The assertion intended to prevent this only checked one direction — a name in the
arity table with no opcode — and could not detect a builtin that had an opcode
but no arity entry. It had also never actually been implemented.

## Decision

The registry in `menai_builtin_registry.py` holds a single table, `BUILTINS`,
keyed by surface-language name. A builtin is opcode-backed if and only if it
appears in that table. Each entry records:

- the opcode that implements it, and
- the arity it presents to the surface language, as a minimum and an optional
  maximum.

The opcode's operand count is derived from the opcode itself rather than stored,
so it cannot disagree with the opcode it belongs to. The surface arity differs
from the operand count only for variadic and optional-argument builtins, where
the desugarer lowers a surface call into one or more fully-saturated opcode
calls.

Prelude-only functions (e.g. `map-list`, `filter-list`, `fold-list`, `list`,
`set`, `vector`) are implemented as Menai lambdas in `prelude.menai` and must
not appear in `BUILTINS`.

## Alternatives considered

### Keep two tables and assert consistency between them

This is the design ADR-0009 recorded. It was rejected because the duplication is
the source of the problem: the tables can disagree, and the assertion can only
check one direction. Folding the opcode into the entry makes the invalid state
unrepresentable instead of detecting it after the fact.

### Keep the arity table and derive the opcode mapping from it

This inverts the duplication rather than removing it, and would move opcode
definitions out of the bytecode module where the opcode enum lives.

### Include prelude functions in the same table

This would require the registry to distinguish opcode-backed from
prelude-implemented entries and would duplicate the prelude's own binding
information. Keeping the table to opcode-backed builtins means the prelude stays
self-contained.

## Consequences

### Positive

- There is one place to look for everything about an opcode-backed builtin.
- A builtin cannot be declared without an opcode, so the two categories cannot be
  confused by construction, and a builtin cannot silently lose its arity entry.
- The opcode's operand count is derived from the opcode, so it cannot disagree
  with the opcode it belongs to.

### Negative

- When adding an opcode-backed builtin, its entry must be added to `BUILTINS`;
  forgetting to do so means the name is treated as a prelude function and its
  calls are not arity-checked.
- When adding a prelude-only function, it must not be added to `BUILTINS`.
- This invariant spans `menai_builtin_registry.py` and `prelude.menai`, which are
  maintained independently, making it easy to violate accidentally.
