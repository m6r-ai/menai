# ADR-0058: Function-valued parameters resolve to their callee may-sets

Date: 2026-10-23
Status: Accepted

## Context

ADR-0021 introduced the interprocedural type analysis. ADR-0027 refined how a
parameter's fact is grounded across recursion cycles. Both recorded the same
limitation as a negative consequence: the analysis did not resolve a call whose
callee is a function-valued parameter. A parameter was not one of the resolvable
sources (a `make_closure` result, a phi of them, a free variable, or a value
fetched from a container), so a call through it was unresolvable and returned ANY.

That limitation loses types that are provable in principle. A higher-order helper
that applies a function-valued parameter — a dispatch or fold helper — returns
whatever the functions passed into it return. When every such function returns the
same struct type, the helper's return type is that struct type, and a field access
on the call result could be resolved to a constant index. Because the call was
unresolved, the result was ANY, the field access stayed symbol-based, and the type
was lost for every caller downstream.

The Rubik's cube vector benchmark is a concrete case. Its `turn-apply` helper calls
a `rotate-cw` parameter that is only ever passed the six `move-*` functions, all of
which return the same `cube-repr` struct. The helper's return type was ANY, so the
`heuristic` function's cube parameter was ANY and its six field reads stayed
symbol-based, even though the struct type is provable.

The fact lattice already distinguishes "no information" (BOTTOM) from "conflicting
information" (ANY), and ADR-0027 shows why that distinction is essential for
termination and soundness. Resolving parameter callees needs the analogous
distinction for the may-set of functions a value can denote.

## Decision

A parameter denotes the union of the functions passed into that parameter position
at any call site. This is a may-set, iterated to a fixed point together with the
existing value resolution (closures, phis, free variables, containers). A call
through a function-valued parameter resolves to the functions that reach it, and
its result fact is the join of their return facts.

The may-set carries an "unknown" flag alongside the known functions. A value is
unknown when it may denote a function the analysis cannot name, in addition to any
known functions. The flag is set for:

- a parameter that receives a value whose known callee-set is empty (an argument
  the analysis cannot name);
- a parameter of a function that escapes, because the host or an unresolved call
  site may pass it a function the analysis cannot see.

The unknown flag propagates through phis and containers exactly as the known set
does: a phi is unknown if any incoming value is, a container is unknown if any
contributing operand is, and a value fetched from a container is unknown if the
container is. A call through a value that is unknown returns ANY, even when its
known set is non-empty.

Tracking only the known set would be unsound. A phi that joins a known closure with
an unnamed value denotes the closure *and possibly something else*; resolving it to
the known closure alone would report a return type more precise than reality, and
the struct field rewrite and guard insertion both consume that fact. The unknown
flag is what keeps the may-set honest.

The unknown flag is settled after the callee-resolution fixed point converges,
because during the fixed point an argument's callee-set may be empty only because
it has not been resolved yet. Flagging unknown during the fixed point would latch
unknown onto a parameter whose argument later resolves to a concrete function.

## Alternatives considered

### Leave the limitation in place and restructure higher-order code

A caller can avoid the higher-order call by dispatching on a tag directly, as the
benchmark's `apply-move` already does. This recovers the type for that one program
without a compiler change. It was rejected as the general answer because
higher-order helpers are common and idiomatic, and requiring every caller to
specialise them defeats the abstraction. It remains a valid local workaround.

### Track only the known callee-set, without an unknown flag

This is simpler and resolves the common case, but it is unsound: a phi of a known
function and an unnamed value would resolve to the known function alone. The
unknown flag is required for the same reason BOTTOM and ANY are distinct in the
fact lattice — "may denote these functions" and "may denote these and possibly
others" are different claims.

### Function cloning / specialisation

Cloning a callee per call-site signature would recover precision without a may-set.
It remains rejected under YAGNI for the same reasons as in ADR-0021 and ADR-0027:
the added code size and complexity are not justified, and the may-set recovers the
type for the workloads that matter without it.

## Consequences

### Positive

- A call through a function-valued parameter resolves to the functions that reach
  it, so a higher-order helper's return type is proven when every function passed
  into it returns that type. Field access on the call result is resolved to a
  constant index.
- The analysis fires on the Rubik's cube vector benchmark's `turn-apply` and
  recovers the `cube-repr` type through `heuristic`, which ADRs 0021 and 0027
  recorded as out of reach.
- The limitation recorded in ADR-0021 and ADR-0027 is removed.

### Negative

- The callee resolution is now a global fixed point across functions rather than a
  per-function one, because parameter-callee flow crosses function boundaries. It
  is more work per compilation than the previous per-function resolution.
- A parameter may denote several functions, so its call result is the join of their
  return facts; a parameter fed functions of different return types still loses the
  type identity, exactly as a parameter fed two struct types does.
- The unknown flag adds per-value state that must be propagated and must be
  included in the fact memoisation signature; omitting it from the signature would
  return stale facts after saturation changes the flag.
