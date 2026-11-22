# ADR-0065: A string append is done in place under ADR-0063's conditions

Date: 2026-11-22
Status: Accepted

## Context

[ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md) lets an update opcode
write into a uniquely-owned vector, dictionary, set or struct instead of allocating a
replacement, and [ADR-0064](0064-bytes-update-in-place.md) extended it to byte sequences.
`MenaiString` was left out of both, and it carries the same cost.

`string-concat` is the only way to build a string from parts, and every call allocates a
copy of the whole result. A loop that appends one piece per iteration therefore copies the
accumulator at every step: O(n²) copy time to build an n-codepoint value. That shape is
common — composing a line from fields, folding a formatted document, collecting the pieces
of a number — and where it appears it dominates the cost of the work being done.

## Decision

`MenaiString` gains a `cap` field recording how many codepoints its inline array holds,
which may exceed `length` exactly as `MenaiDict`'s `cap` may exceed its length.
`alloc_menai_string` sets `cap = length`, so a shared value is still allocated tight and
slack is only ever requested by the in-place path.

`OP_STRING_CONCAT` then appends in place when **all** of the following hold:

1. The destination register is the source register, so the write consumes the single
   reference (ADR-0063's condition 1, and the reason it is not merely a reference count).
2. The receiver's reference count is exactly one.
3. The receiver owns its storage — it is not a slice view.
4. **The other operand is not the receiver.** This replaces ADR-0063's self-reference
   test with a different one, and the difference matters: a string holds only codepoints,
   so it cannot contain itself and cannot build a cycle. What it can do is name itself as
   the source of an append, and the source codepoints are read after the receiver has been
   replaced, so a source that pointed into the retired allocation would dangle.

Conditions 3 and 4 overlap in one direction — a view of the receiver retains it, so the
count in condition 2 is already above one — but the receiver itself is a legal source and
only condition 4 rejects it.

An append uses `menai_string_reserve`, which returns the value unchanged when the spare
room can hold the addition and otherwise allocates a replacement with
`cap = 2 * (length + n)` and copies once. A sequence of n appends therefore copies
`n + n/2 + n/4 + ... = 2n` codepoints in total, which is linear.

The append must **clear the cached hash**, for ADR-0064's reason: a string caches its hash
in the value, and a hash is observed without retaining the value — a `set-member?` test or
a dictionary lookup reads it and drops the reference immediately. A uniquely-owned value
can therefore carry a stale cached hash from before it was mutated, and leaving it would
make a subsequent lookup miss.

## Alternatives considered

### Make the standard library build strings from a list and convert once

The fallback the standard library uses where the accumulator idiom cannot be reached:
build a list of pieces and join it with `list->string`, which is linear. It costs a cons
cell and a pool allocation per piece, where the in-place path costs neither, and it forces
every caller to restructure before it benefits. It remains the right shape for a caller
that genuinely has the pieces as a list.

### Grow by copying into the next power of two

Rejected as an inferior version of the same mechanism: reserving `2 * (length + n)`
already amortises to 2n codepoints copied, and a doubling rule would need a second policy
for the small values that dominate.

### Give the compiler a pre-sizing pass

Rejected for ADR-0063's reason: the final length depends on the data in the general case,
and the runtime test is a cached load and a compare on a conditional the opcode already
contains.

## Consequences

### Positive

- Building a string one piece at a time is amortised O(1) per codepoint rather than O(n),
  for the accumulator idiom, with no source change beyond keeping the write inside the
  loop that owns the accumulator.
- No compiler change, no bytecode format change, and no change to the slot allocator, the
  peephole or the validator: the mechanism is a branch in the opcode.
- Values that are shared — a literal, a captured value, a slice view, a function parameter
  — keep the allocating path, so no observable behaviour changes.

### Negative

- Executed instruction counts do not change, so the opcode profile cannot see this; it is
  a wall-clock, asymptotic gain, and only a large enough input shows it.
- `MenaiString` grows by two words (the capacity and the storage pointer that slice views
  need, ADR-0066), which costs memory on small values.
- Every append carries a branch that fails whenever the value is shared. A decode path that
  appends through a *slice view* fails condition 3 every time, so it cannot benefit and
  cannot be made to without dropping the view.
- The cached hash becomes a correctness obligation on any future in-place string write. It
  is not visible in the type and a missing clear is a silent lookup failure rather than a
  wrong value.
- The fast path needs the accumulator to be local to the loop that appends to it. A helper
  that survives inlining takes the string as an argument, receives it through a `MOVE` that
  retains, and so copies on every write — with no diagnostic when a refactor moves the
  write across that boundary.

### Neutral

- The fallback is always correct, so a wrong prediction costs time rather than
  correctness, and the test must never be an `assert`.
- A uniquely-owned value that is full is copied with headroom, so the allocating path can
  over-allocate. It remains always correct.

## Related

- [ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md) establishes the four
  conditions and the per-type capacity mechanism this decision extends.
- [ADR-0064](0064-bytes-update-in-place.md) is the same decision for byte sequences,
  including the obligation to clear a cached hash.
- [ADR-0066](0066-string-slices-and-trims-are-views.md) is why condition 3 exists for
  strings where ADR-0064 has no analogue for bytes.
