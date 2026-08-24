# ADR-0016: Reference counting in the C VM

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

The C VM uses reference counting. Every value carries a reference count
(`ob_refcnt`). `menai_value_retain` increments the count; `menai_value_release`
decrements it and frees the value when the count reaches zero. This works in
concert with the pool allocator (see ADR-0014) — freed values return their
memory to the pool's free-list rather than the system allocator.

Menai's purity makes reference counting a viable strategy. Without mutation,
the cycles that plague refcounting in impure languages are far more constrained.
The only type that can introduce a reference cycle is the function/closure type,
and even that is difficult to achieve in practice. Data structures like lists,
dicts, and sets cannot form cycles because they are immutable and constructed
from values that already exist — there is no way to create a list that contains
itself.

## Alternatives considered

### Tracing garbage collection

A tracing GC (mark-and-sweep, copying, or generational) would automatically
handle cyclic references and could compact memory. However, tracing GC
introduces pause times — the program stops while the collector scans the heap.
For a VM designed to execute short computations with predictable latency, these
pauses are undesirable. A tracing GC also does not naturally return freed
memory to the pool allocator's hot free-lists, since collection happens in
batches rather than at the point a value becomes unreachable.

### Hybrid refcounting + tracing

Some systems use reference counting for immediate reclamation of short-lived
values and a tracing collector as a backstop for cyclic references. This adds
the complexity of both approaches. The pause-time problem of tracing remains,
and the system must manage two reclamation mechanisms.

## Consequences

### Positive

- Memory is reclaimed precisely at the moment a value becomes unreachable — no
  delay, no batch collection, no pause times.
- Freed memory returns immediately to the pool allocator's free-list, where it
  is likely to still be hot in the CPU cache for reuse by the next allocation.
- Deallocation is deterministic and predictable, which is valuable for a VM
  that may be embedded in systems with latency requirements.
- The reference count is a simple integer increment/decrement, which is cheap
  compared to a full heap scan.

### Negative

- Reference counting cannot handle cyclic references. The current implementation
  does not address this: if a function/closure cycle is created, the values
  involved will leak because neither reference count reaches zero. This is a
  known limitation. A tracing collector will be added to handle this case,
  complementing the refcounting scheme rather than replacing it.
- Every assignment and scope change requires retain/release operations, adding
  overhead to every value manipulation in the VM.
- The `menai_value_retain` exception (see ADR-0006) — where retain does not need
  `MenaiVMState *` — must be carefully understood by anyone writing C VM code,
  as it is the sole deviation from the convention that all memory functions take
  the state pointer.
