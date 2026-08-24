# ADR-0012: Distinct `#none` type for absence

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

Many languages use a single value to represent both boolean false and the absence
of a value. Scheme uses `#f` for both. Python uses `None` (which is falsy in boolean
contexts). This overloading is convenient but creates ambiguity: when a function
returns `#f`, does it mean "the answer is false" or "there is no answer"?

This ambiguity matters for Menai because the language is designed for AI agents
generating and reasoning about code. An AI that receives `#f` from `dict-get`
cannot tell whether the key exists with value `#f`, or whether the key is absent.
The agent must call a separate predicate (`dict-has?`) to disambiguate. This is
error-prone.

It also creates optimization headaches. If `#f` means both "false" and "absent",
the compiler cannot treat `#f` as a pure boolean value — it carries overloaded
semantics that complicate type analysis and dead code elimination.

## Decision

Menai has a distinct `#none` type, separate from boolean `#f`. The `#none` value
represents the absence of a value. It is returned by operations that produce no
meaningful result: missing dict keys (when no default is provided), not-found
list/string searches, unparseable string conversions, etc.

`#none` is not a boolean and cannot be used as a condition in `if`. The `none?`
predicate tests for it explicitly.

## Alternatives considered

### Reuse `#f` for absence (Scheme model)

This is the simplest approach — one fewer type, no new literal. But it overloads
`#f` with two meanings: "false" and "absent." This creates ambiguity in return
values, complicates type analysis, and requires callers to use separate
predicates to disambiguate. It goes against Menai's principle of being explicit.

### Make `#none` falsy (Python model)

This would keep `#none` as a distinct type but allow it in boolean contexts (e.g.
`(if #none ...)` would be valid and take the false branch). This preserves the
distinction in return values but reintroduces ambiguity in conditionals: the
compiler can no longer assume `if` receives a boolean. It also creates an
implicit coercion from `#none` to `#f`, which violates the principle of
explicitness.

## Consequences

### Positive

- Return values are unambiguous: `#f` always means false, `#none` always means
  absent. No need for a separate predicate to disambiguate.
- The boolean type stays pure — `#f` is always a boolean, never overloaded with
  another meaning. This simplifies type analysis and optimization.
- The language is explicit and consistent: absence has its own type, its own
  literal (`#none`), and its own predicate (`none?`).

### Negative

- There is one more type in the language, adding a small amount of complexity to
  the type system, value hierarchy, and documentation.
- Programmers coming from languages where false and absence are conflated must
  learn the distinction.
