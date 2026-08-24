# ADR-0016: Reference counting with closure cycle collection in the C VM

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

The C VM must manage memory for all Menai runtime values: integers, strings,
lists, dicts, sets, structs, functions, etc. These values are created and
discarded frequently during execution. The memory management strategy affects
performance, latency, and implementation complexity.

The two main approaches are reference counting (each value tracks how many
references point to it; when the count reaches zero, the value is freed
immediately) and tracing garbage collection (periodically scan the heap from
roots to find live objects, then free everything else).

## Decision

The C VM uses reference counting as its primary memory management strategy.
Every value carries a reference count (`ob_refcnt`). `menai_value_retain`
increments the count; `menai_value_release` decrements it and frees the value
when the count reaches zero. This works in concert with the pool allocator
(see ADR-0014) — freed values return their memory to the pool's free-list
rather than the system allocator.

Menai's purity makes reference counting a viable strategy. Without mutation,
the cycles that plague refcounting in impure languages are far more constrained.
The only type that can introduce a reference cycle is the function/closure type.
Data structures like lists, dicts, and sets cannot form cycles because they are
immutable and constructed from values that already exist — there is no way to
create a list that contains itself.

Cycles arise from `letrec`: the compiler emits `MAKE_CLOSURE` followed by
`PATCH_CLOSURE`, which fills sibling capture slots with sibling closures. A
self-recursive `letrec` produces a closure whose capture slot points at itself;
mutually recursive `letrec` groups produce closures that capture each other.
When the `letrec` scope exits, the register holding each closure is released,
but each closure still holds refcount >= 1 from its siblings'/own capture slots,
so the refcount never reaches zero and the closures leak.

To reclaim these cyclic closures, a targeted mark-and-sweep cycle collector
runs at the end of every `menai_vm_execute_native` call and at VM teardown. The
collector tracks all live closures in a per-instance registry and traces only
closures — not the general heap. It marks all closures reachable from globals
and the execute result, then sweeps unreachable closures in four strictly
separated phases: mark, partition, break internal edges (bare refcount
decrements only), and free (destruction only). Closures with external
references that survive edge-breaking (refcnt > 0) are returned to the registry
rather than freed. See the architectural invariant in `AGENTS.md` for the
refcnt guard constraint.

## Alternatives considered

### Tracing garbage collection

A general tracing GC (mark-and-sweep, copying, or generational) would
automatically handle cyclic references and could compact memory. However,
tracing GC introduces pause times — the program stops while the collector
scans the heap. For a VM designed to execute short computations with
predictable latency, these pauses are undesirable. A general tracing GC also
does not naturally return freed memory to the pool allocator's hot free-lists,
since collection happens in batches rather than at the point a value becomes
unreachable. Since closures are the only cycle-forming type, a general heap
scan is unnecessary.

### Hybrid refcounting + tracing

Some systems use reference counting for immediate reclamation of short-lived
values and a tracing collector as a backstop for cyclic references. This adds
the complexity of both approaches. The pause-time problem of tracing remains,
and the system must manage two reclamation mechanisms. The implemented
design is a targeted variant of this approach: the cycle collector traces only
closures (not the general heap), runs per-execute-call rather than on a
threshold, and reuses the existing refcount infrastructure for edge-breaking
rather than maintaining a separate tracing machinery.

## Consequences

### Positive

- Memory is reclaimed precisely at the moment a value becomes unreachable. There no
  delays, no batch collections, and no pause times.
- Freed memory returns immediately to the pool allocator's free-list, where it
  is likely to still be hot in the CPU cache for reuse by the next allocation.
- Deallocation is deterministic and predictable, which is valuable for a VM
  that may be embedded in systems with latency requirements.
- The reference count is a simple integer increment/decrement, which is cheap
  compared to a full heap scan.
- The targeted cycle collector handles the one case where refcounting fails
  (cyclic closures from `letrec`) without the cost or complexity of a general
  tracing GC.

### Negative

- Every assignment and scope change requires retain/release operations, adding
  overhead to every value manipulation in the VM.
- The `menai_value_retain` exception (see ADR-0006) (where retain does not need
  `MenaiVMState *`) must be carefully understood by anyone writing C VM code,
  as it is the sole deviation from the convention that all memory functions take
  the state pointer.
