# ADR-0066: String slices and trims are views into their source

Date: 2026-11-22
Status: Accepted

## Context

A `MenaiString` is an immutable sequence of Unicode codepoints stored as UTF-32, and the
operations that produce a sub-range of one — `string-slice`, `string-trim`,
`string-trim-left`, `string-trim-right` — allocated a new value and copied the codepoints
into it. For a trim this copies the whole surviving text to remove whitespace from the
ends, which is the entire cost of the operation.

`MenaiBytes` and `MenaiVector` have always shared rather than copied: a slice allocates a
header that points into the owner's storage and retains the owner, and the same mechanism
is what makes the in-place update of ADR-0063 safe to reason about, because "does this
value own its storage?" is a test the representation can answer.

Strings were excluded from that only by history. The cost is real and repeated: a
tokeniser that trims each token of a document copies the whole token once for the trim and
once more for whatever it does next, and the codepoints being copied were already resident
and unchanging.

## Decision

`MenaiString` gains the same structural-sharing representation as `MenaiBytes`: an `owner`
pointer that is non-NULL only for a view, and a `data` pointer that addresses the value's
own inline array for an owner and the owner's array for a view. Everything that reads a
string reads through `data`, so no other code changes shape.

A slice over a **proper sub-range** of a value is a view: it allocates only the header,
points `data` into the root owner's codepoint array, and retains the owner. Views never
form chains — a slice of a view points at the same root owner — so repeated slicing keeps
one owner alive and adds a header each time.

A slice over the **whole** value returns the source itself, retained. A string is
immutable, so nothing distinguishes a copy from the original; this is the case every trim
of an already-trimmed value hits, and it makes that case allocation-free.

Trims are expressed as slices, so trimming never moves a codepoint: the cost is the scan
that finds the whitespace.

A view is always allocated exactly tight (`cap == length`), so it can never be the
receiver of an in-place append: growth always allocates a replacement, and the view's
borrowed storage is never written. The append's ownership test states this requirement
rather than relying on it (ADR-0065).

The cached hash stays per object, so a view caches the hash of its own codepoints. It lives
on the pool header side of the value, not in the shared array, which is what makes that
possible.

## Alternatives considered

### Keep copying, but copy with a cheaper primitive

A `memmove` of the sub-range is what the code already did. The copy is not improvable: the
win is not copying at all, and it is asymptotic in exactly the case that matters — a trim
of a long value that removes a few characters.

### Give strings a start offset and a length instead of a data pointer

A `start` field added to the existing inline representation would let a *value* describe a
sub-range of its own allocation without a second object. It cannot express a slice of a
slice without copying, it makes every access add an offset, and it has no way to keep the
source alive once the source object goes out of scope. The owner pointer is what makes the
lifetime work, and it is the representation bytes and vectors already use.

### Copy on write, or copy when the view outlives the source

Requires tracking a view count on the owner and a copy at the point the source dies, which
turns a `release` into a conditional copy of unbounded size. The values are immutable, so
there is nothing to write and nothing to copy back.

## Consequences

### Positive

- `string-slice` and the trims are O(1) in the codepoints produced — the trim keeps its
  whitespace scan, the slice keeps its bounds checks, and neither allocates proportional to
  the text.
- A tokenising or parsing idiom that slices a document into tokens now costs one header per
  token instead of one copy of the token, and a trim of an already-trimmed token costs
  nothing at all.
- The representation now agrees across the three variable-length types, so the ownership
  test that ADR-0063 and ADR-0065 depend on has the same meaning everywhere.

### Negative

- A view keeps the **entire** owner alive. A one-codepoint token sliced out of a large
  document retains the document until that token is released, so a program that extracts a
  few fields from a large input and drops the input now holds all of it. This is the
  trade-off bytes and vectors already make, and it is accepted here for the same reason:
  the common case keeps the source alive anyway.
- `MenaiString` grows by two words, which is a real cost on the small strings that dominate
  a value count.
- A slice is no longer independent storage, so any future opcode that writes into a string
  must make the same "does this value own its storage?" test that ADR-0063 requires of the
  collections. That requirement is now stated at the one such site rather than being
  implied by "strings always own their codepoints".
- Executed instruction counts do not change, so the opcode profile cannot see the gain;
  it is wall-clock time on inputs large enough for the copy to matter.

### Neutral

- Nothing observable changes: strings are compared, hashed, formatted and converted by
  value, and identity has no meaning in the language, so a program cannot tell a view from
  a copy.

## Related

- [ADR-0063](0063-in-place-update-of-uniquely-owned-collections.md) makes ownership of
  storage a condition of the in-place fast paths, which is the reason the distinction
  between an owner and a view has to be expressible in the type at all.
- [ADR-0065](0065-string-append-in-place.md) is the in-place append whose ownership test
  this representation makes necessary.
- [ADR-0019](0019-slice-out-of-bounds-raises-error.md) fixes the bounds behaviour of a
  slice, which a view does not change: the checks happen before the view exists.
