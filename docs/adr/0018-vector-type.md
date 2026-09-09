# ADR-0018: Vector type

Date: 2026-09-03  
Status: Accepted

## Context

ADR-0017 chose cons cells as the internal representation for lists, giving O(1)
head/tail decomposition and O(1) prepend — ideal for the recursive traversal
patterns that dominate Menai list processing. The ADR explicitly acknowledged
the trade-off: random access (`list-ref`) is O(n), and suggested "a vector type,
if one is added in the future."

The benchmark evidence is clear. Two of the four benchmark suites — Sudoku and
Rubik's Cube — are dominated by index-heavy code:

- **Sudoku**: The board is a list of 9 rows, each a list of 9 integers. Every
  operation indexes by position. `find-empty` calls `(list-ref (list-ref board
  row) col)` per cell — O(row + col) per lookup, O(n²) per full board scan.
  `set-cell` rebuilds a row via `list-slice` + `list-append` + `list-concat`
  and then rebuilds the board the same way — O(n) to update a single cell with
  multiple list allocations.

- **Rubik's Cube**: Each face is a list of 9 stickers. `get-sticker` is
  `(list-ref face pos)`, called ~20 times per move. Each move constructs new
  faces by indexing into old ones position-by-position, then building new lists
  with `(list ...)`.

Both are doing what arrays were invented for: fixed-size, integer-indexed
access with positional updates. A vector type with O(1) random access and
cache-friendly contiguous storage eliminates this bottleneck.

## Decision

Add a `vector` type as a distinct type from `list`, backed by a contiguous
array with structural sharing for slices. Vectors provide:

- O(1) random access via `vector-ref`
- O(1) length via `vector-length`
- O(n) functional update via `vector-set` (single contiguous copy, not a
  cons-cell allocation chain)
- Slice sharing via `vector-slice` (views point into the owner's backing array,
  retaining the owner — same pattern as `MenaiBytes`)
- O(n) concatenation via `vector-concat` (single allocation, two array copies)

Vectors are immutable, like all Menai values. They are a complement to lists,
not a replacement: lists remain the default for recursive traversal, vectors
are for index-heavy code.

The surface language provides `(vector ...)` construction, `vector-ref`,
`vector-set`, `vector-slice`, `vector-concat`, `vector->list`, `list->vector`,
type predicates, equality, and higher-order operations (`map-vector`,
`filter-vector`, `fold-vector`, `find-vector`, `any-vector?`, `all-vector?`,
`sort-vector`). There is no literal syntax — vectors are created via
construction, like bytes.

Vectors are NOT pattern-matchable and NOT hashable. Pattern matching is about
recursive decomposition (head/tail), which is a list concept. Hashability may
be revisited if a use case arises.

## Alternatives considered

### Array-based lists

Replace the cons-cell representation with an array-based representation for
lists, giving O(1) random access for all list operations. This was rejected
because it would break the performance of recursive traversal — the dominant
list access pattern. Prepending would become O(n) (requiring a full array
copy), and head/tail decomposition would become O(n) or require slice sharing
with its own complexity. ADR-0017 explicitly evaluated and rejected this
approach.

### Persistent vector trie

A Clojure-style persistent vector trie (HAMT) gives O(log₃₂ n) random access,
prepend, and functional update, with structural sharing. This was rejected as
premature complexity. The implementation is significantly more complex than a
contiguous array with copy-on-write, and the O(log n) access time is worse
than O(1) for the small-to-medium vectors that dominate real usage. If the
copy-on-write O(n) update cost becomes a problem for large vectors, a trie
can be revisited.

### Not adding vectors

Leave lists as the only sequence type and accept O(n) random access. This was
rejected because index-heavy code is real and measurable — the Sudoku and
Rubik's Cube benchmarks demonstrate the cost. Telling users to rewrite
index-heavy algorithms in recursive traversal style is not practical; some
problems are inherently positional.

## Consequences

### Positive

- O(1) random access for index-heavy code (Sudoku, Rubik's Cube, matrix
  operations, lookup tables).
- O(n) functional update via a single contiguous copy — faster and more
  cache-friendly than the O(n) cons-cell allocation chain that `list-slice` +
  `list-append` + `list-concat` produces.
- Slice sharing: `vector-slice` creates a view into the owner's backing array
  with no copy, retaining the owner. This is the same proven pattern used by
  `MenaiBytes`.
- The type is distinct from lists, consistent with Menai's strict typing
  philosophy. No implicit coercion means no surprises.
- The implementation closely mirrors `MenaiBytes`, the most recently added
  type, reducing the surface area for new bugs.

### Negative

- A new type increases the language's surface area: new opcodes, new C VM
  functions, new prelude wrappers, new documentation.
- Vectors are not pattern-matchable. Users who want destructuring must convert
  to a list or use `vector-ref` explicitly.
- Vectors are not hashable. They cannot be set members or dict keys. This may
  be revisited if a use case arises.
- `vector-set` is O(n) (full copy). For update-heavy code on large vectors,
  this is more expensive than a persistent trie's O(log n) update. The
  contiguous copy is fast in practice (cache-friendly, single allocation), but
  the asymptotic cost is higher than a trie would be.
