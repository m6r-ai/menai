# ADR-0045: A match's desugared form is bounded

Date: 2026-10-08  
Status: Accepted

## Context

A `match` is desugared into a chain of tests, one per arm. A literal arm becomes a
type guard plus an equality test. A list pattern `(a b c)` becomes a `list?` test, a
`list-length` test, and one `list-nth` extraction per element, each feeding its own
sub-pattern test. The generated form is therefore considerably larger than the pattern
that produced it, and the expansion compounds: a nested pattern's sub-patterns expand
in turn, and each arm is nested inside the failure path of the arm before it.

The growth is super-linear in the number of arms and the nesting depth of the patterns.
Measured on a synthetic match of increasing size, the node count of the desugared body
grows roughly cubically:

| arms | desugared nodes |
|---|---|
| 2 | 153 |
| 4 | 640 |
| 6 | 1,631 |
| 8 | 3,294 |
| 10 | 5,797 |

The expansion is not merely wasteful. It is unbounded, and the cost of every pass after
the desugarer is super-linear in the size of the tree it is handed. A match that expands
to a hundred thousand nodes produces a control-flow graph of tens of thousands of
blocks, and the CFG pass manager's cost on that graph is high enough that the compiler
becomes unusable — not diverging, but taking so long that it is indistinguishable from a
hang.

This was observed directly. `regexp.menai` contains a ten-arm `match` over the variants
of a parsed-regexp node. When the arms are enum patterns, the match is fused into a jump
table and the function's body is 258 nodes. When the same arms are list patterns — which
is what an enum pattern degrades to when its head does not resolve to an enum type — the
body expands to 81,759 nodes, a 317× increase, and the compile does not complete in
practical time.

ADR-0043 already records the mechanism, under the module-renaming invariant:

> a list pattern compiles to a `list?`/`list-length`/`list-nth` test chain, so a `match`
> over an enum degrades from a jump table into a long comparison chain and can explode
> the CFG of any function it is inlined into.

What was not recorded is that "explode the CFG" means the compiler stops being usable,
and that nothing bounds the expansion. The desugarer has no limit on the size of the
form it produces.

## Decision

The desugared form of a single `match` is bounded. A match whose desugared form exceeds
`MAX_DESUGARED_MATCH_NODES` (4096) AST nodes is a **compile-time error**.

The error names the match, reports the expanded size and the limit, and suggests reducing
the number of arms or the nesting depth of the patterns.

The bound is on the **desugared size of one match**, not on the size of the source
pattern. The source pattern is a poor proxy: a short pattern can expand enormously, which
is the whole problem. The desugared size is what the later passes pay for, so it is the
quantity to bound.

The limit is chosen with headroom over real code. The largest match in the entire
standard library — in `regexp.menai` — desugars to 290 nodes, so 4096 leaves more than an
order of magnitude of room for hand-written code while rejecting the pathological case
decisively. The value is a round power of two for readability; there is no technical
reason it must be one.

The check is a compile-time error rather than a warning, following the precedent of
`regexp-repeat-limit` in the standard library: an expansion that is impractically large
is rejected rather than silently accepted. A warning would leave the pathological compile
in place for anyone who ignored it.

## Alternatives considered

### Fix the expansion so it is linear

The expansion is super-linear because each nested list pattern re-extracts and re-tests
its elements, and because arms nest inside the failure path of the preceding arm. A
smarter desugaring could share the `list?`/`list-length` test across arms of the same
arity, and hoist repeated element extractions.

Rejected as the immediate fix, not in principle. It is a redesign of list-pattern
desugaring with real risk of changing matching semantics, and it does not remove the need
for a bound: a user can still write a match with ten thousand arms, and the desugarer
must still refuse to expand it without limit. A bound is required either way. The
expansion can be improved later, on top of the bound.

### Bound the source pattern instead of the desugared form

Rejected. The source pattern is not a proxy for the expansion. The pathological
`regexp.menai` match is ten short arms; its source is unremarkable and its expansion is
81,759 nodes. A bound on the source would either be too loose to catch it or too tight to
permit legitimate code.

### Bound the whole program rather than each match

Rejected for this decision. A whole-program bound is a complementary safeguard, and may
be worth adding if aggregate blow-up is observed from many individually-acceptable
matches. It is not a substitute for a per-match bound, because it cannot attribute the
failure to the construct that caused it. The per-match bound gives an error at the match,
which is actionable; a whole-program bound can only report that the program is too large.

### A warning rather than an error

Rejected. The consequence of exceeding the bound is that the compiler becomes unusable,
so a warning would be a promise the compiler cannot keep. This mirrors the reasoning in
ADR-0043 that non-exhaustive matches must be an error rather than a warning.

## Consequences

### Positive

- The desugarer can no longer emit an unboundedly large form for a single match. The
  pathological compile is rejected at the point of cause, with an actionable message.
- The failure is a clear compile-time error naming the match, rather than an unexplained
  slow compile or an apparent hang.
- The bound is on the quantity the later passes actually pay for, so it bounds the
  compile cost, not merely the source size.
- No legitimate standard library code is affected: the largest match in the library is
  290 nodes against a limit of 4096.

### Negative

- A match that would previously have compiled slowly now fails to compile. This is a
  behaviour change for any program with a sufficiently large match, though no such
  program exists in the standard library.
- The limit is a fixed constant. A program with a match between 290 and 4096 nodes is
  accepted, and one above is rejected, with no graduated response. The value may need
  revisiting if legitimate code approaches it.
- The bound does not make the expansion linear. A match just under the limit still
  expands to thousands of nodes and costs the later passes accordingly. Improving the
  expansion remains worthwhile, and is not foreclosed by this decision.
