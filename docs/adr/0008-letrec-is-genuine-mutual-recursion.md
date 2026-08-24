# ADR-0008: `letrec` reaching the IR builder is always genuine mutual recursion

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

`letrec` in Lisp-family languages can be used for both genuinely mutually recursive
bindings and for non-recursive bindings that are simply written in `letrec` form.
The IR builder and downstream passes need to know which case they are handling.
Allowing non-recursive bindings to reach the IR builder as `letrec` would require
every downstream pass to handle a case that is semantically just `let`, adding
complexity and potential for bugs.

## Decision

The desugarer guarantees that by the time `letrec` reaches the IR builder, every
`letrec` is a single strongly-connected component of mutually-recursive bindings.
Non-recursive bindings are hoisted to `let` forms.

However, not every binding in a `letrec` group is necessarily a lambda. A
non-lambda binding (e.g. `(letrec ((x (list (lambda () x)))) x)`) can appear in a
`letrec` group when its RHS contains a nested lambda that closes over the binding
name — the dependency analyzer sees a cycle and correctly keeps it in `letrec`.
The IR builder and both codegens handle this. The CFG builder handles it via a
dedicated Phase 2b / Phase 3b in `_build_letrec`: non-lambda binding values are
evaluated after all sibling lambda closures exist (so nested lambdas can capture
them), and any nested lambdas with sibling captures are patched afterward.

IR passes downstream of the IR builder may not assume all `letrec` bindings are
lambdas.

## Alternatives considered

### Let non-recursive bindings pass through as `letrec`

This would avoid the hoisting step in the desugarer but would require every
downstream pass to distinguish recursive from non-recursive `letrec` groups,
adding complexity throughout the compiler.

### Require all `letrec` bindings to be lambdas

This would simplify downstream passes but would incorrectly reject valid programs
where a non-lambda binding participates in a mutual recursion cycle through a
nested lambda.

## Consequences

### Positive

- Downstream passes can assume `letrec` means genuine mutual recursion, not just
  a binding that happened to be written as `letrec`.
- The CFG builder has dedicated phases (2b/3b) to handle non-lambda bindings
  within `letrec` groups.

### Negative

- The desugarer must correctly identify strongly-connected components and hoist
  non-recursive bindings to `let`.
- Downstream passes must not assume all bindings in a `letrec` group are lambdas.
- This invariant spans the desugarer, IR builder, both codegens, and the CFG
  builder, making it easy to violate accidentally.
