# ADR-0007: Dead code elimination is always safe

Date: 2026-08-24  
Status: Accepted

## Context

In languages with side effects, an expression whose result is unused may still
have observable effects (I/O, mutation, state changes) and cannot be safely
removed. Optimisation passes in such languages must check for side effects before
discarding code, which adds complexity and can be a source of bugs.

Menai is a pure functional language with no side effects. This changes the
landscape fundamentally.

## Decision

Because Menai has no side effects, any expression whose result is never used can
be discarded unconditionally. Optimisation passes may rely on this without
checking for side effects.

## Alternatives considered

### Conservative elimination with side-effect checks

This would require each optimisation pass to implement or call a side-effect
analysis before discarding code. In a pure language this analysis would always
return "no side effects", making the check pure overhead — both in implementation
complexity and in runtime.

## Consequences

### Positive

- Dead code elimination is simpler and more aggressive than in impure languages.
- Optimisation passes do not need side-effect analysis infrastructure.

### Negative

- This invariant is a direct consequence of Menai's purity (see blueprint.md,
  "Purity — no side effects"). If Menai were ever to gain side effects, this
  invariant would break and all optimisation passes relying on it would need to
  be revisited.
- This invariant spans all optimisation passes, making it easy to violate
  accidentally if the language's purity guarantee is weakened.
