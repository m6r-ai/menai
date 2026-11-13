# ADR-0061: The type-signature table is the builtins' operand and result contract

Date: 2026-11-13  
Status: Accepted

## Context

Guard insertion emits a runtime type guard for a call argument only when the
argument's declared type is specific. An argument declared `any` reaches the
opcode unchecked, so an opcode that reads an operand as a particular type is
relying on the signature table having declared that type — not merely on callers
passing the right value. `struct-get` reads its field name as a symbol, and while
its entry declared the field name `any`, a non-symbol field name reached the
opcode and faulted.

The result type is consumed just as directly. It becomes the call's proven fact,
which predicate folding may rewrite a test against and against which guard
insertion may treat a later use as already checked. An entry that disagrees with
the builtin produces a fact more precise than reality, which is a miscompile
rather than a missed optimisation.

## Decision

`BUILTIN_TYPE_SIGNATURES` is the only place the compiler learns a builtin's
argument types and result type, and both are consumed as proven facts.

- An operand an opcode assumes must be declared with that type. The guard the
  declaration buys is the check that makes the assumption safe.
- A declared result type must be the type the builtin actually produces.
- The table is therefore part of the builtin's contract, not a hint. Declaring an
  operand or result `any` is safe and forfeits optimisation; declaring a specific
  type that is wrong is not.
- Every declared result type is checked against the type the builtin actually
  produces, so an entry that drifts fails a test rather than silently
  miscompiling.

## Alternatives considered

### Give each operational opcode its own operand type checks

Contradicts the VM design, in which operational opcodes omit type checks and rely
on the compiler having inserted guards. It also pays a runtime check on every
execution of the opcode to catch what is a compile-time mistake.

### Record the types in the opcode header instead

Splits a builtin's description between the bytecode module and the registry, which
is the duplication ADR-0026 removed for arity. The signature table is keyed by
surface name and read by the compiler, which is the layer that needs it.

### Verify only argument types, not result types

An argument mismatch fails at the guard the declaration buys, so it is loud. A
result mismatch is silent: it feeds a wrong fact to predicate folding and guard
insertion. The result is the case that most needs the test.

### Derive the result type by executing each builtin over sample inputs

Would test behaviour rather than the declaration, and needs a sample per type
combination. A declared-versus-actual comparison over the existing test
population catches drift with far less machinery.

## Consequences

### Positive

- A builtin's operand assumptions cannot silently go unguarded: declaring the type
  is how the guard is obtained.
- A drifted result type is caught by a test rather than by a miscompiled program.
- The table stays the single description of a builtin's types, so there is no
  second copy to disagree with it.

### Negative

- Adding an opcode-backed builtin now requires a correct signature entry, and a
  wrong entry is a correctness bug rather than a lost optimisation.
- A result type the table cannot express must be declared unknown, which forfeits
  the fold and the elided guard for that call.
- The result-type test covers the builtin population and must be maintained as
  builtins are added.

## Related

- [ADR-0026](0026-single-builtin-table.md) makes `BUILTINS` the single description
  of an opcode-backed builtin. This decision covers its types, which that table
  does not record.
- [ADR-0027](0027-recursion-cycle-parameter-grounding.md) and
  [ADR-0035](0035-function-provenance-must-follow-containers.md) establish how the
  facts this table seeds are propagated without becoming over-precise.
- [ADR-0036](0036-predicate-fold-may-consume-interprocedural-facts.md) is the
  consumer that turns a wrong result type into a miscompile.
- [ADR-0049](0049-hoisting-must-not-introduce-a-raise.md) relies on builtin
  metadata recorded beside the opcode for the same reason: the description must
  not be able to drift from the builtin it describes.
