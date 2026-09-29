# ADR-0035: Function provenance must follow containers

Date: 2026-09-29  
Status: Accepted

## Context

The interprocedural type analysis proves a parameter's type from the arguments
at its call sites, and the struct field rewrite consumes those facts. A fact
that is more precise than reality is therefore a miscompile, not a missed
optimisation.

The analysis resolves which function a call denotes before it can attribute a
call site to a callee. A function value that reaches a call by a route the
resolution does not follow is invisible: the call contributes no call site, the
callee's parameter is never degraded by it, and the fact can be over-precise.

Containers are such a route. A function stored in a list and fetched back out
can be called, and the analysis did not connect the fetched value to the stored
function. A function that escapes this way can be called with a value of any
type while the analysis reports a proven type for its parameter, so the field
access is rewritten to a constant index and the runtime guard is omitted. The
result is a VM crash on valid source, not merely a lost optimisation.

ADR-0029 identified this class of gap and deferred it, restricting predicate
folding to locally-proven facts in the meantime. It recorded the gap as latent
for guard insertion. It is not latent: guard insertion omits the guard on the
strength of the same fact, so the struct rewrite path is unmitigated.

## Decision

Function provenance must follow containers. If a value is stored into a
container, the container must be treated as possibly denoting the functions the
value denotes; if a value is fetched from a container, the result must be
treated as possibly denoting the functions the container holds. The same holds
for every builtin that moves a value into or out of a container.

The provenance relation is consequently a *may* relation over a set of
functions, not an exact mapping to one, and it is iterated to a fixed point.

A function whose value can reach a call site the analysis cannot resolve must
have its parameters treated as unconstrained. This is the same degradation the
analysis already applies to a parameter reached by an unknown argument, and it
is propagated by the existing fixed point rather than by a separate pass.

## Alternatives considered

### Restrict the rewrite to locally-proven facts

Sound without following containers, but it forfeits the interprocedural case
entirely: a struct threaded as a parameter is never locally proven, and that is
the motivating workload of ADR-0021.

### Guard the rewritten access

Restores the safety invariant but adds a runtime check to the access the rewrite
exists to make cheaper, and treats the symptom rather than the unsound fact.

### Check the operand in the VM opcode

Contradicts the VM's design, in which operational opcodes omit type checks and
rely on the compiler having inserted guards.

### Treat only the container's escape, not its contents

Does not connect a stored function to a call through a value fetched back out.
The fetched value's provenance is what must be tracked, not the container's fate.

## Consequences

### Positive

- A function that escapes has its parameters left unconstrained, so a field
  access on them is not rewritten to an index and its runtime guard is kept. A
  call with a value of another type raises a type error instead of reading a
  non-struct as a struct.
- The analysis remains a pure function of the program: where a type cannot be
  proven, the existing runtime path is used.

### Negative

- A function that escapes loses all parameter precision, so a struct threaded
  through such a function is no longer rewritten. This is the price of soundness
  and is confined to functions that actually escape.
- Every builtin that can move a value into or out of a container must be
  accounted for. A missing one lets a function that does escape appear not to,
  which is a miscompile rather than a missed optimisation. That list is
  correctness-critical and must be kept complete as builtins are added.
- A function returned to the host escapes, because the host may call it with a
  value of any type, so its parameters are left unconstrained.

## Related

- [ADR-0021](0021-interprocedural-type-analysis.md) and
  [ADR-0027](0027-recursion-cycle-parameter-grounding.md) establish the analysis
  this decision makes sound.
- [ADR-0029](0029-predicate-fold-locally-proven-only.md) records the same gap
  from the predicate-folding side and defers its fix to this decision.
