# ADR-0064: Byte-string updates are done in place under ADR-0063's conditions

Date: 2026-11-21
Status: Accepted

## Context

[ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md) lets an update opcode
write into a uniquely-owned vector, dictionary, set or struct instead of allocating a
replacement. `MenaiBytes` was left out of that decision, and the omission is load-bearing
for the worst remaining cost in the standard library.

Bytes have no functional-update opcode: a byte sequence can only be built by
`bytes-append-u8`, the `bytes-append-*-le`/`-be` family, the LEB128 appends, or
`bytes-concat`. Each of those allocates a copy of the whole value, so appending to a bytes
accumulator costs O(n) per byte and O(n²) to build an n-byte value. That is the shape the
binary encoders have where they append per element at all: `bmp-encode` appends each
pixel's channels to the row it is building, and `zip-create` concatenates each entry's
header and data. A 96×96 BMP is 27 KB built three or four bytes at a time — an order of
magnitude more copying than the encoded image's own size if every append copies, and it
grows quadratically with the image.

The same reasoning that justified ADR-0063 for the other collections applies unchanged to
bytes: when nothing can observe a value, replacing its contents is not observable, and the
VM's reference counting can tell when nothing can observe it. What bytes need beyond
ADR-0063's four conditions is **capacity**, because an append changes the length. That is
the same problem ADR-0063 solved for dictionaries and sets, and this decision reuses that
mechanism rather than inventing a second one.

## Decision

`MenaiBytes` gains a `cap` field recording how many bytes its inline array holds, which may
exceed `length` exactly as `MenaiDict`'s `cap` may exceed its length. `alloc_menai_bytes`
sets `cap = length`, so a shared value is still allocated tight and slack is only ever
requested by the in-place path.

The append and write opcodes then update in place when **all** of ADR-0063's conditions hold:

1. The destination field equals the source field, and the source operand is a register.
2. The value's reference count is exactly one.
3. The value owns its storage — it is not a slice view.
4. *No self-reference test is needed.* A byte sequence holds only octets, so it cannot
   contain itself and cannot build a cycle. This is the one place where ADR-0063's fourth
   condition has no analogue.

An append additionally uses `menai_bytes_reserve`, which returns the value unchanged when
`extra <= cap - length` and otherwise allocates a replacement with `cap = 2 * (length + extra)`
and copies once. A sequence of n appends therefore copies `n + n/2 + n/4 + ... = 2n` bytes in
total, which is linear. A write opcode needs no capacity at all: it replaces bytes within
the existing length.

Both families must **clear the cached hash**, which the other collections do not have to
consider. Bytes are hashable and cache their hash in the value, and a hash is observed
without retaining the value — a `set-member?` test or a dictionary lookup reads it and drops
the reference immediately. A uniquely-owned value can therefore carry a stale cached hash
from before it was mutated, and leaving it would make a subsequent lookup miss. Every
in-place bytes write sets `hash = -1`.

## Alternatives considered

### Pre-size a buffer and write into it, with no capacity field

Add `bytes-fill` (n zero bytes) and let `bytes-write-*` update in place, so an encoder
allocates the exact output size and fills it. This needs no `cap` and no growth, and the
write path becomes a store.

It was rejected as insufficient on its own. It requires every caller to know the output
length in advance, which `zip-create` does not (it needs a pre-pass over the entries), and
it forces every existing encoder to be restructured before it gets any faster. Appending is
the idiom the encoders already use, so making append cheap lets each of them benefit without
being restructured around a known length. The write path is taken as well, because it is the
same test and it makes a pre-sized-buffer style available to callers that prefer it.

### Shrink the allocation on every append instead of carrying slack

Rejected: it turns each append into an allocation and a copy, which is the behaviour being
removed. Carrying `cap` and growing geometrically is what makes the sequence linear, and the
dictionary has done exactly this since ADR-0063.

### Make the copy cheaper rather than removing it

The obvious alternative is to leave the opcodes alone and have the encoders build a list of
byte values and convert once with `list->bytes`. That is linear — `png-encode`'s scanline
builder converted to it, having previously concatenated a growing list per pixel — but it
costs a cons cell per byte (27 KB of image is 27,000 cons cells and 27,000 pool
allocations) where the in-place path costs none. The list form remains the fallback for a
caller that cannot use an accumulator, not a replacement for this.

### Give bytes the same treatment inside the compiler instead

A compiler pass could recognise an append chain and pre-size the buffer. It was rejected for
ADR-0063's reason: the length depends on the data in the general case, and the runtime test
is a cached load and a compare on a conditional the opcode already contains.

## Consequences

### Positive

- Building a bytes value one piece at a time is amortised O(1) per byte rather than O(n),
  for the accumulator idiom the encoders already use. Where the append happens in the loop
  that owns the accumulator — as it does in `png-encode`'s scanline builder — the value is
  built with no allocation per byte and no source change beyond keeping the write in that
  loop.
- No compiler change, no bytecode format change, no change to the slot allocator, the
  peephole or the validator. As with ADR-0063 the mechanism is a branch in the opcode.
- Values that are shared — a literal, a captured value, a slice view, a function parameter —
  keep the allocating path, so no observable behaviour changes.

### Negative

- Executed Menai instruction counts do not change, so the opcode profile cannot see this;
  wall-clock time at a large enough input is the only measurement, as for ADR-0063.
- `MenaiBytes` grows by one word, which costs memory on small values.
- Every append and write opcode carries a branch that fails whenever the value is shared,
  and a slice view fails condition 3 unconditionally, so a decode path that appends through
  a view will not benefit and cannot be made to.
- The fast path needs the accumulator to be local to the loop that appends to it, which is a
  property of the caller's shape, not of the opcode. An encoder that appends through a helper
  taking the buffer as an argument gets one copy on entry to that helper, so `bmp-encode`,
  which builds a row per call, copied the whole image once per row and this decision did
  nothing for it. Flattening that loop was measured and was *slower*: the copy it removes is
  a memcpy of a few kilobytes, which costs less than the extra loop state needed to keep the
  row in the caller. The gain is asymptotic, so it appears on inputs far larger than the
  benchmark cases.
- The cached hash is now a correctness obligation on every future in-place bytes opcode. It
  is not visible in the type and a missing clear is a silent lookup failure, so it is stated
  at each site and covered by a test.

### Neutral

- As with ADR-0063 the fallback is always correct, so a wrong prediction costs time rather
  than correctness, and the test must never be an `assert`.
- The geometric growth deliberately means the allocating path can over-allocate: a
  uniquely-owned value that is full is copied with headroom. It remains always correct.

## Related

- [ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md) establishes the four
  conditions and the per-type capacity mechanism this decision extends.
- [ADR-0019](0019-slice-out-of-bounds-raises-error.md) and the `MenaiBytes` slice-view
  representation are why condition 3 is load-bearing rather than decorative.
