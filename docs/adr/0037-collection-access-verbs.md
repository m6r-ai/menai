# ADR-0037: Element access verbs — `-nth`, `-get`, `-with`, and `-without`

Date: 2026-09-30  
Status: Accepted

## Context

Menai's indexed and keyed types expose an operation that reads an element. Some of
them additionally expose an operation that produces a new value with one element
replaced. The names had drifted into an inconsistent set:

- Reads used two different verbs. Positional reads used `-ref` (`string-ref`,
  `list-ref`, `bytes-ref`, `vector-ref`) while keyed reads used `-get`
  (`dict-get`, `struct-get`).
- Writes used `-set` (`dict-set`, `struct-set`, `vector-set`) — but `-set` is the
  verb for *mutation* in virtually every language a user has seen before (Python,
  JavaScript, Java, Ruby), and `set!` is the one mutation form in Clojure. Menai is
  pure: `dict-set`, `struct-set`, and `vector-set` do not mutate, they return a new
  collection. The verb actively misdescribed the operation and invited reasoning
  about aliasing that does not hold.
- Adding and removing an element used `-add` and `-remove` (`set-add`, `set-remove`,
  `dict-remove`, `list-remove`). These are the canonical mutation verbs in the same
  languages (`set.add`, `set.remove`, `list.remove` all mutate), so they misdescribed
  purity in exactly the same way as `-set`.

Reads are not one operation. Positional access retrieves the element at position
`n` of an *enumerable* sequence, while keyed access retrieves the value associated
with a key in a *non-enumerable* mapping. A list, vector, string, or bytes value has
a first and a last element and can be walked by position; a dict or struct has no
first key and cannot be indexed by position. These are different operations and
deserve different verbs.

The positional family already existed. Lists expose `list-first` and `list-last`,
naming the two endpoint positions. `nth` is the general case that sits between them
(position 0, position `n`, position `n-1`), so a positional read verb completes a
set the language already had rather than introducing a new concept.

The two read axes are also not the same shape as the write axis. `string` and
`bytes` are sequences of scalars — a character or a byte, not a value drawn from the
language's value universe — whereas `list`, `vector`, `dict`, and `struct` are
containers of arbitrary values. Positional read spans both kinds; only the
containers support a functional single-element *update* in the surface language, so
the write verbs apply to a narrower set.

## Decision

Element access uses one of three verbs:

- **`-nth`** reads the element at a position in an enumerable sequence:
  `string-nth`, `list-nth`, `bytes-nth`, `vector-nth`.
- **`-get`** reads the value associated with a key in a non-enumerable mapping:
  `dict-get`, `struct-get`.
- **`-with`** produces a new container with one element added or replaced, and
  **`-without`** produces a new container with one element removed. Used only where a
  functional single-element update exists: `vector-with`, `dict-with`, `struct-with`,
  `set-with`, `set-without`, `dict-without`, `list-without`. There is no
  `string-with` or `bytes-with`, because strings and bytes have no functional
  single-element update.

`-ref`, `-set`, `-add`, and `-remove` are removed from the element-access
vocabulary. The operation names, the `$`-prefixed primitive forms, the opcode names,
and the compiler-internal identifiers are all renamed to match, so there is a single
name for each operation at every layer.

Set algebra (`set-union`, `set-intersection`, `set-difference`) and positional list
construction (`list-prepend`, `list-append`) keep their names: they are nouns or
positional descriptions, not mutation verbs.

The internal indexed struct operations are reworked from `STRUCT_INDEXED_GET` /
`STRUCT_INDEXED_SET` to `STRUCT_GET_INDEXED` / `STRUCT_WITH_INDEXED`, so the
operation verb precedes the qualifier consistently.

## Alternatives considered

### One `-get` verb for both positional and keyed reads

A single read verb covering every type is the smallest vocabulary. Rejected because
positional and keyed access are different operations: positional access indexes an
enumerable sequence, keyed access looks up a non-enumerable mapping. Collapsing them
hides the distinction and leaves `-get` meaning two things. `-nth` and `-get` name
the two operations precisely.

### Bare `nth` instead of `type-nth`

Clojure exposes `nth` without a type prefix because it dispatches on the runtime
value. Menai is statically typed and has no overloading, so a bare `nth` cannot be
resolved to the right operation; the type prefix is required.

### Keep `-set`, document that it is a functional update

Rejected because the problem is the connotation, not the documentation. A user who
reads `vector-set` and assumes mutation will write incorrect code regardless of what
the manual says. The verb should describe what the operation does.

### `assoc` instead of `-with`

Clojure uses `assoc` for exactly this operation (associate a key or position with a
value, returning a new collection) and reserves `set!` for mutation. `assoc` is
accurate and has precedent, but it is a Clojure-ism that is opaque to a reader who
has not seen it. `-with` reads as a description of the result — "this collection,
with this position replaced" — which is self-explanatory.

### `update` instead of `-with`

`update` is accurate but collides with the established idiom (Clojure, Haskell,
Elm) in which `update` takes a *function* applied to the current value, not a
replacement value. Using it for value replacement would set a false expectation.

### `-include`/`-exclude` instead of `-with`/`-without`

`include` and `exclude` are descriptive and non-mutating, so they would have been
acceptable. Rejected because they introduce a third verb family for no gain: `-with`
already names the update operation, and `-with`/`-without` is a natural antonym pair
that reuses it.

### Keep `-add`/`-remove`

Rejected for the same reason as `-set`: `add` and `remove` are the canonical mutation
verbs, so they imply an in-place change that Menai does not perform.

## Consequences

### Positive

- One verb per operation: `-nth` to read by position, `-get` to read by key, and
  `-with`/`-without` to add or remove. The rule is "`-nth` by position, `-get` by
  key, `-with`/`-without` to update" with no exceptions.
- `-nth` completes the positional accessor set that `list-first` and `list-last`
  already established, rather than introducing a new concept.
- The write verb no longer implies mutation, so it no longer contradicts the
  language's purity.
- The operation name is identical at every layer — surface, `$`-primitive, opcode,
  and compiler-internal identifier — so there is nothing to translate when reading
  the pipeline.

### Negative

- `-ref` is the idiomatic Lisp verb and its removal makes Menai look slightly less
  like Scheme. This is consistent with Menai's stated position that it is
  Lisp-inspired but not a Lisp dialect.
- `-nth` is a third read verb, so the read vocabulary is larger than a single `-get`
  would have been. The extra verb buys precision: it distinguishes positional from
  keyed access.
- `-with` covers two shapes: a positional or keyed replacement that takes a position
  and a value (`vector-with v i val`), and a membership update that takes just an
  element (`set-with s x`). The type prefix disambiguates.
- The rename touches every module, benchmark suite, test, and documentation page
  that referenced the old names.
