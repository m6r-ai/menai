# ADR-0057: Typed destructuring patterns

Date: 2026-10-22  
Status: Accepted

## Context

ADR-0044 established that a pattern's meaning is decided by its form, not by what
types happen to be in scope. It applied that principle to struct and enum patterns by
introducing the `:` head symbol, and in doing so made the rule uniform: a pattern whose
head is a reserved symbol has one meaning, and a pattern with no reserved head is a
list pattern.

That rule is now incomplete in two directions.

**Lists are the one exception.** A bare `(a b c)` is a list destructuring pattern, and
it is the only pattern form whose type is inferred from the *absence* of a marker. Every
other typed destructuring names its type: `(: Point x y)`, `(: state 'idle)`. The bare
form is inherited from Menai's Lisp ancestry rather than chosen, and Menai rejects Lisp
inheritance elsewhere — ADR-0001 has no `cond`, ADR-0003 has proper lists only, ADR-0004
has no implicit coercion. The bare list pattern is an inconsistency with the language's
own method.

**Vectors have no destructuring at all.** ADR-0018 states:

> Vectors are NOT pattern-matchable and NOT hashable. Pattern matching is about
> recursive decomposition (head/tail), which is a list concept.

The first clause of that justification does not hold. Fixed-arity list patterns `(a b c)`
already exist and are *not* head/tail decomposition — they test arity and bind
positionally. A vector has exactly that shape: an ordered sequence with a
runtime-determinable length.

The practical cost is concrete. A vector is the natural representation for a
rectangular table, and `csv-decode` already returns one — a vector of rows, each row a
vector of field strings, precisely so that rows and fields are reachable in O(1) by
position. A caller holding such a table cannot destructure a row; it must index with
`vector-nth`. The same applies to any fixed-shape result a module returns, such as a
`(low high count)` histogram bin.

## Decision

There are two kinds of structural pattern, distinguished by the operation they perform,
and the language already has a notation for each.

### Fixed-arity destructuring takes a `:` type head

`:` is the single head symbol for fixed-arity typed destructuring, covering both
user-declared nominal types and builtin structural types:

```menai
(: Point x y)        ; struct — nominal
(: state 'idle)      ; enum — nominal
(: list a b c)       ; list — builtin structural
(: vector a b c)     ; vector — builtin structural
```

The head is `:`, followed by a type name, followed by bindings. There is no syntactic
distinction between a nominal and a builtin type in a pattern, and none is needed: the
distinction that matters in a pattern is that it decomposes a typed value, not how that
type was declared. Resolution of the type name is an implementation concern of the
desugarer, not a surface-language distinction.

The type name is an ordinary symbol and is renamed by the module resolver exactly as any
other reference to a module binding, per ADR-0044.

### Prefix-plus-rest decomposition uses the existing `.` notation, bare

`(head . tail)` is retained as a pattern form and is written without a type head:

```menai
(match lst
  ((: list) (list))
  ((head . tail) (list-prepend tail head))
  (_ lst))
```

This is not a special case for lists. `.` is already the language's notation for
prefix-plus-rest, in lambda parameter lists:

```menai
(lambda (a b . rest) ...)
```

A pattern's `.` form and a lambda's `.` form express the same thing — a fixed prefix
followed by the remainder — and both are unambiguous by shape. Re-heading the pattern
form as `(: list head . tail)` would make `.` a structural operator *inside* a `:` form,
which is a new shape and the actual special case.

The distinction the two forms encode is the distinction between the two operations:

- **Fixed-arity positional binding** is generic across ordered types, so it names its
  type with `:`.
- **Prefix-plus-rest decomposition** is a distinct operation with its own notation, so it
  uses `.` and needs no type head.

### The bare fixed-arity list pattern is removed

`(a b c)` is no longer a list destructuring pattern. List destructuring is written
`(: list a b c)`.

This is a deliberate breaking change. The prelude and the standard library contain no bare
fixed-arity list destructuring patterns, so no module source changes. The test suites do
contain them, embedded as Menai source strings inside Python test files, and those must be
migrated to the `: list` form. The migration is confined to tests, which is why it is being
made now: once further standard library modules are written against the bare form, the
migration would extend to shipping library code.

The cons form `(head . tail)` is unaffected by this removal and remains valid.

### Vectors support fixed-arity destructuring

```menai
(match bin
  ((: vector lo hi count) (integer->string count))
  (_ "malformed"))
```

Nested patterns compose as they do for lists, which is the case that matters for tables:

```menai
(match m
  ((: vector (: vector a b) (: vector c d))
   (float- (float* a d) (float* b c)))
  (_ (error "expected a 2x2 matrix")))
```

Vector destructuring is **fixed-arity only**. There is no `.` form, because a vector has
no O(1) tail — a rest binding would be an O(n) slice. A caller who wants the tail writes
`(vector-slice v 1)`.

An arity mismatch falls through to the next arm rather than raising, matching the
existing behaviour of fixed-arity list patterns. `(: vector)` matches the empty vector.

### Bytes are admitted by the rule, deferred

The rule this ADR establishes is: **a type supports fixed-arity structural destructuring
if it has an ordered shape with a runtime-determinable length.** `bytes` satisfies it, and
`(: bytes a b c)` would bind three integers. It is not implemented by this ADR because no
use case has been demonstrated — `bytes-read-*` covers binary parsing — but the rule
admits it without a grammar change.

### Dicts and sets get no structural destructuring

They are unordered and have no positional shape, so positional binding is not defined for
them. A `(: dict ...)` pattern would be a key-presence query, not a destructuring, and
would be O(k) lookups disguised as a pattern. They support predicate patterns only:
`(? dict? d)`, `(? set? s)`.

### Resolution and errors

A `(: TypeName ...)` head is resolved against the lexical scope for a type binding. The
builtin type names `list` and `vector` are recognised as types before any list-pattern
fallback. A `(: TypeName ...)` whose head does not resolve to a type in scope is a
compile-time error; it is never interpreted as a list pattern. This extends ADR-0044's
rule to builtin types.

## Alternatives considered

### `#vector(...)` mirroring the literal form

Use the display form as the pattern head, so a pattern looks like the value it matches:

```menai
(#vector a b c)
```

Rejected. It rests on a premise that does not hold: pattern syntax does not mirror
display syntax, and never has. A struct displays as `(point 1 2)` but matches as
`(: point x y)`; a dict displays as `{("a" 1)}` and a set as `#{1 2 3}`, and neither has a
structural pattern at all. Choosing `#vector` would introduce a second convention for
typed destructuring alongside `:`, and would put `#` to a fourth use — it is already the
hash-literal prefix for `#none`/`#t`/`#xFF`, the set prefix `#{`, and the vector and bytes
prefix `#vector`/`#bytes`.

It would also require a lexer change. `#` is not a standalone token: `_handle_hash` reads
`#` and immediately inspects the following character to fuse a single token. Making
`#vector` usable in head position would mean either redefining the entire hash-literal
family or introducing context-sensitive lexing.

### `(:vector a b c)` with no space

Rejected. There is no semantic distinction between a builtin and a nominal type in a
pattern, so there is nothing for the spacing to encode. Whitespace is not significant
here, so the distinction is not even expressible in the token stream — `(:vector ...)`
and `(: vector ...)` lex identically.

### Keep the bare fixed-arity list pattern

Rejected. It is the one pattern form whose type is inferred from a missing marker, and it
is inherited from Lisp rather than chosen. Keeping it means the language has two
conventions for the same operation with no principled reason to prefer one.

### Remove the cons pattern as well

Rejected. The cons pattern is not fixed-arity destructuring; it is prefix-plus-rest
decomposition, which is a different operation with its own established notation. Removing
it would delete the only pattern form that expresses recursive traversal, and recursive
traversal is what ADR-0017's cons-cell representation exists to make cheap. It would also
break the symmetry with lambda parameter lists, which use the same `.` notation for the
same purpose.

### Make vectors hashable at the same time

Rejected, and recorded here because it was considered alongside. A vector's hash is O(n),
so hashing a vector key costs the same as a linear scan and buys nothing for the small
key sets that occur in practice; for large key sets the O(n) hash is worse than the
alternative of an explicit canonical key. `bytes` is already hashable and has the same
O(n) property, so this is an existing inconsistency, but adding vectors to it would
extend an inconsistency rather than resolve one. A caller who needs a composite key
converts to a string or bytes key explicitly, which is the language's stated preference
for visible conversion.

## Consequences

### Positive

- A pattern's meaning is decided by its form alone. There is one head for fixed-arity
  typed destructuring, and no pattern form infers its type from a missing marker.
- The two pattern kinds map onto two distinct operations, and the notation for each
  already exists in the language: `:` for typed fixed-arity binding, `.` for
  prefix-plus-rest, matching lambda parameter lists.
- Vectors can be destructured positionally, which is what a rectangular table and a
  fixed-shape result both need. `csv-decode`'s vector of vectors becomes directly
  matchable.
- The rule generalises: `bytes` is admitted without a grammar change, and the rule states
  why `dict` and `set` are excluded rather than leaving their exclusion unexplained.
- The change is made while the migration is small. No bare fixed-arity list pattern exists
  in the prelude or the standard library, so the migration is confined to Menai source
  strings embedded in Python test files.
- No lexer change is required. `:` and the type names are already valid symbols.

### Negative

- `(: list a b c)` is longer than `(a b c)`, and the reader must know that `list` is a
  builtin type name usable in a pattern head.
- Removing the bare fixed-arity list pattern is a breaking change to any code written
  outside this repository. Menai has no external users today, which is why the change is
  being made now; after this ADR it would require a migration.
- A reader must know that `.` in a pattern means prefix-plus-rest and that it takes no
  type head, while every other structural pattern does. The justification is that `.` is
  the language's existing notation for that operation, but it is still two conventions to
  learn.
- ADR-0018's statement that vectors are not pattern-matchable is superseded. Its
  justification for that statement does not hold, and this ADR records why.

## Relationship to other ADRs

- **Extends [ADR-0044](0044-type-pattern-heads-are-syntactically-distinct.md)** — the
  `:` head now covers builtin types as well as nominal ones.
- **Amends [ADR-0018](0018-vector-type.md)** — vectors are pattern-matchable. The
  hashability half of ADR-0018's statement stands, for the reasons given above.
- **Consistent with [ADR-0003](0003-proper-lists-only.md) and
  [ADR-0017](0017-cons-cell-internal-representation.md)** — proper lists and the
  cons-cell representation are unchanged. What changes is the surface pattern syntax,
  not the internal representation.
