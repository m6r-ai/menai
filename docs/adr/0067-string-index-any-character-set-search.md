# ADR-0067: Character-set search is a native VM operation

Date: 2026-11-22  
Status: Accepted

## Context

Menai's scanning code — the regular-expression matcher, the CSV reader, the HTML and
XML tokenisers — repeatedly needs the position of the next character belonging to a
set: whitespace, a digit, a name terminator, a delimiter. Every primitive the language
offers in this shape is needle-based. `string-index` finds one fixed substring,
`bytes-index` one fixed substring of octets, `bytes-index-int` one fixed byte value,
and `string->list` splits on one fixed delimiter. None answers "the next character in
this set".

A set of *k* characters cannot be assembled from single-needle searches. Either each
match pays *k* full scans, which is quadratic in the subject, or each start position
pays *k* searches for the next occurrence of each character, which is worse than
testing membership directly. Menai has no mutable state, so per-needle cursors that
advance monotonically through one operation cannot exist either.

The cost of not having the primitive is measured. A regular expression whose first
element is a character class — `\s`, `\d`, `[0-9]` — has no literal prefix for
`string-index` to jump to, so the matcher evaluates the pattern at every start
position. `\s+` over 64 KB of prose executes 6.36 M VM instructions for 13,916
matches: about 97 instructions per start position against a per-match cost that is
already linear. The per-position work is a closure call, a dispatch on the pattern
node's tag, two levels of recursion into the matcher, a `string-nth` that allocates a
one-character string, and a fold over the class's ranges — all to answer "is this
character whitespace, no".

Three unrelated callers want the same primitive, which is why it belongs to the
language rather than to the regular-expression module: a tokeniser, a CSV reader and
two markup parsers.

## Decision

`string-index-any` is an opcode-backed builtin implemented natively in the VM:

```menai
(string-index-any s ranges) → integer codepoint index of the first character inside any
                              of the ranges, or #none
```

`ranges` is a vector of integers holding flat, inclusive `(lo hi)` pairs in codepoint
space. A character belongs to the set when it lies in any pair, so a set of *k*
characters costs *k/2* pairs and one scan of the subject.

The set travels as one argument rather than as variadic arguments because an opcode
encodes a fixed number of register operands; n-ary forms exist only in the surface
language, where the compiler folds them into a chain of binary calls. Two operands —
the subject and the set — fit the existing two-source instruction encoding.

Ranges are inclusive at both ends, may be given in any order, and may overlap or be
adjacent without changing the answer.

Negation is the caller's responsibility. A caller searching for a character *outside*
a set complements the ranges when it builds them: "not a digit" is
`(vector 0 47 58 1114111)`. This keeps one opcode rather than an opcode plus a flag,
and the caller already owns the set, so it already knows whether the set is negated.

An empty set is an error rather than "never matches": a caller that builds a set
always has at least one range, so an empty vector is a bug in that caller, and
returning `#none` would hide it. The same applies to an odd element count, to a pair
whose `lo` exceeds its `hi`, and to any bound outside `0`–`1114111`.

Inside the opcode, a set that lies entirely in ASCII is reduced to a 128-bit
membership mask built once per call, turning the inner loop into a load, a shift and
a test. That is the common case — whitespace, digits, word characters, `[a-z]`,
punctuation — and one scan of a 64 KB subject costs a few instructions per character
instead of the tens the interpreter spent per position. A set that reaches beyond
ASCII compares against the ranges directly, which with one or two ranges is already
cheaper than building a wider mask.

## Alternatives considered

### A bytes version

`bytes-index-any` would be cheaper still and could use libc's `memchr` family, but a
byte offset is not a codepoint offset. On non-ASCII text the result would need a
byte-to-codepoint map, and a bytes representation cannot express a non-ASCII range
such as `[α-ω]` at all. Strings are already stored as UTF-32 in the VM, so a strided
codepoint scan is not slower than an octet scan over the same range coverage.

### A predicate callback

`(string-scan s pred)` with the set test supplied as a Menai function keeps the
opcode trivial, but it calls back into the interpreter once per character, which is
exactly the cost being removed.

### A compiled-DFA value

`(dfa-compile …)` and `(dfa-scan dfa s)` would serve a regular-expression engine for
the whole pattern rather than only its first character. That is a much larger
commitment: a new heap type with its own finalizer and hashing story, a decision
record on the DFA representation, and it moves the pattern dialect's compiler into
the VM, which the regular-expression design deliberately keeps in a library. The
first-character set captures most of the benefit for a fraction of the surface.

### Ranges as a flat bytes value

A `bytes` value of 4-byte big-endian pairs would be read without unboxing, but the
encoding is opaque on the language surface, which is worse for a documented builtin.
Unboxing a handful of integers per call is irrelevant against a scan of the subject.

### Accept the interpreted probe

The alternative is to keep the current state: correct, and 75 ms on the 64 KB case.
That leaves every class-first pattern paying per-position interpretation.

### `memchr` for `bytes-index-int` as a stopgap

It would speed up single-byte searches, which the regular-expression matcher does not
use, and would not help a set at all.

## Consequences

### Positive

- A character-set scan is a few native instructions per character. The class-first
  regular-expression cases, the CSV field scan and the markup tokenisers all lose
  their per-character interpreted loop, and the matcher gains a skip path for
  patterns that begin with a class, and for an alternation of literals, whose first
  set is the union of its branches' first characters.
- Searching for any of several delimiters becomes one scan instead of one per
  delimiter, which removes code as well as cost at the call sites.
- The primitive is available to every program as an ordinary builtin, and is defined
  once rather than re-implemented per parser.

### Negative

- The opcode validates its set on every call: a malformed set raises, so a caller
  that builds sets dynamically pays for the check per call. The check is O(k) against
  an O(n) scan, so it is negligible in the cases the primitive exists for.
- The ASCII mask is built per call rather than cached in a value the caller holds.
  That is O(1) for a small set and needs no new type, but a caller scanning many short
  subjects with one large set would build the same mask repeatedly.
- The constant folder does not fold `string-index-any`. Its set argument reaches the
  opcode as a constructed vector, and the language has no vector literal for the
  folder to recognise, so folding would require a second implementation of the range
  test in Python that must agree with the VM exactly. The opcode is not on a hot path
  for constant subjects, so the folding is declined rather than duplicated.
- Each binding must implement the opcode against this specification rather than
  inheriting it from the language.
