# ADR-0059: The inliner's recursion guard is intrinsic to the lambda

Date: 2026-10-24  
Status: Accepted

## Context

ADR-0031 established that the IR inliner must not inline a recursive call, and
chose a cheap guard: refuse to inline a call to a `$letrec`-bound function made
from within that `$letrec` group, keying on the target's `binding_name` being a
member of the enclosing group's binding names.

That guard is keyed on a property of the **walk position**, not of the lambda.
The group's names are in scope only while the walk is inside the group; they are
reset when descending into a nested `$let` body, which is what makes a
`$letrec`-bound function inlinable from outside its own group.

The inliner runs to a fixed point. After it inlines a body, it re-walks the
result at the call site, with the call site's group names. A self-call inside a
freshly inlined body is therefore not recognised by a position-based test: the
body's own group names are no longer in scope at the new position. The call
would be inlined again, and again, without bound.

This was not a latent hazard that had merely gone unnoticed. It was known and
recorded in the pass itself, and it was unreachable only because a second check
— the rejection of any lambda with captures — happened to reject every lambda
that has `sibling_free_vars`, which includes every self-recursive one. The two
checks were load-bearing for each other in a way neither stated.

## Decision

The guard is keyed on the lambda, not on the walk position. The inliner refuses
to inline a lambda whose `binding_name` appears in its own `sibling_free_vars`:
a lambda that names itself among its sibling captures can reach itself.

The `letrec_names` set is removed from the pass entirely. It existed only to
serve the position-based guard, and no other check reads it.

Mutual recursion is not detected by this test. A lambda that calls a sibling
which calls it back captures that sibling, and the existing sibling-capture rule
already refuses to inline a lambda bound elsewhere that has sibling captures.
The two rules together cover both shapes.

A direct lambda application (`((lambda ...) arg)`) remains exempt: the lambda is
written at the call site and cannot be recursive in the same sense.

## Alternatives considered

### Keep the position-based guard and fix the re-walk

The re-walk could be made to carry the inlined body's group names forward. This
was rejected: it preserves a guard whose correctness depends on a property the
pass does not otherwise track, and it makes the guard's behaviour depend on how
the fixed-point loop happens to re-enter a subtree. The intrinsic test has no
such dependency and is the same cost — a field comparison.

### Detect mutual recursion as well

Rejected as disproportionate, for the reason ADR-0031 gives: it needs a
call-graph analysis the pass does not otherwise have. The sibling-capture rule
already covers the case soundly, at the cost of also rejecting a non-recursive
sibling call.

## Consequences

### Positive

- The guard is correct under re-walking, because it reads the lambda rather than
  the walk position. The fixed-point loop can no longer re-inline a self-call it
  has just substituted.
- The two checks are no longer load-bearing for each other. Relaxing the capture
  rule — which is desirable, since it is a significant missed optimisation — no
  longer unmasks a non-terminating inliner.
- The pass is smaller: a parameter threaded through nine methods is gone.

### Negative

- A self-recursive lambda is rejected even at a call site where inlining it once
  would be sound, because the guard cannot distinguish "this call site" from
  "the call sites the substitution will create". This is the same coarseness
  ADR-0031 accepted, now stated in terms of the lambda.
- The guard reads a field that is only populated for a lambda bound in a binding
  form. A lambda with no `binding_name` is never considered self-recursive,
  which is correct: an unbound lambda cannot refer to itself by name.

## Related

- [ADR-0031](0031-inliner-does-not-inline-recursive-calls.md) is superseded by
  this decision. Its requirement — do not inline a recursive call — remains in
  force; only the mechanism changes.
- [ADR-0008](0008-letrec-is-genuine-mutual-recursion.md) establishes that a
  `$letrec` group is a single strongly-connected component, which is why a
  sibling capture is the right signal for recursion.
- [ADR-0032](0032-single-binding-tail-recursive-letrecs-become-loops.md) converts
  a single-binding self-recursive group to a loop before the inliner runs, so the
  common self-recursive shape never reaches this guard at all.
