# ADR-0011: S-expression syntax

Date: 2026-08-24  
Status: Accepted

## Context

Menai needed a syntax. The choice of syntax affects every aspect of the language:
the lexer, parser, AST, error messages, tooling, and the experience of any agent or
human writing Menai code. A complex syntax requires a complex parser, more
error-handling infrastructure, and more machinery for tooling (formatters,
disassemblers, etc.).

The language is designed for AI agents as the primary users. This raises the
question: what syntax is most natural for an AI to generate, read, and reason
about?

## Decision

Menai uses S-expression syntax: nested parenthesised forms where the first element
is the operator and the remaining elements are operands. This is the same general
form used by Lisp-family languages.

## Alternatives considered

### C-style syntax (curly braces, infix operators, statements)

This would be more familiar to human programmers coming from mainstream languages
(C, Java, Python, Rust). However, it requires a significantly more complex parser:
operator precedence rules, statement vs expression distinctions, semicolons,
optional braces, and ambiguous grammars all add complexity. The tooling cost is
also higher — formatters and disassemblers must understand and reproduce the
syntactic conventions. For AI code generation, the complexity of producing
correctly-formatted C-style code is higher than producing correctly-structured
S-expressions.

### Custom DSL syntax

A purpose-built syntax could be optimised for AI readability and generation. But
designing a new syntax from scratch is a significant undertaking, and the result
would be unfamiliar to both humans and AIs. There would be no existing tooling,
no prior art, and no pre-training familiarity. The benefit of a custom syntax
would have to justify this cost, and it is unclear what that benefit would be over
S-expressions.

## Consequences

### Positive

- The parser is simple: S-expressions have a minimal grammar (parentheses, atoms,
  comments). No operator precedence, no statement/expression distinction, no
  semicolons or braces.
- The syntactic structure is familiar to LLMs from their pre-training on
  Lisp-family code, even though Menai's semantics differ from other S-expression
  languages. This lowers the barrier for AI code generation.
- Homoiconicity is natural: code and data share the same S-expression
  representation, which is a direct consequence of the syntax choice.
- Tooling is simpler: formatters, pretty-printers, and disassemblers work with
  the same uniform structure.

### Negative

- S-expression syntax is less familiar to programmers from mainstream
  C-style languages, who may find the heavy use of parentheses and prefix notation
  unfamiliar.
- The syntax does not communicate type information visually (e.g. no type
  annotations on bindings), which could make code harder to read for humans
  without tool support.
