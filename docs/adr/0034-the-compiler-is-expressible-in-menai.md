# ADR-0034: The compiler is expressible in Menai

Date: 2026-09-28  
Status: Accepted

## Context

Menai's compiler is currently written in Python. The long-term goal is that
every part of it — the front end, the analyses, the optimisations, the code
generation — could be written in Menai itself.

This is not a porting exercise for its own sake. Menai is pure: it has no
mutation, no I/O, and no side effects. A compiler written in Menai would be a
compiler that can be reasoned about, tested, and transformed by the same tools
that consume the language. It would also mean an AI agent could inspect and
modify the compiler using Menai's own machinery.

The goal imposes a standard on the Python implementation, because a component
that cannot be expressed as a pure function over values can never be ported.

## Decision

Every compiler phase is a pure function over immutable values:

- **Models are values.** A data structure passed between phases is immutable,
  so a phase cannot alter its input.
- **Transformations are pure functions.** A phase takes a value and returns a
  new value. It does not mutate its input, and it does not depend on hidden
  state that survives between calls.

This is the standard the whole compiler holds itself to, not a rule for one
layer. It applies to the representations (AST, IR, CFG, VCode, bytecode), to
the builders and analyses that transform them, and to the front end.

### Enforcing it

A model's sequence fields are declared as tuples and every construction site
passes a tuple, so a model's contents cannot be mutated in place. The type
checker enforces this: a list passed where a tuple is declared is a static
error. Each layer also has an immutability test that snapshots a model, runs a
phase over it, and asserts the input is unchanged.

Freezing the models — making a frozen dataclass, so that rebinding a field
raises — was considered and rejected. It is not needed for the invariant the
tuples provide, and it cannot be applied conditionally: the type checker
requires the `frozen` argument to be a literal, so a model is either always
frozen or never. Always freezing costs roughly four times as much to construct
as a plain dataclass, and the compiler constructs a great many of them.

## Relationship to other ADRs

ADR-0005 requires that no IR pass mutate its input tree, and ADR-0033 makes the
CFG an immutable value. Both are instances of this standard, adopted before the
general principle was articulated. This ADR does not supersede them; it is the
general statement they are instances of.

## Alternatives considered

### Treat immutability as a CFG-specific fix

The CFG refactor (ADR-0033) could be regarded as a one-off, with no general
standard. This would leave the other models mutable by convention and the
asymmetry between layers unexplained: a reader would have no way to tell
whether one layer being frozen and another not was a decision or an accident.
Rejected.

### Require purity only of the optimisation passes

The passes are where mutation caused visible bugs (ADR-0033). Restricting the
standard to them would leave the builders and the front end unconstrained, and
those are the components that must be ported for the compiler to be expressible
in Menai. Rejected.

### Coerce sequence fields to tuples during construction

A `__post_init__` that converts a model's sequence fields to tuples would make
the invariant hold however the model is constructed, rather than relying on
every construction site to pass a tuple. It was rejected on measured cost: it
roughly doubles the cost of constructing a model, and it does not remove the
need for the tests. Declaring the fields as tuples and letting the type checker
find the construction sites achieves the same result at no runtime cost.

### Freeze the models

Making each model a frozen dataclass would make rebinding a field raise, which
the tuple fields do not prevent. It was rejected because it cannot be applied
conditionally — the type checker requires the `frozen` argument to be a literal
— so it would cost roughly four times as much to construct a model on every
compilation, in exchange for catching a mistake that the immutability tests
already catch.

## Consequences

### Positive

- The standard is explicit, so a new phase's author knows what is required and
  a reviewer knows what to check.
- The CFG refactor and the IR tree immutability rule become instances of a
  single stated principle rather than isolated decisions.
- The property that matters most for Menai-expressibility — that
  transformations are pure functions of their input — is achievable without
  paying a cost on every compilation, because freezing is a verification-time
  check rather than a runtime one.

### Negative

- Rebinding a model's field is not prevented, only mutating its contents. A
  phase that reassigns a field of a model it was handed is caught by the
  immutability tests rather than by the model itself.
- The standard is enforced by the type checker and the tests, not by the model,
  so a phase written in a way the type checker cannot see can still violate it.
