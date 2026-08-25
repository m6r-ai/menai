# ADR-0015: Desugar to a small core before IR

Date: 2026-08-24  
Status: Accepted

## Context

Menai's source language has a number of convenient constructs: `match`,
`and`, `or`, `struct`, variadic arithmetic wrappers, default arguments, and
more. Each of these has its own semantics and evaluation rules.

If the compiler backend (IR builder, CFG builder, optimisation passes, code
generators) had to handle every source-level construct directly, the IR would
need a node type for each one. Every optimisation pass would need to understand
and handle every form. The compiler would be more complex and harder to
maintain.

## Decision

The desugarer transforms the full AST into a small core language before the IR
builder sees it. The core consists of:

- Literals (numbers, strings, booleans, `#none`)
- Variables (symbols)
- `if` expressions
- `let` / `let*` / `letrec` bindings
- `lambda` expressions
- Function calls

Everything else is desugared into these forms. For example, `match` becomes
nested `if` expressions with predicate checks and destructuring, `and`/`or`
become nested `if` expressions with short-circuit evaluation, and variadic
arithmetic operations become explicit binary call chains.

## Alternatives considered

### Handle all source forms in the IR

This would keep the IR close to the source language, preserving structure that
desugaring discards. But it would require the IR, every optimisation pass, and
both code generators to handle every form. The IR would have more node types,
each pass would have more cases, and the compiler would be more complex overall.
The simplicity of a small core was preferred.

### Desugar at the IR level instead of the AST level

This would keep the full AST through semantic analysis and module resolution,
then desugar when lowering to IR. But there is no benefit to keeping the
non-core forms past semantic analysis — they have already been validated and
do not need to participate in module resolution. Desugaring at the AST level
means the IR builder and everything downstream only ever sees the core forms,
which is the goal.

## Consequences

### Positive

- The IR, CFG, optimisation passes, and code generators only handle a small set
  of core forms. This keeps the compiler design simpler and reduces the surface
  area for bugs.
- Optimisation passes operate on a smaller, more uniform IR, making them easier
  to write and reason about.
- New source-level constructs can be added by writing desugaring rules, without
  touching the IR or anything downstream.

### Negative

- Some structural information is lost during desugaring. For example, a `match`
  expression becomes a tree of `if` expressions, and the original pattern
  structure is no longer visible to downstream passes.
- Error messages generated after desugaring may reference the desugared form
  rather than the original source construct, though source location metadata
  is preserved to mitigate this.
- The desugarer itself is a substantial component with its own complexity,
  particularly for pattern matching and variadic operation expansion.
