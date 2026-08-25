# ADR-0014: Pool allocator in the C VM

Date: 2026-08-24  
Status: Accepted

## Context

The C VM allocates and frees large numbers of small value objects (integers,
booleans, strings, lists, dicts, etc.) during execution. The original
implementation used malloc/free for every allocation and deallocation.

This was a performance concern. malloc/free has per-call overhead, and freshly
allocated memory may not be in the CPU cache. In a VM that creates and discards
many short-lived values during a single execution, the allocation cost and cache
behaviour can dominate runtime.

## Decision

The C VM uses a pool allocator with per-instance free-lists. The free-lists are
owned by `MenaiVMState` (see ADR-0006), so each VM instance has its own pool.

When a value is freed, its memory is returned to the pool's free-list for its
size bucket rather than being returned to the system allocator. When a new value
of the same size is needed, the pool satisfies the allocation from the free-list
if possible, falling back to malloc only when the free-list is empty.

## Alternatives considered

### malloc/free (the original implementation)

This was the first approach. It is simple and requires no custom allocator
infrastructure. But every allocation and deallocation is a system call with
overhead, and freshly allocated memory may be cold in the cache. For a VM with
high allocation rates, this is a significant performance cost.

### slab allocator

A slab allocator pre-allocates large slabs of objects of the same type, similar
to the pool allocator but with type-specific slabs. This can be more efficient
for fixed-size objects but adds complexity in managing separate slabs per type.
The pool allocator's size-bucketed free-lists achieve a similar benefit — reusing
memory for objects of the same size — without type-specific slab management.

## Consequences

### Positive

- Allocations from the free-list are faster than malloc — just a pointer
  pop from the free-list head, no system call.
- Reused memory is likely to still be hot in the CPU cache, improving access
  latency for newly allocated values.
- The pool is per-instance (part of `MenaiVMState`), so there is no contention
  between VM instances.

### Negative

- The pool allocator adds complexity to the C VM: free-list management, size
  bucketing, and the convention that every allocation function takes
  `MenaiVMState *` as its first parameter.
- Memory freed to the pool is not returned to the system until the VM instance
  is destroyed. Long-running VM instances may hold freed memory in their pools
  rather than releasing it.
- The pool must be carefully sized and bucketed to avoid wasting memory on
  oversized free-lists or missing reuse opportunities for uncommon sizes.
