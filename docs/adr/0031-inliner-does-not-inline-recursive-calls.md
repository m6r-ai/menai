# ADR-0031: The IR inliner does not inline recursive or mutually-recursive calls

Date: 2026-09-27  
Status: Accepted

## Context

The IR inliner substitutes a lambda body at a call site when the call target
resolves to a known lambda. Inlining is always safe in Menai because the language
is pure, but inlining a recursive function is not terminating. The pass runs to
a fixed point, and substituting a self-recursive body would reintroduce the same
call it just removed.

The inliner needs a cheap, sound way to reject recursive and mutually-recursive
call sites. At the IR level a `letrec` group is a single strongly-connected
component of mutually-recursive bindings (ADR-0008), so any call from inside a
`letrec` group to one of that group's bindings may be recursive. Distinguishing
the genuinely recursive call from a non-recursive sibling call would require a
call-graph analysis the pass does not otherwise need.

## Decision

The inliner refuses to inline a call to a `letrec`-bound function made from
within that `letrec` group. The check is on the target's `binding_name` being a
member of the enclosing group's binding names.

The guard is scoped to the enclosing group only. The set of group names is
populated when walking a `letrec` and reset to the empty set when descending into
a nested `let` body, so a `letrec`-bound function is inlinable at call sites
outside its own group.

A direct lambda application (`((lambda ...) arg)`) is exempt from this check: the
lambda is written at the call site and cannot be recursive in the same sense.

## Alternatives considered

### A precise call-graph analysis to detect only genuine recursion

Rejected as disproportionate. It would let the inliner inline non-recursive
sibling calls, but it adds a whole-program analysis to a pass whose value is
local rewriting, and the non-recursive sibling case is rare.

### Inline recursive calls with a recursion-depth limit

Rejected: it would produce a bounded unrolling of a loop, duplicating code to
gain a fixed amount of unrolling.

## Consequences

### Positive

- The recursion check is a field comparison, not a tree walk, so it costs
  nothing for the common non-recursive case.
- A `letrec`-bound function remains inlinable from outside its group.

### Negative

- The guard is coarse: a non-recursive call to a `letrec` sibling made from
  within the group is not inlined, even though it would be sound. This is the
  cost of not having a call-graph analysis.
- A self-recursive `letrec` binding is never inlined, so it remains a closure
  allocation and an indirect call unless another pass removes it.
