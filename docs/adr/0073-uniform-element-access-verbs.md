# ADR-0073: Uniform element-access verbs across every container

Date: 2026-11-28  
Status: Accepted

## Context

[ADR-0037](0037-collection-access-verbs.md) established the element-access verbs and
carved out an exception: strings and bytes had no `-with`, "because strings and bytes
have no functional single-element update". Lists were never given one either.

[ADR-0072](0072-bytes-element-access-verbs.md) removed the carve-out for bytes, which
had grown `bytes-read-*` and `bytes-write-*` field families that contradicted the
vocabulary in three ways. It deliberately left the carve-out standing for strings and
said nothing about lists.

That leaves the verb set uneven. Reading an element by position is available on every
enumerable sequence — `list-nth`, `vector-nth`, `string-nth`, `bytes-nth` — but
replacing one is available on only two of them:

| Type | Positional read | Positional update |
|------|-----------------|-------------------|
| vector | `vector-nth` | `vector-with` |
| bytes | `bytes-nth` | `bytes-with-u8` |
| list | `list-nth` | — |
| string | `string-nth` | — |

The same asymmetry appears against the keyed types, where read and update are always
paired: `dict-get`/`dict-with`, `struct-get`/`struct-with`. A user who has learned that
`-nth` reads and `-with` replaces has to unlearn it for two of the four sequences, so
the rule has to be stated with exceptions.

The justification for the string exception does not survive inspection. A Menai
string is a sequence of Unicode codepoints stored as UTF-32:

```c
struct MenaiString {
    ssize_t length;      /* logical codepoint count */
    ssize_t cap;         /* codepoints available in inline_data */
    hash_t hash;         /* cached hash; -1 = not yet computed */
    MenaiString *owner;  /* non-NULL when this is a slice view */
    uint32_t *data;      /* codepoints */
    uint32_t inline_data[];
};
```

There is no encoding to redo and no variable-width hazard: index `i` names exactly one
`uint32_t`, so replacing it is a single slot write when the string is the writer's own
storage, and a copy plus one slot write otherwise. `string-nth` already performs the
mirror-image operation. A functional single-element update exists and is cheap.

The missing list update is a consequence of representation rather than of principle. A
list is a chain of cons cells with a cached length, so replacing element `i` cannot
overwrite in place without mutating a value other holders can see. It can still be
done functionally: copy the `i` cells before the target, put the new value in a fresh
cell, and share the original tail from `i + 1` on. That costs O(i), but cost is not a
reason for the vocabulary to differ — `list-nth` is O(i) too, and is named with the
same verb as `vector-nth`.

## Decision

Every container that reads an element by position or key also produces a new value
with one element replaced, using `-with`.

- **`string-with` replaces the codepoint at a position.** It takes
  `(string-with s i ch)` and returns a string of the same codepoint length as `s`.
  `ch` must be a one-codepoint string, because the element of a string is a character,
  and that is what `string-nth` returns. An out-of-range `i` raises, matching
  `string-nth`; a `ch` that is not one codepoint raises `NOT_SINGLE_CHAR_STRING`, the
  error `string->integer-codepoint` already uses.
- **`list-with` replaces the element at a position.** It takes
  `(list-with lst i x)`, returns a list of the same length, and raises on an
  out-of-range `i`, matching `list-nth`. The cells before `i` are copied and the tail
  from `i + 1` is shared, so the cost is O(i).
- **`bytes-with-u8` is the bytes element update**, and the width-qualified forms
  `bytes-with-{width}-{endian}` replace a field. This restates ADR-0072, which this
  ADR supersedes and whose decision remains in force unchanged.

The resulting matrix has no exceptions:

| Type | Read | Update |
|------|------|--------|
| list | `list-nth` | `list-with` |
| vector | `vector-nth` | `vector-with` |
| string | `string-nth` | `string-with` |
| bytes | `bytes-nth`, `bytes-nth-{width}-{endian}` | `bytes-with-u8`, `bytes-with-{width}-{endian}` |
| dict | `dict-get` | `dict-with` |
| struct | `struct-get` | `struct-with` |

`set` remains the one type with `-with` and no read, because a set has no element
address: `set-with` adds a member, which is the only update a set has.

There is still no `string-without` or `bytes-without`. `-without` removes an element,
and removing one element from a sequence of scalars shortens it, which is a splice
rather than an element update.

### `string-with` updates in place under ADR-0063's conditions

`string-with` takes the same fast path as `string-concat`, and for the same reason: a
string that nothing else can observe is not a value, it is the writer's own storage.
The conditions are [ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md)'s,
in the form [ADR-0065](0065-string-append-in-place.md) already applies to append:

- **`dest == src0`.** The result goes back into the register the string came from, so
  the write consumes the single reference. A count of one is not sufficient on its
  own: in `(let ((a (string-with s 0 "x"))) (list s a))`, `s` has one reference held by
  a register that is still live, and writing through it would change the value the
  program goes on to read. Only the slot allocator knows when a reference is consumed,
  and `dest == src0` is the runtime-visible form of that.
- **`s->owner == NULL`.** A view borrows its codepoints from its owner, so that storage
  is not this value's to write.
- **`ob_refcnt == 1`.** Every holder retains the value — registers, the constant pool,
  closure captures and container elements alike — so a count of one means no other
  holder.

Two conditions that `vector-with` and `string-concat` need are absent here, and the
omissions are load-bearing rather than oversights. `string-with` does not change the
length, so it never reallocates and `cap` is irrelevant; the in-place path is a single
indexed store. And the replacement codepoint is copied **by value** out of `ch`, not
retained as a reference, so a `ch` that aliased the receiver would be harmless — unlike
`vector-with`, which must exclude `val == v` because retaining a reference to the
container being written would build an uncollectable cycle.

The cached hash must be cleared, exactly as `menai_string_append_slot` does: a string
is hashable, and a hash is observed by a dict or set without retaining the value, so a
stale one would outlive the mutation.

`list-with` cannot use this path at any cost. The cells after `i` are shared with the
source list, so the result's spine is never uniquely owned and a write would be
observable through the original. It copies the prefix and shares the tail.

### The operation is exactly what `-nth` returns

The element an update replaces is the value the corresponding read produces: an
arbitrary value for a list or vector, a character for a string, a byte for bytes, a
field for the width-qualified bytes forms. That is why `string-with` takes a
one-codepoint string rather than an arbitrary one, and why `bytes-with-u8` takes an
integer 0–255 rather than bytes.

## Alternatives considered

### Leave strings and lists out and document the exceptions

The smallest change, and the position ADR-0037 held. Rejected because the exceptions
are not principled: the string one rests on a factual claim about the representation
that is false, and the list one rests on a cost difference that the read verb already
ignores. A vocabulary rule that needs two exceptions and a paragraph of justification
is worse than one new verb.

### `string-with` taking an arbitrary replacement string

This would read as "replace the character at `i` with this substring", a splice that
may change the length of the result. Rejected because it is a different operation from
the one the verb names: `vector-with` and `dict-with` replace exactly one element and
preserve the container's length. It would also overlap confusingly with
`string-replace`, which replaces every occurrence of a substring.

### `string-with` always copying

The straightforward implementation: allocate a same-length string, copy, overwrite one
slot. Rejected because the copy is avoidable in exactly the case that matters — a loop
building a string element by element — and the machinery to avoid it already exists and
is already trusted for append. Copying unconditionally would make `string-with` an
asymptotically worse operation than the `string-concat` call it replaces for no
benefit.

### An in-place path for `list-with`

Rejected as impossible rather than merely expensive: the tail from `i + 1` is shared
with the source, so the result's cells are not uniquely owned and ADR-0063's conditions
never hold.

### `list-set` or `vector-set` spelling for the new operation

`-set` is the mutation verb ADR-0037 removed. Reintroducing it for the two types that
were missing the operation would put the old connotation back on exactly the new names.

## Consequences

### Positive

- One rule with no exceptions: `-nth` reads an element by position, `-with` replaces
  one. It holds for every container that has either operation.
- ADR-0037's carve-out, and the false claim about strings that supported it, are both
  gone; the verbs can be justified once instead of per type.
- `string-with` and `list-with` are predictable from `vector-with` without consulting
  documentation.
- A loop that rewrites a string element by element is linear, not quadratic, because
  the accumulator is uniquely owned and updated in place.

### Negative

- `list-with` is O(i) in allocation and copying, where `vector-with` is O(1) for a
  uniquely-owned vector. A loop that replaces into a list element by element is
  quadratic, and the cost is not visible in the name. This is the same asymmetry
  `list-nth` already has.
- `string-with` is O(n) when the string is shared or reached through a view, so the
  linear behaviour depends on the accumulator being uniquely owned. A rewrite of a
  string held in a dict, or of a view, copies.
- `-with` now spans three shapes: a positional replacement of an arbitrary value
  (`vector-with`, `list-with`), a positional replacement of a scalar (`string-with`,
  `bytes-with-u8`), and a keyed or membership update (`dict-with`, `struct-with`,
  `set-with`). The type prefix disambiguates, but the verb covers more ground than it
  did.
- Two more opcodes and two more prelude bindings to maintain.
