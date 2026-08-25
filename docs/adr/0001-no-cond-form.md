# ADR-0001: No `cond` form

Date: 2026-08-24  
Status: Accepted

## Context

Lisp-family languages traditionally provide a `cond` form for multi-branch
conditional logic — a chain of test/expression pairs evaluated in order until one
test is truthy. This is a core form in Scheme, Common Lisp, and most Lisp
dialects.

Menai needed a multi-branch conditional mechanism. The question was whether to
follow the Lisp tradition with `cond` or use a different construct.

## Decision

Menai has no `cond` form. `match` covers all multi-branch conditional use cases
and is more expressive. Do not add `cond`.

## Alternatives considered

### Add `cond` alongside `match`

This would provide familiarity for programmers coming from other Lisp dialects.
However, `match` already handles every pattern that `cond` can express, plus
destructuring, type predicates, and guards. Having both would create two parallel
ways to express the same logic, leading to inconsistency and the need to choose
between them on every use.

### Add `cond` instead of `match`

This would be more traditionally Lisp-like but would lose the destructuring and
pattern-matching capabilities of `match`, which are valuable in a homoiconic
language designed for AI code generation.

## Consequences

### Positive

- There is one canonical way to express multi-branch conditionals, reducing
  stylistic inconsistency.
- `match` is more powerful than `cond`, so no expressiveness is lost.

### Negative

- Programmers coming from other Lisp dialects will not find `cond` and must
  learn `match` instead.
