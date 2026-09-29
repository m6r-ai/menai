# ADR-0036: Type-predicate folding may consume interprocedural facts

Date: 2026-09-29  
Status: Accepted

## Context

ADR-0029 restricted type-predicate folding to facts derived locally within the
function being compiled. The restriction existed because the interprocedural
type analysis could report a parameter's type more precisely than was sound: a
function value that escaped into a call the analysis could not resolve could be
called with a value of any type, but the analysis did not see that call site and
so did not degrade the parameter. Folding a predicate on such a fact would
delete a branch that must be taken at runtime, changing behaviour.

ADR-0029 recorded two things about that gap. First, that it was latent for guard
insertion. Second, that the correct fix was to make the analysis sound by
tracking escaping function values, and that the locally-proven restriction could
then be relaxed without changing the pass's structure.

The first of those was wrong. Guard insertion consumes the same facts and omits
the `ASSERT_STRUCT` guard on the strength of them, so a function value that
escaped into an unresolved call with a value of another type caused a struct
field access to be rewritten to a constant index and executed without a guard,
crashing the VM. The gap was reachable, not latent.

ADR-0035 makes the analysis sound by following function values through
containers and degrading the parameters of every function that escapes.

## Decision

The restriction in ADR-0029 is lifted. Type-predicate folding may consume any
fact the interprocedural type analysis reports, because those facts are now
sound: a function that can be called from a call site the analysis cannot
resolve has its parameters left unconstrained, so the analysis never reports a
proven type for a parameter that can receive a value of another type.

The pass's structure is unchanged. It consumes the same per-value facts it
already consumed; only the restriction on which facts it will act on is removed.

## Alternatives considered

### Keep the locally-proven restriction

Retains the restriction as defence in depth against a future unsoundness in the
analysis. Rejected: it is redundant once the analysis is sound, and it forfeits
the folds the restriction was only ever meant to defer. Defence against a
future unsoundness belongs in the analysis, where the unsoundness would be
introduced, not duplicated in every consumer.

### Keep the restriction until the analysis is proven sound by testing

The analysis is sound by construction once ADR-0035 is implemented: the
degradation is applied to every function that can reach an unresolved call site,
not only to those the tests happen to cover. The restriction would be removed on
a schedule rather than on a criterion.

## Consequences

### Positive

- Predicates on parameters and call results fold when the analysis proves their
  type, eliminating runtime checks and branches that were previously retained.
- The pass no longer carries a separate provenance computation for its
  arguments, so it is simpler.

### Negative

- The pass now depends on the soundness of the interprocedural analysis, so an
  unsoundness there is a miscompile here rather than a missed optimisation. This
  is the ordinary relationship between an analysis and its consumers.

## Related

- [ADR-0029](0029-predicate-fold-locally-proven-only.md) is superseded by this
  decision.
- [ADR-0035](0035-function-provenance-must-follow-containers.md) makes the
  interprocedural facts sound, which is what this decision relies on.
