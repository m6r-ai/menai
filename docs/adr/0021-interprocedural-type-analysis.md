# ADR-0021: Interprocedural flow-based type analysis

Date: 2026-09-17  
Status: Accepted

## Context

Struct field access in the surface language is by symbol name: `(struct-get p 'x)`
looks up the field index in the struct type's hash table at runtime. Index-based
opcodes (`struct-ref`, `struct-set-ref`) already existed but were only reachable
through the explicit `struct-ref`/`struct-set-ref` builtins where the user supplies
an integer index.

The compiler can resolve a symbol field name to a constant index if it knows the
receiver's specific struct type. The CFG type-propagation pass tracks a coarse type
per value ('struct', 'integer', ...) but not the struct type identity, so it cannot
resolve field access.

The motivating workload reads struct fields inside functions that receive the struct
as a parameter — for example a `distance` function taking two points. The struct type
is only knowable at the call sites, not inside the callee. Local (intraprocedural)
analysis therefore resolves nothing for the code that matters; the analysis must be
interprocedural.

Menai is dynamically typed with no annotations, so any such analysis is an
optimisation, not a type system: it must never change observable behaviour, and where
it cannot prove a type it must fall back to the existing runtime path.

## Decision

Add a whole-program flow-based type analysis (`MenaiCFGInterprocTypeAnalysis`) that
computes a type fact for every SSA value and propagates argument facts into callee
parameters to a fixed point. Where a struct field access has a receiver whose struct
type identity is proven and a constant-symbol field argument, rewrite it to the
index-based form.

The analysis is flow-based, not Hindley-Milner. It degrades gracefully: a value whose
type cannot be proven is simply unknown at that point, and the runtime path is used.
It does not generalise over polymorphic uses — a parameter called with two different
struct types is unknown, not polymorphic.

The type fact lattice has three levels: BOTTOM (no information), a known kind, and ANY
(conflicting kinds). Keeping "no information" and "conflicting information" distinct
is essential: if both mapped to a single unknown element, joining two different known
kinds would move *down* the lattice and the parameter-fact fixed point would oscillate
rather than converge.

The distinction also governs soundness. A value whose type is genuinely unknown — a
builtin result whose signature does not fix the result type (struct-ref, dict-get,
list-first), or an unresolvable call result — is ANY, because it could be anything and
a guard or an index resolution must not rely on it. BOTTOM is only the fixed-point
identity: it is the initial value and the join identity, not a claim that a value is
untyped. Free variables and globals are left at BOTTOM: making them ANY would poison
the return-fact fixed point, because a function that returns a captured value creates a
cycle through its own parameter, and ANY in that cycle never resolves back to the
precise type the real call sites establish.

The analysis pass stores its per-value facts on each function and a separate guard
insertion pass consumes them. Guard insertion needs only the coarse type name, so it
reads `fact.kind`. Splitting the two keeps a single source of facts and lets the
interprocedural analysis run before guards are inserted, so guards can be suppressed
where the interprocedural facts prove a type. The prelude is spliced into every
program as ordinary lexical bindings, so its functions are analysed like any other
function: every call site is present in the compilation and parameter inference
applies to them.

The fixed point propagates three kinds of fact, all of which are required for the
analysis to fire on real code:

- Argument facts into parameters: A call's argument facts join into the callee's
  parameter facts.
- Return facts out of calls: Each function has a return fact, the join over its
  return points; a call's result fact is the callee's return fact. This is essential
  because a struct type usually enters a call chain through a function's return value
  rather than a constructor at the call site: a recursive search passes its cube
  parameter through functions that each return a cube.
- Callee resolution: A call's callee is an SSA value, resolved from four sources:
  a `make_closure` result; a phi joining such results; a `dict-get` of a constant key
  from a dict whose matching value denotes a function (this is how a module's exported
  functions are reached, since a module is a dict of functions); and a free variable,
  resolved to whatever the corresponding capture denotes in the parent function (this
  is how a function reaches a letrec sibling, which is captured rather than created
  locally). Calls through function-valued parameters are not resolved. There is no
  function cloning or specialisation.

## Alternatives considered

### Hindley-Milner type inference

HM reconstructs the most general type of every expression, including polymorphic ones.
It was rejected because Menai is dynamically typed: HM would be unsound as a type
system here, and as an analysis it degrades globally (one un-inferable construct
poisons a whole component) rather than gracefully. Menai's containers are inherently
`any`-typed, so HM's polymorphism advantage buys little on the code that matters. HM
would be the right foundation if Menai were becoming a statically typed language, but
that is a language-design decision, not an optimisation one.

### Intraprocedural analysis only

Resolving field access only where the struct type is locally known (constructor sites,
`struct-set` results, `struct-is-instance?` refinement) would not help the motivating
workload, where the struct arrives as a parameter. It was rejected as insufficient.

### Function cloning / specialisation

Cloning a callee per call-site type signature would recover polymorphism without a
type system, and is safe in a pure language. It was rejected for this change under
YAGNI: the added code-size and complexity are not justified until profiling shows the
propagation-only approach is too imprecise. It can be layered on later.

### A single unknown element in the lattice

Using one "unknown" for both "no information" and "conflicting information" is
simpler but non-terminating: the interprocedural fixed point oscillates. The three-level
lattice is required for convergence.

## Consequences

### Positive

- Struct field access through a parameter is resolved to a constant index when every
  call site agrees on the struct type, eliminating the runtime hash lookup.
- The analysis is a pure optimisation: unprovable cases fall back to the existing
  symbol-based path, so behaviour is unchanged.
- The fact lattice and propagation generalise to any type, not just structs, so later
  work (e.g. guard elimination for numeric types) can build on the same infrastructure.

### Negative

- The analysis is whole-program, so it is a new kind of CFG pass (see ADR-0020) and
  must traverse the call graph itself.
- It does not resolve polymorphic parameters; a function called with two struct types
  gains nothing. Recovering that requires cloning, which is not implemented.
- It does not resolve calls through function-valued parameters that are not one of the
  four resolvable sources (e.g. a function stored in a list and fetched by index, or
  passed as an argument and called indirectly).
- Struct type identity is carried as a `MenaiStructType` object in the fact, so facts
  are not trivially serialisable; this is acceptable because facts are transient within
  a single compilation.
