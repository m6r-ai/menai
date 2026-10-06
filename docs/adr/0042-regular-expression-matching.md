# ADR-0042: Regular-expression matching — the `regexp` capability module

Date: 2026-10-06  
Status: Accepted

## Context

Menai has no way to search a string for a pattern. `string-index` finds a literal
substring and `string-replace` replaces a literal substring, but anything involving
character classes, repetition, alternation, or anchors has to be hand-written, and
an agent handed a log line, an ISO date, or a UUID has no way to extract a field
from it.

This is a common agent operation, and it is one the language should provide. The
question is what shape it takes: a prelude function, a standard library module, or
an opcode-backed primitive.

A pattern matcher was prototyped in pure Menai and benchmarked before this decision
was taken. The measurements are summarised below; the full investigation is not
reproduced here.

### Why not an opcode

ADR-0024 and ADR-0025 set the criterion for opcode-hood: a performance-critical,
fixed-spec, integer-heavy kernel whose behaviour is pinned by a published standard
rather than by anything specific to Menai.

A regexp engine fails the second half of that test. The pattern *dialect* is a
language design decision — which constructs exist, what they mean, what is excluded
— and it is specific to Menai. That decision does not belong in C, where each binding
would have to reimplement it against a specification that exists only in this
document.

The prototype also showed that the VM is not the bottleneck. The `match` on the
instruction opcode compiles to a dense jump table; the inliner inlines the helper
that tests a character class; tail calls work; and the type analysis eliminates most
guards. Measured against Python's `re` — which is a C extension, not a peer — a pure
Menai matcher is roughly 25x slower on a single scan and, on a chained per-line
workload, was 328x slower before a literal-prefix prefilter and 48x slower after it.

A prefilter that checks the pattern's literal prefix at each start position, so that
positions which cannot begin a match are rejected with one string comparison instead
of a full match attempt, is worth 6.5x on the chained workload. It is about 25 lines
of Menai. That is the evidence that the residual is Menai-level call overhead rather
than something only an opcode can fix, and it is why this is a Menai-level capability
rather than an opcode.

### Why a standard library module rather than the prelude

The capability was first implemented as prelude functions. That was a mistake.

The prelude is the language's always-present vocabulary: it is spliced into every
program as a `letrec`, and every program pays for it. The cost is compile time — the
prelude's bindings are built into the IR and walked by the optimisation passes before
dead-binding elimination prunes the ones the program does not reach. A regexp engine
is roughly 430 lines: a pattern parser, a matcher, a literal-prefix prefilter, a
compiled-regexp struct type, and a family of operations. Putting that in the prelude
makes every program pay to build and walk it, whether or not the program has anything
to do with pattern matching.

The prelude is for what every program needs. A structured capability with its own
type, its own dialect, and its own operation family is something a program opts into.
That is what the standard library is for, and the cost of opting in is a single
`import`.

The counter-argument — that `string-index` and `string-replace` are prelude functions
and pattern matching is their generalisation, so splitting them apart is an
inconsistency — was considered and rejected. `string-index` is one line of vocabulary;
a regexp engine is a subsystem with a documented dialect and a repetition limit. They
are not the same kind of thing, and treating them as one conflates vocabulary with
capability.

### What kind of module

A regexp engine is not one operation. It is a pattern compiler, a matcher, and a
family of search, split, and replace operations, all sharing a compiled-regexp type
and a pattern dialect. It therefore does not fit the `<format>-<operation>` shape of
an operation module.

It is a **capability module**, the third kind of standard library module defined by
[ADR-0041](0041-standard-library-module-taxonomy.md): a module named for a capability,
exporting the family of operations the capability comprises. The module is named
`regexp` and exports the capability's members under their bare operation names.

## Decision

### The module

The capability is a standard library module, `regexp.menai`, exporting:

| Export | Signature | Returns |
|---|---|---|
| `regexp` | — | the compiled-regexp struct type |
| `compile` | `(compile pattern-string)` | a compiled regexp value |
| `search` | `(search rx s)` | the first match as `(start end)`, or `#none` |
| `search?` | `(search? rx s)` | `#t` if `s` contains a match, `#f` otherwise |
| `search-all` | `(search-all rx s)` | every match, as a list of `(start end)` |
| `split` | `(split rx s)` | `s` split on matches, as a list of strings |
| `replace` | `(replace rx s replacement)` | `s` with every match replaced |

The exports carry no `regexp-` prefix. The module name supplies the capability, so the
call site is `(:: regexp search)`, not `(:: regexp regexp-search)`. This is the
capability-module export rule from ADR-0041.

A match is reported as a half-open `(start end)` index pair, the same convention as
`string-slice`, so `(string-slice s start end)` is the matched text.

`search` finds a match anywhere in the string. Requiring the match to span the
whole string is expressed with the anchors (`^...$`) rather than by a second operation,
so there is no `matches` counterpart.

### Argument order: the regexp is first

The compiled regexp is the first argument and the string is the second.

This follows the rule that the *name* signals the argument order. An operation named
`domain-operation` takes the domain first: `search` is a regexp operation
applied to a string, so the regexp comes first, exactly as `bytes-hash-sha2-256` takes
the bytes first and `list-reverse` takes the list first.

### The compiled regexp is a struct

The compiled regexp is a struct with three fields:

```menai
(struct (source node prefix))
```

- `source` — the original pattern string, for error messages and inspection.
- `node` — the parsed instruction tree.
- `prefix` — the precomputed literal prefix, used by the fast path.

A struct is nominally typed and inspectable with `struct-get`, and it costs nothing:
a benchmark of the struct against a tagged list on the chained per-line workload
showed the difference to be inside the measurement noise.

The struct type is named `regexp`, and the operation that produces one is `compile`.
The module exports the type as `regexp` and the operation as `compile`, so an importer
binds the type with `(:: regexp regexp)` and the operation with `(:: regexp compile)`.

The struct's constructor takes all three fields, so `(regexp "a")` is an arity error
and `compile` is the only way to build one.

The type predicate is `regexp?`. It must guard with `struct?` before calling
`struct-is-instance?`, because `struct-is-instance?` raises on a non-struct first
argument rather than returning `#f`. A type predicate must be total:

```menai
(regexp? (lambda (x)
           (and (struct? x) (struct-is-instance? x regexp))))
```

### The pattern dialect

The dialect is deliberately small. A construct is included only if it is needed for
a common task and its semantics are unambiguous.

| Construct | Syntax | Meaning |
|---|---|---|
| Literal | `abc` | the characters themselves |
| Any character | `.` | any single character |
| Character class | `[abc]`, `[^abc]`, `[a-z]` | one character from a set, optionally negated |
| Shorthand class | `\d`, `\w`, `\s` | digit, word character, whitespace |
| Negated shorthand | `\D`, `\W`, `\S` | the complement of the above |
| Start anchor | `^` | start of input |
| End anchor | `$` | end of input |
| Zero or more | `a*` | greedy |
| One or more | `a+` | greedy |
| Zero or one | `a?` | greedy |
| Exact repeat | `a{n}` | exactly n |
| Open repeat | `a{n,}` | n or more, greedy |
| Bounded repeat | `a{n,m}` | between n and m, greedy |
| Grouping | `(...)` | groups for alternation |
| Alternation | `a\|b` | a or b |

`^` and `$` anchor to the start and end of the whole input, not to line boundaries,
because the input is a single string rather than a stream of lines. A caller
matching line by line passes one line at a time.

**Excluded deliberately:**

- **Backreferences** (`\1`) — require a backtracking engine with capture state, and
  are not needed for the common extraction tasks.
- **Lookaround** (`(?=...)`, `(?<=...)`) — a separate matching mode with its own
  semantics, and the largest source of subtle behaviour in mainstream engines.
- **Lazy and possessive quantifiers** (`*?`, `*+`) — greedy-only is unambiguous and
  covers the common cases. Adding a second quantifier mode doubles the semantics of
  repetition.
- **Named groups** — there is no capture API, so there is nothing to name.
- **Unicode property classes** (`\p{L}`) — a large data dependency for a rare need.
- **Anchors to line boundaries** (`\b`, `\A`, `\Z`) — `^` and `$` cover the input-level
  cases, and `\b` requires a definition of "word character" at the boundary.

A `{` that does not begin a valid quantifier is a literal character, so `a{b}` matches
the literal text `a{b}`.

### The repetition limit

A repetition count may not exceed 1000. A larger count raises an error rather than
being accepted, because `{n,m}` is implemented by expansion — the node tree grows
with the count — and an unbounded count would let a short pattern produce an
impractically large program.

The limit is a documented constant, not an implementation accident. Raising an error
is consistent with ADR-0019: out-of-range input raises rather than silently doing
something else.

### Matching is greedy and reports the leftmost match

`search` reports the leftmost match, and quantifiers are greedy: they consume
as much as possible while still allowing the rest of the pattern to match.

`search-all` scans left to right, resuming after the end of each match. A match
that consumes no characters terminates the scan, so a pattern that can match the empty
string does not loop forever.

## Alternatives considered

### A prelude function

This was the first implementation, and it was rejected on review. The prelude is
spliced into every program, so a 430-line capability in the prelude is compile-time
cost paid by every program, whether or not it uses pattern matching. The prelude is
for the language's always-present vocabulary, not for structured capabilities. See
the Context above.

### An opcode-backed primitive

Rejected: the dialect is a Menai design decision, not a fixed specification, so it
fails the ADR-0024 criterion. The prototype also showed the residual cost is
Menai-level call overhead, which a prefilter addresses without VM changes. See the
Context above.

### One module per operation (`regexp-compile`, `regexp-search`, ...)

Rejected: the parts are not independently useful. They share the compiled-regexp type
and the pattern dialect, and a user needs several of them together. Splitting them
forces the agent to find and import every part and puts the shared type in an
arbitrary one of them. The capability is the unit, so the module is the unit. See
ADR-0041.

### `string-match` rather than `regexp`

Rejected: naming the operations `string-*` would put the string first, so that
`string-match` reads like `string-index`. That is internally consistent, but it
splits the family across two prefixes and makes the whole capability harder to
find. Naming the module `regexp` keeps the family together, and the
`domain-operation` shape is the one the language already uses everywhere else.

### `string-pattern` rather than `regexp`

Rejected: `string-pattern` names the input rather than the operation, and "pattern"
is a euphemism for the thing being compiled. `regexp` is the honest and searchable
term, and as a bare noun type it is parallel to `list`, `dict`, `set`, and `vector`.

### One-shot `(search pattern-string s)`

Rejected: compiling the pattern on every call is catastrophic in a loop, which is the
dominant use. The compiled value is a first-class value that can be bound once and
reused, and the benchmark's chained workload depends on it.

### A richer dialect (backreferences, lookaround, lazy quantifiers)

Rejected for the reasons given under the dialect table. Each construct is a design
decision with real semantics, and none is needed for the common extraction tasks.
The dialect can grow later; it cannot shrink.

### Rejecting an invalid repetition count silently

Rejected: silently treating `a{5000}` as a literal, or silently clamping it, would
produce a pattern that does not do what it says. Raising is consistent with ADR-0019.

## Consequences

### Positive

- An agent can extract a field from a log line, validate an ISO date or a UUID, and
  split on runs of whitespace, without hand-writing a scanner.
- The capability is a single module, so finding it once finds all of it, and the
  exports carry no redundant prefix.
- A match composes with `string-slice`, so extracting the matched text is one step.
- The compiled regexp is a value that can be bound once and reused, which is what
  makes the chained workload fast.
- The dialect is small, documented, and honest about what it excludes, so a pattern
  that does not work is a documented limitation rather than a surprise.
- No VM changes: the matcher is Menai-level code, so every binding inherits it.
- The prelude does not grow, so a program that does not use pattern matching does not
  pay to build or walk the engine.

### Negative

- The capability must be imported. An agent that has `string-index` in hand and reaches
  for a pattern search will not find it in the prelude, and must know to import
  `regexp`. This is a discovery cost, and it is the price of keeping the prelude small.
  It is mitigated by the module appearing in the standard library listing the help text
  generates, and by the module's header comment naming the capability.
- The dialect is smaller than PCRE, so a pattern copied from elsewhere may use a
  construct that is not supported. This is mitigated by documenting the exclusions.
- A pattern that fails to compile raises; there is no way to test a pattern without
  compiling it, and no way to catch the error in the language, because Menai has no
  exception handling.
- The matcher is roughly an order of magnitude slower than a native engine on a
  single scan. The prefilter closes most of the gap on the chained per-line case,
  which is the common shape.
