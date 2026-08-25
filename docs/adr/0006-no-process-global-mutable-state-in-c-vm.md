# ADR-0006: No process-global mutable state in the C VM

Date: 2026-08-24  
Status: Accepted

## Context

The C VM must be safe to instantiate multiple times (e.g. for parallel execution
or testing). Process-global mutable state — file-level mutable statics — would
make VM instances interfere with each other, causing race conditions, memory
corruption, or incorrect results.

## Decision

All mutable VM state (pool allocator free-lists, singletons, prelude globals) is
owned by `MenaiVMState`, a per-instance struct allocated by the Python `MenaiVM`
wrapper. A `MenaiVMState *` pointer is passed explicitly as the first argument
to every C function that allocates, frees, or touches singletons. There are no
file-level mutable statics in the C VM outside the bridge layer (which has only
read-only Python type references fetched at module init).

`menai_value_retain` is the one exception: it only increments `ob_refcnt` and
does not need `MenaiVMState *`. Every other refcount or allocation function
(`menai_value_release`, `menai_value_free`, `menai_alloc`, `menai_free`, all
`alloc_menai_*` constructors, `menai_none`, `menai_boolean_true`,
`menai_boolean_false`) takes `MenaiVMState *vs` as its first parameter.

`MenaiCodeObject` retain/release does not take `MenaiVMState *` — C code objects
are ephemeral (built and destroyed within a single `execute()` call) and never
shared across VM instances or threads.

## Alternatives considered

### Global mutable state with locking

This would avoid the need to thread `MenaiVMState *` through every function, but
would introduce lock overhead, deadlock risk, and would prevent true parallel
execution across VM instances.

### Thread-local storage

This would avoid explicit threading of the state pointer but would tie the VM to
thread-based isolation, preventing other concurrency models and complicating
testing.

## Consequences

### Positive

- VM instances are fully isolated and can run in parallel without interference.
- The per-instance state model supports testing and concurrent execution cleanly.

### Negative

- Every C function that touches mutable state must take `MenaiVMState *` as its
  first parameter. This is a pervasive convention that must be followed
  consistently.
- `menai_value_retain` is the sole exception — it only increments a refcount and
  is safe without the state pointer.
- `MenaiCodeObject` retain/release is exempt because code objects are ephemeral
  and never shared across VM instances.
- The bridge layer is exempt because it only holds read-only references fetched
  at module init.
- This invariant spans the entire C VM, making it easy to violate accidentally
  when adding new functions.
