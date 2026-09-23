# ADR-0027: Recursion-cycle parameter grounding in the interprocedural type analysis

Date: 2026-09-23  
Status: Accepted

## Context

ADR-0021 introduced the interprocedural flow-based type analysis and recorded a
rule for call sites inside a recursion cycle: a parameter is grounded only by a
call site outside the function's recursion component, and a parameter with no such
external grounding stays at BOTTOM. The stated justification was that "the value
the function is first called with is unconstrained" when it is only reached from
inside the cycle.

That rule conflates two distinct situations, because BOTTOM is both the
fixed-point identity and the value a call site contributes when its argument's
type is unknown:

- the function has no call site outside its recursion component at all; and
- the function has such a call site, but the argument passed there has an unknown
  type.

The first situation is not a soundness problem: if the whole recursion component
is entered through an externally-grounded call site, then every function in it is
reachable from that entry, and the facts that flow around the component are
grounded by it. Treating the first situation like the second discards facts that
are sound, and it defeats the analysis on exactly the workload ADR-0021 was
written for: a struct threaded as a parameter through a group of mutually
recursive functions, where only the entry function is called from outside the
group.

The second situation is a genuine soundness problem, and conflating it with the
first is not merely conservative. BOTTOM is the join identity, so a call site
whose argument is unknown is silently dropped by the join. A function whose
parameter is reached only by in-cycle calls, one of which passes an unknown value,
can therefore have a concrete fact from another call site survive the join and be
reported as proven, even though at runtime the unknown value can flow in. This is
a miscompile, not a missed optimisation: the field access is rewritten to a
constant index chosen for one struct type and reads the wrong field when a value
of another struct type arrives.

## Decision

The effective fact of a parameter is computed from three inputs, tracked
separately:

- the join over call sites outside the function's recursion component;
- the join over call sites inside it; and
- whether any call site outside the component exists for that parameter.

A parameter that has an external call site uses the join of the external and
internal facts. A parameter that has no external call site uses its internal
facts, provided its recursion component is externally grounded — that is, the
component contains at least one function with an external call site. A parameter
whose only external call site passes an unknown value is unconstrained and is not
grounded: an unknown argument is not the same as no call site.

The distinction between "no information" (BOTTOM) and "conflicting information"
(ANY) in the fact lattice is unchanged and remains essential for convergence. What
changes is which of the two a call site with an unknown argument contributes to
the effective parameter fact, so that such a call site degrades the parameter
rather than being ignored.

This is a correctness issue first and an optimisation second. The miscompile above
exists independently of whether the recursion-component grounding is enabled.

## Alternatives considered

### Keep the ADR-0021 rule and require external grounding per parameter

This is the status quo. It is sound only by accident: it happens to avoid the
miscompile in the cases covered by the existing tests, but the miscompile is still
reachable, and it forfeits the optimisation on mutually-recursive groups whose
inner functions are only reached from a grounded entry. It was rejected because it
is both less correct and less useful than tracking the three inputs separately.

### Ground every parameter from its internal facts

Dropping the external-grounding requirement entirely would ground a recursion
component that has no external entry at all, including one reachable only through
a call the analysis cannot resolve. Such a component's facts would be derived from
values it produces itself, which is not sound. The externally-grounded condition
is what makes the internal facts traceable to a real entry.

### Function cloning / specialisation

Cloning a callee per call-site type signature would recover precision without
these distinctions. It remains rejected under YAGNI for the same reasons as in
ADR-0021: the added code size and complexity are not justified for this change.

## Consequences

### Positive

- The analysis fires on struct state threaded through a group of mutually
  recursive functions, which is the motivating workload from ADR-0021.
- The miscompile where an unknown in-cycle argument fails to degrade a parameter
  is fixed: an unknown argument now degrades the parameter instead of being
  dropped by the join.
- The rule is stated in terms of what is actually known about each parameter,
  rather than in terms of where its call sites happen to sit.

### Negative

- The parameter-fact computation needs one more piece of per-parameter state (the
  presence of an external call site) and the recursion-component grounding flag,
  so it is no longer a pure function of the two joins.
- The analysis still does not resolve calls through function-valued parameters, so
  a recursion component reachable only through such a call is not grounded and
  gains nothing.
