# ADR-0017: Cons-cell internal representation for lists

Date: 2026-09-02  
Status: Accepted

## Context

ADR-0003 decided that Menai would have no cons cells and no improper lists, and
that the internal representation of lists would be "an implementation detail of
each binding." The ADR's reasoning conflated two separate questions:

1. **What does the surface language expose?** Should there be improper lists,
   a `cons` operation, and cons-cell-level destructuring?
2. **How are lists represented internally?** Should the runtime use cons cells
   (linked pairs) or some other structure (e.g. arrays, vectors)?

ADR-0003 answered "no" to both, but its arguments were almost entirely about
question 1 — the complexity of improper lists, the ambiguity in type checks,
equality, and printing, and the absence of cons-cell-level destructuring in the
surface language. Those arguments are sound and remain valid: Menai's surface
language should continue to expose only proper lists with no `cons` operation
and no improper lists.

The answer to question 2 was a consequence, not an independently justified
decision. ADR-0003 rejected "linked lists with explicit nil" (cons cells where
the tail is always a list or nil) on the grounds that it "still requires cons
cells as the building block" — but this is circular. It did not evaluate the
performance characteristics of cons cells as an internal representation for
proper lists.

Performance testing has now shown that cons cells are a significant win for the
dominant list access pattern in Menai programs: recursive traversal. Operations
like `map-list`, `filter-list`, `fold-list`, and user-defined recursive
functions that process lists head-and-tail are natural and efficient with cons
cells. Prepending an element is O(1) with no copying. Decomposing a list into
head and tail is O(1). These operations are the bread and butter of functional
list processing.

Cons cells do lose on random access — indexing the nth element is O(n) rather
than O(1) as it would be with an array-based representation. But random access
is rare in Menai programs. The language's functional style favours sequential
traversal over indexing, and the prelude functions that operate on lists are
all traversal-based. The trade-off strongly favours cons cells.

Crucially, this change is invisible at the language level. The surface language
continues to provide `list`, `list-prepend`, `list-append`, and pattern matching
on list shapes. There is no `cons` operation, no improper lists, and no
cons-cell type exposed to the programmer. The compiler pipeline is unmodified —
the change is entirely within the runtime representation of list values.

## Decision

Lists are represented internally as cons cells — linked pairs where the head
holds an element and the tail holds either another cons cell or nil. All lists
remain proper: the tail of every cons cell is always a list (another cons cell
or nil), never an arbitrary value. There are no improper lists.

This is an internal representation choice. The surface language is unchanged:
there is no `cons` operation, no dotted-pair data literal, and no cons-cell
type. List construction uses `list`, `list-prepend`, and `list-append`, all of
which produce proper lists. Pattern matching uses list patterns; the dotted
pattern `(head . tail)` is a pattern-matching convenience that decomposes a
proper list into its first element and the rest, not a cons-cell type
operation.

This supersedes the internal-representation aspect of ADR-0003. ADR-0003's
surface-language decision (no cons cells, no improper lists, proper lists
only) remains in force.

## Alternatives considered

### Array-based or vector-based representation

An array or vector representation gives O(1) random access and compact memory
layout. However, it makes prepending an element O(n) (requiring a copy of the
entire array) and makes head/tail decomposition O(n) or O(1)-with-slice
(depending on implementation). Since recursive traversal is the dominant
pattern and random access is rare, the array representation trades a frequent
win for an infrequent one. It was the approach implied by ADR-0003's
"implementation-defined representation," and performance testing showed it to
be inferior for Menai's usage patterns.

### Implementation-defined per-binding representation

ADR-0003 left the representation as "an implementation detail of each binding."
This allows each binding to choose optimally, but in practice it means the
reference implementation must pick a representation anyway, and downstream
bindings that follow the reference will inherit its choice. Having a single
specified representation — cons cells — gives consistency across bindings and
ensures the performance characteristics are predictable.

## Consequences

### Positive

- Recursive list traversal — the dominant access pattern — is natural and
  efficient. Head/tail decomposition is O(1).
- Prepending an element (`list-prepend`) is O(1) with no copying.
- The representation aligns with the functional programming style that Menai
  programs use: recursive functions that process lists head-and-tail map
  directly onto cons-cell traversal.
- The change is invisible at the language level. No compiler changes, no
  surface-language changes, no changes to pattern matching or type predicates.

### Negative

- Random access (`list-get`) is O(n) rather than O(1). This is a regression
  for code that indexes into lists by position. Such code is rare in Menai
  programs, and where it matters, a different data structure (e.g. a vector
  type, if one is added in the future) would be more appropriate.
- Cons cells have higher per-element memory overhead than a contiguous array
  (each element requires a pair object with two pointers). This is the
  standard trade-off for linked representations and is outweighed by the
  traversal performance gain.
