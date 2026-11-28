# ADR-0072: Bytes element access uses `-nth` and `-with`

Date: 2026-11-28  
Status: Accepted

## Context

[ADR-0037](0037-collection-access-verbs.md) settled the element-access vocabulary:
`-nth` reads an element by position, `-get` reads a value by key, and
`-with`/`-without` produce a new value with an element added, replaced or removed.
It removed `-set`, `-add` and `-remove` because those verbs connote mutation, and
Menai is pure. It also stated a carve-out: there is no `string-with` or `bytes-with`,
"because strings and bytes have no functional single-element update".

That carve-out no longer describes the language. The `bytes` type subsequently grew
two families of fixed-width field operations:

- `bytes-read-*` decodes a 2-, 3-, 4- or 8-byte field at an offset and returns an
  integer or float (`bytes-read-u32-le`, `bytes-read-f64-be`, ...).
- `bytes-write-*` returns new bytes with a fixed-width field replaced at an offset
  (`bytes-write-u32-le` takes bytes, an offset and a value, and returns bytes).

These families were never brought into the ADR-0037 vocabulary, and the result
contradicts it in three distinct ways.

**The write verb names mutation.** `bytes-write-u32-le` returns a new value and
leaves its argument untouched, so `-write` implies exactly the in-place change that
`-set` was removed for. It is the same defect under a different word, and a reader
who assumes mutation from `bytes-write-*` will write incorrect code for the same
reason they would have from `vector-set`.

**One operation has two names.** `bytes-read-u8` and `bytes-nth` are the same
operation, with the same arity, the same result and the same error behaviour: both
return the unsigned byte at an offset and both raise if the offset is out of range.
ADR-0037 requires a single name for each operation at every layer, so one of them
must go.

**The same operation has different verbs across types.** Replacing one element at a
position is `vector-with` for a vector and `bytes-write-u8` for bytes: identical
arity, identical semantics, different verb. Every other type — `dict`, `struct`,
`set` — agrees with `vector-with`.

A further signal is that `-read` and `-write` appear in no other builtin name in the
language. The bytes type is the sole home of a verb family the rest of the
vocabulary does not use, which is what a term that was never reconciled looks like.

The carve-out's premise is also simply false for bytes: `bytes-write-u8` *is* a
functional single-element update. It was never true for strings in the same sense
either — a string has no positional single-character replacement, and
`string-replace` replaces every occurrence of a substring rather than the character
at an index. So this ADR withdraws the claim for bytes and leaves it standing for
strings.

## Decision

Bytes element access uses the ADR-0037 verbs.

- **`-nth` reads a field at a position.** The single element is `bytes-nth`. A
  fixed-width field is `bytes-nth-{width}-{endian}`, for widths `u8`/`i8`,
  `u16`/`i16`/`u24`/`i24`/`u32`/`i32`/`u64`/`i64`, and `f32`/`f64`. A variable-width
  field is `bytes-nth-uleb128` / `bytes-nth-sleb128`.
- **`-with` replaces a field at a position.** `bytes-with-{width}-{endian}`, with the
  same width set as `-nth`.
- **`bytes-append-*` keeps its name.** Appending is positional construction, not a
  mutation verb, which ADR-0037 already allows for `list-prepend` and `list-append`.
- **`bytes-read-*` and `bytes-write-*` are removed.**
- **`bytes-read-u8` is deleted rather than renamed.** `bytes-nth` already performs it
  and two names for one operation is the defect being fixed.
- **No opcode renumbering.** The retired `BYTES_READ_U8` leaves its number unused.

### Element read versus field read

`bytes-nth` reads the *element*: one byte, the unit the sequence is composed of, and
the only thing a positional index into bytes can name without a width. The
width-qualified forms read a *field*: a multi-byte value whose bytes are an encoding
of the value rather than the value itself. A field occupies several bytes but is one
value, and it is that granularity which makes `-with` the correct verb — the same
reasoning that makes `-nth` the correct verb for reading it.

This is why `bytes-nth` has no `-u8` twin. The widths that need a qualifier are the
ones where the byte count is a choice.

`bytes-nth-uleb128` returns `(value next-offset)` rather than a bare value. A
variable-width field does not tell the caller where the next field begins, so the
cursor is part of the result. That is a consequence of the width being variable, not
a different operation, so it does not justify a different verb.

### Renamed operations

| Before | After |
|--------|-------|
| `bytes-read-u8` | *removed* — use `bytes-nth` |
| `bytes-read-{i8,u16,i16,u24,i24,u32,i32,u64,i64}-{le,be}` | `bytes-nth-...` |
| `bytes-read-{f32,f64}-{le,be}` | `bytes-nth-{f32,f64}-{le,be}` |
| `bytes-read-{uleb128,sleb128}` | `bytes-nth-{uleb128,sleb128}` |
| `bytes-write-{u8,i8,...}-{le,be}` | `bytes-with-...` |
| `bytes-write-{f32,f64}-{le,be}` | `bytes-with-{f32,f64}-{le,be}` |
| `bytes-append-*` | unchanged |

The operation name is identical at every layer — builtin registry, type signatures,
`$`-prefixed primitive, opcode, and C VM identifier — so there is nothing to
translate when reading the pipeline.

## Alternatives considered

### Rename only the write family

`-read` is far less objectionable than `-write`: a read does not imply a side effect,
and it is the established word for pulling a value out of an encoded representation.
Renaming just `bytes-write-*` to `bytes-with-*` would fix the mutation connotation at
the lowest cost, since the write family is exercised almost entirely by tests rather
than by the standard library's own encoders.

Rejected because it fixes one of the three contradictions and leaves two. Bytes would
still be the only type with a read verb, and `bytes-read-u8` would still duplicate
`bytes-nth`. It would also double the churn: the read family is the half that is
load-bearing in real code, so whoever renamed it later would pay the cost twice.

### `bytes-with-u8` as the single fixed-width replacement, no `-nth` change

Tempting because a bytes replacement at single-byte granularity is exactly
`vector-with`. Rejected for the same reason: it addresses the verb but not the
duplication or the read vocabulary.

### Rename `bytes-read-u8` to `bytes-nth-u8`

This keeps a uniform `nth-{width}-{endian}` shape, so a code generator emitting
bytes field accesses never needs a special case for the single-byte read. Rejected
because it is a second name for an operation the language already names. A name whose
only justification is symmetry with names that do not need it is worse than an
acknowledged asymmetry.

### Keep `-read` for the LEB128 pair only

The LEB128 operations advance a cursor, so they resemble a stream read more than an
indexed read. Rejected because `-nth` reads a field at a position, and a
variable-width field is still a field at a position; the returned next-offset follows
from the width rather than from a different kind of operation. Keeping `read` for two
operations would preserve exactly the inconsistency this ADR exists to remove.

### `-put`, `-store` or `-replace` instead of `-with`

`put` and `store` carry the same mutation connotation as `write` and would fail for
the same reason `-set` did. `replace` is already used by `string-replace`, whose
semantics are replace-every-occurrence, so reusing it for a positional replacement
would set a false expectation. `-with` is the established verb for a functional
single-element replacement, and using it keeps the vocabulary at one name per
operation.

### `bytes-decode-*` instead of `bytes-nth-*`

`decode` is accurate for the read direction and is honest about going from an
encoding to a value. Rejected because it would introduce a verb used by no other
type, and because it does not cover the opposite direction: an encoder would need a
matching `bytes-encode-*`, which is precisely what `bytes-append-*` already is.
`-nth` is ADR-0037's positional-read verb and spans every enumerable sequence.

## Consequences

### Positive

- bytes uses the same vocabulary as every other type: `-nth` to read by position,
  `-with` to replace. ADR-0037's rule of one verb per operation now holds with no
  carve-out for bytes.
- The mutation connotation is gone. `bytes-with-u32-le` reads as "these bytes, with
  this field replaced", which is what the operation does.
- `bytes-nth` is the only name for the single-byte read.
- A reader who has met `vector-with` can predict `bytes-with` without consulting
  documentation, which is the whole point of a consistent verb set.

### Negative

- A breaking rename. Every standard-library codec decoder, several test suites, and
  any external program that used `bytes-read-*` or `bytes-write-*` must be updated.
  There is no deprecation path: the language is pre-1.0 and carries no compatibility
  shims.
- `bytes-nth` has no `-u8` twin while the other widths do. The element/field
  distinction justifies it, but it is a genuine asymmetry that a reader may notice
  before they find the reasoning.
- `-with` gains a second shape. `vector-with` replaces one element; `bytes-with-u32-le`
  replaces a field of several bytes. The type prefix disambiguates, as it already
  does for `set-with`'s membership shape, but the verb is no longer uniform in
  granularity.
- `bytes-nth-uleb128` returns two values where every other `-nth` returns one.
- The retired `BYTES_READ_U8` opcode leaves a gap in the opcode numbering. Opcode
  numbers are not renumbered, so anything reading a disassembly must tolerate the
  hole.
