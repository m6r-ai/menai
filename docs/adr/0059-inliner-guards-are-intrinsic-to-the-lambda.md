# ADR-0059: The inliner's guards are intrinsic to the lambda

Date: 2026-10-24  
Status: Accepted

## Context

The IR inliner substitutes a lambda body at a call site. Two guards decide
whether it may.

**Recursion.** Inlining a recursive function is not terminating: the pass runs
to a fixed point, and substituting a self-recursive body would reintroduce the
call the substitution just removed. ADR-0031 chose a cheap guard for this:
refuse to inline a call to a `$letrec`-bound function made from within that
`$letrec` group, keying on the target's `binding_name` being a member of the
enclosing group's binding names.

That guard is keyed on a property of the **walk position**, not of the lambda.
The group's names are in scope only while the walk is inside the group; they are
reset when descending into a nested `$let` body, which is what makes a
`$letrec`-bound function inlinable from outside its own group. Because the pass
re-walks a call site with the *call site's* group names after inlining a body, a
self-call inside a freshly inlined body is not recognised: the body's own group
names are no longer in scope at the new position. The call would be inlined
again, and again, without bound.

**Captures.** A lambda's capture lists were computed once, when the lambda was
built, from the body it had then. Inlining a body that reads a captured name
into a caller makes that name free in the caller too, and the downstream closure
conversion binds a lambda's names *only* from its capture lists. A name the body
reads but the lists omit is unresolvable. The inliner therefore refused to inline
any lambda bound elsewhere that had captures of any kind.

That rule was a significant missed optimisation: a small `$let`-bound lambda
closing over a few module-level bindings — the common shape for a helper selected
once per call — was rejected outright, leaving a closure allocation and an
indirect call at every call site.

The two guards were load-bearing for each other in a way neither stated. The
position-based recursion guard was unreachable only because the capture rule
happened to reject every lambda that has `sibling_free_vars`, which includes every
self-recursive one.

## Decision

Both guards are keyed on the lambda, not on the walk position.

**Recursion.** The inliner refuses to inline a lambda whose `binding_name`
appears in its own `sibling_free_vars`: a lambda that names itself among its
sibling captures can reach itself. The `letrec_names` set is removed from the
pass entirely; it existed only to serve the position-based guard, and no other
check reads it.

Mutual recursion is not detected by this test. A lambda that calls a sibling
which calls it back captures that sibling, and the sibling-capture rule below
already refuses to inline a lambda bound elsewhere that has sibling captures. The
two rules together cover both shapes.

**Captures.** The inliner recomputes each lambda's `sibling_free_vars` and
`outer_free_vars` from its optimised body, instead of copying the originals. A
lambda bound elsewhere is therefore rejected only when it has *sibling* captures;
outer captures are permitted.

The sibling/outer split is decided by the `$letrec` group the lambda is a member
of, so the walk threads that group's binding names down to its binding values,
and every lambda body resets them to empty. This is the same split the IR builder
makes when it first builds the lambda.

A sibling capture remains a rejection. It names a binding in the lambda's own
`$letrec` group, which does not exist at the call site, so it cannot be reproduced
there by any recomputation.

A direct lambda application (`((lambda ...) arg)`) is exempt from both guards: the
lambda is written at the call site, so it cannot be recursive in the same sense
and every capture is already in scope.

`_free_vars` is load-bearing for the capture rule and must stay exact. It was
written for a check that only ever caused a rejection, where an over-approximation
cost a missed optimisation. It now drives capture lists, so a name it wrongly
reports becomes a spurious capture and a name it misses becomes an unresolvable
one. In particular a builtin call's function position names an opcode rather than
a binding, and must not be reported.

## Alternatives considered

### Keep the position-based recursion guard and fix the re-walk

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

### Keep the coarse capture rule and accept the missed optimisation

Rejected because the cost is on the common shape. A helper selected once per call
is exactly what the inliner exists to remove, and rejecting every capturing
lambda rejects most of them.

### Identity-based capture compatibility

Reject only captures that do not resolve to the same binding at the call site.
This was implemented and found unsound. The inliner's `scope_stack` is a
name-based view of the IR tree, but the CFG builder converts each lambda into a
separate function whose only bindings are its parameters plus the free variables
it *declares*. A name the inliner can see is therefore not necessarily a name the
enclosing function captures, and inlining a body that reads it produced an
unresolved variable in the CFG builder.

This is not a fixable detail of the check. It is a mismatch between the name-based
view the inliner works in and the declared-capture view the CFG builder works in.
Do not revisit this approach without first addressing closure conversion.

## Consequences

### Positive

- Both guards are correct under re-walking, because they read the lambda rather
  than the walk position. The fixed-point loop can no longer re-inline a
  self-call it has just substituted.
- The two guards are no longer load-bearing for each other, so they can be
  reasoned about, and relaxed, independently.
- A lambda that closes over outer bindings is inlined, removing a closure
  allocation and an indirect call from the call site.
- The capture lists are correct by construction rather than by convention: they
  are derived from the body that will actually be compiled, so they cannot drift
  from it.
- The pass is smaller: a parameter threaded through nine methods is gone.

### Negative

- A self-recursive lambda is rejected even at a call site where inlining it once
  would be sound, because the guard cannot distinguish "this call site" from
  "the call sites the substitution will create". This is the same coarseness
  ADR-0031 accepted, now stated in terms of the lambda.
- The recursion guard reads a field that is only populated for a lambda bound in
  a binding form. A lambda with no `binding_name` is never considered
  self-recursive, which is correct: an unbound lambda cannot refer to itself by
  name.
- `_free_vars` changes from a conservative filter into a correctness-critical
  analysis. Its exactness is now a requirement, not a preference.
- The capture rule stops being a blanket guard on inlining. Previously-opaque
  closures now reach later passes, which exposes IR shapes those passes have
  never seen. This is a real cost: it has already surfaced latent defects in the
  VCode peephole optimiser, and further ones are plausible.
- The sibling/outer split must be threaded through the whole walk. A lambda body
  that forgets to reset the enclosing group names would misclassify an outer
  capture as a sibling one, or the reverse.

## Related

- [ADR-0031](0031-inliner-does-not-inline-recursive-calls.md) is superseded by
  this decision. Its requirement — do not inline a recursive call — remains in
  force; both its mechanism for enforcing it and the coarse capture rule that
  accompanied it are replaced here.
- [ADR-0008](0008-letrec-is-genuine-mutual-recursion.md) establishes that a
  `$letrec` group is a single strongly-connected component, which is why a
  sibling capture is the right signal both for recursion and for a capture that
  cannot be reproduced at a call site.
- [ADR-0032](0032-single-binding-tail-recursive-letrecs-become-loops.md) converts
  a single-binding self-recursive group to a loop before the inliner runs, so the
  common self-recursive shape never reaches the recursion guard at all.
