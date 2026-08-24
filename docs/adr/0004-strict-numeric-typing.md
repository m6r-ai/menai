# ADR-0004: Strict numeric typing

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

Most programming languages perform implicit numeric coercion — automatically
converting between integer, float, and complex types as needed. While convenient,
this is a source of subtle bugs: silent precision loss (int → float), unexpected
promotion (float → complex), and ambiguous operator semantics. For a language
designed for AI agents generating code programmatically, these ambiguities are
particularly problematic — an AI might not anticipate that an operation silently
promotes an integer to a float, or that mixing types in a larger expression
causes cascading coercions.

## Decision

There is no implicit coercion between `integer`, `float`, and `complex`. All
arithmetic operators are type-specific (e.g. `integer+`, `float*`,
`complex-sqrt`). Conversions between types must be explicit (e.g.
`integer->float`, `float->complex`).

## Alternatives considered

### Implicit coercion with promotion rules

This is the approach taken by most mainstream languages (Python, JavaScript,
Scheme). It is convenient but introduces a class of bugs from silent coercion and
makes type behaviour less predictable. The coercion rules themselves become
complex (what happens when you mix int and complex? what about overflow?).

### A single numeric type

This would eliminate coercion entirely by having only one number type (e.g.
everything is a float, or everything is a bignum). This loses the ability to
distinguish exact integer arithmetic from inexact floating-point, which is
important for correctness in many algorithms.

## Consequences

### Positive

- There is no class of bugs from silent coercion.
- Type behaviour is predictable: the type of an expression's result is determined
  by the operator used, not by the types of the operands.

### Negative

- Numeric code is more verbose: the programmer must choose the correct operator
  for each type.
- Adding a new numeric type in the future would require adding a full set of
  type-specific operators, not just adding it to a coercion table.
