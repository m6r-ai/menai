# ADR-0002: Symbols are not strings

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

In some Lisp dialects, symbols and strings are interchangeable or can be coerced
between each other with little friction. This can lead to ambiguity: is `"foo"`
the same as `'foo`? Can you use a string where a symbol is expected? The
distinction between code-as-data (symbols) and text data (strings) becomes
blurred.

Menai is homoiconic — code is data. This makes the distinction between symbols
(representing code structure) and strings (representing text data) particularly
important. Conflating them would undermine the clarity of the code-as-data model.

## Decision

`symbol` values are a distinct type from `string`. Symbols are produced only by
`quote` and exist solely to support homoiconicity (code-as-data). They cannot be
used interchangeably with strings.

## Alternatives considered

### Unify symbols and strings

This would reduce the number of types and simplify some operations. However, it
would blur the distinction between code structure and text data, making it harder
to reason about homoiconic operations like `quote` and pattern matching on code
representations.

### Implicit coercion between symbols and strings

This would allow symbols and strings to be used interchangeably with automatic
conversion. This introduces the same class of bugs as any implicit coercion:
ambiguity about which type is actually being operated on, and unexpected behaviour
when a symbol is silently converted to a string or vice versa.

## Consequences

### Positive

- Homoiconic operations (quote, pattern matching on code) produce and consume
  symbols, not strings, making the code-as-data model clear and unambiguous.
- There is no implicit coercion between symbols and strings, eliminating a class
  of bugs.

### Negative

- Symbols and strings require their own predicates (`symbol?`, `string?`),
  equality operations, and conversion functions.
- Converting between the two types requires an explicit call to
  `symbol->string`.
