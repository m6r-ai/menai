# ADR-0032: Single-binding tail-recursive letrecs are converted to loops before inlining

Date: 2026-09-27  
Status: Accepted

## Context

A self-recursive function written as a `letrec` is a closure allocation plus a
call per iteration. The inliner cannot help: it does not inline recursive calls
(ADR-0031), because substituting a self-recursive body would not terminate the
fixed-point loop.

Such a function is, however, a loop in disguise. When the recursion is a single
tail call (the classic accumulator-style iteration) the closure and the call
overhead can be eliminated entirely by rewriting the recursion as a loop with an
explicit back-edge.

The rewrite must happen before inlining so that the inliner sees the loop body
and can inline calls within it, and so that the IR optimizer can optimise the
loop body.

## Decision

The `MenaiIRLetrecToLoop` pass converts a `letrec` to a `MenaiIRLoop` when all of
the following hold:

- The `letrec` has exactly one binding.
- The binding's value is a lambda that is self-referencing (the binding name is
  in the lambda's `sibling_free_vars`).
- The `letrec` body is a single call to that lambda, optionally wrapped in a
  return.
- The arity of that call matches the lambda's parameter count.
- Every self-referencing call in the lambda body is a tail call (recursion is
  iteration, not a general call graph).
- The binding name is not referenced outside direct call position, and no nested
  lambda references it.

The lambda's self-referencing tail calls become `MenaiIRRecur` back-edges, and
the call's arguments become the loop's initial values.

The pass runs before the inliner in the IR pipeline.

## Alternatives considered

### Convert multi-binding letrecs as well

Rejected. A loop has a single set of loop-carried variables. Converting a
mutually-recursive group would require each binding's parameters to become loop
variables updated in lock-step, which is a substantially more complex
transformation for a case that is rare in practice.

### Leave recursion to the inliner

Not viable: the inliner does not inline recursive calls (ADR-0031), so the
closure and call overhead would remain.

### Convert at the CFG level instead of the IR level

Rejected. The pattern is a syntactic one over the tree — a single-binding letrec
whose body is a single self-call — and is far easier to recognise and rewrite on
the IR tree than after scope has been dissolved into SSA values and edges.

## Consequences

### Positive

- A self-recursive accumulator-style function becomes a loop with no closure
  allocation and no call per iteration.
- The inliner can inline calls inside the loop body, and the IR optimizer can
  optimise it, because the conversion happens first.

### Negative

- Only single-binding letrecs are converted, so a self-recursive function that
  shares a `letrec` group with other bindings is not converted. Moving such a
  function into its own single-binding `letrec` makes it eligible.
- The pass must reject any binding name reference outside direct call position,
  because the name is not in scope after conversion.
