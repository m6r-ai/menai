# ADR-0052: Regular-expression captures

Date: 2026-10-15  
Status: Accepted

## Context

ADR-0042 introduced the `regexp` capability module with a deliberately small
pattern dialect and a match reported as a half-open `(start end)` index pair. It
excluded named groups on the grounds that "there is no capture API, so there is
nothing to name", and excluded backreferences because they "require a backtracking
engine with capture state".

The absence of a capture API turned out to be the wrong thing to build the
dialect around. The most common agent task is not "does this string contain a
match" but "pull field N out of this line" — an ISO date out of a log line, the
host out of a URL, the two halves of a key/value pair. `search` reports only the
whole match, so extracting a *sub*-match means re-running a narrower pattern
against the matched text, which is both slower and less precise than capturing it
during the match that already found it.

The matcher was already a backtracking engine with capture state in all but name:
`regexp-m` threads a continuation through every node, and the `star` case retries
with a different extent by re-invoking the continuation. Recording the extent of a
group as the match passes through it is a small addition to machinery that already
exists.

## Decision

### Capturing groups and two new operations

`(...)` becomes a **capturing** group. Groups are numbered 1-based by the position
of their opening parenthesis, left to right, including nested groups. The module
gains two operations:

| Export | Signature | Returns |
|---|---|---|
| `search-groups` | `(search-groups rx s)` | the first match as `(start end g1 g2 ...)`, or `#none` |
| `search-all-groups` | `(search-all-groups rx s)` | every match, as a list of `(start end g1 g2 ...)` |

Each `gi` is that group's `(start end)` pair, or `#none` when the group did not
participate in the match — an untaken alternation branch, or an unentered optional.
A pattern with no capturing groups yields a two-element `(start end)` list, the
same shape as `search`, so the two-element case is not a special case of the
operation but the natural degenerate one.

The existing `search`, `search?`, `search-all`, `split` and `replace` operations
keep their signatures and their behaviour. Groups capture for the new operations;
the old ones are unaffected.

### `replace` expands group references

`replace`'s replacement argument is no longer purely literal. It may refer to the
match and its groups:

- `$0` — the whole match
- `$1`..`$n` — the capturing groups, each substituted with the text it captured
- `$$` — a literal dollar

A `$` followed by anything else, a trailing `$`, or a group number with no
corresponding group raises. A group that did not participate substitutes the empty
string. This is the operation that makes captures useful rather than merely
available: reordering a date from `YYYY-MM-DD` to `DD/MM/YYYY` is one call.

### A group is numbered when it is parsed

`+` and `{n,m}` are implemented by expanding the quantified sub-node — `a+`
becomes `(seq a (star a))`, and `a{2,4}` becomes a chain of mandatory and optional
copies. That expansion duplicates the sub-node, which was harmless while groups
were discarded but wrong once a group carries an identity: `([a-z])+` produced two
group nodes and therefore two group numbers, and `([a-z]){2,4}` produced four.

The fix is to assign a group its number when the parser reads its opening
parenthesis, rather than in a pass over the finished tree. A quantifier then
expands a node that already carries its number, so the copies share it and the
group is numbered once. Numbering after parsing cannot do this: two copies of a
group node are structurally identical, so a later pass has no way to tell that
they are the same group.

The parse functions therefore thread the next group number alongside the next
index, returning `(list <node> <next-index> <next-group>)`. The alternative —
making `+` and `{n,m}` node kinds so the tree never contains a copy — was
implemented and rejected; see below.

## Alternatives considered

### Extending `search` to return captures when the pattern has groups

Rejected: it makes the operation's return shape depend on the pattern, which is
exactly the implicit polymorphism the language rejects everywhere else. A caller
would have to inspect the pattern to know what it was handed.

### Named groups

Rejected for this change. Numbered groups cover the extraction tasks, and naming
them raises a further design question — the syntax, and whether a name is a symbol
or a string — that is separable from whether captures exist at all. The dialect can
grow names later; it cannot shrink them.

### A dict result keyed by group number

Rejected: heavier than the flat list, and inconsistent with the `(start end)`
convention the rest of the module uses. A caller that wants a dict can build one.

### Leaving `replace` literal and adding a separate `replace-groups`

Rejected: two operations that differ only in whether `$` is special is a worse
interface than one operation with a documented replacement syntax. The cost is that
`replace`'s replacement is no longer literal, which is a real change to an existing
operation and is why it is recorded here.

### Making `+` and `{n,m}` node kinds instead of expansions

Rejected on measurement. A `plus` node matching its child in place, and a `repeat`
node matching it between lo and hi times, remove the duplication by construction
and need no numbering pass at all. But they cost performance on exactly the
workloads that matter: an every-match scan over a `+` pattern was about 13%
slower, and splitting and replacing on `\s+` about 7-12% slower, than the
expansion form. Numbering at parse time fixes the duplication bug without
touching the matcher's structure, so it is the cheaper way to get the same
correctness.

## Consequences

### Positive

- An agent can extract a field from a log line, a URL, or a key/value pair in one
  operation, which was the motivating task and was previously impossible.
- `replace` can reorder captured text, so reformatting a date or a name is one call.
- The two-element result of a group-less pattern is the same shape as `search`, so
  a caller that does not use groups is unaffected by their existence.
   - A quantified group is numbered once, so `([a-z])+` declares one group and not
     two. The tree still contains the copies the quantifier expands to, but they
     share a number, so the group count matches what the pattern wrote.
- No VM changes: the matcher is still Menai-level code, so every binding inherits
  it, and the prelude still does not grow.

### Negative

- `replace`'s replacement argument is no longer literal. A replacement string that
  contains a `$` must escape it as `$$`, and a replacement that previously passed a
  literal `$1` through now raises. This is a behaviour change to an existing
  operation.
   - The capture vector is threaded through every continuation, so the matcher carries
     a little more state per call even for patterns with no groups. Measured cost is
     a few percent on `+`-heavy every-match scans; the alternative of making
     quantifiers node kinds was several times larger and was rejected.
- Backreferences remain excluded. The engine now has capture state, so the
  ADR-0042 reason for excluding them no longer applies verbatim; they are excluded
  because they are not needed for the common extraction tasks, not because the
  engine cannot represent them. A future ADR may revisit this.
- The dialect is still smaller than PCRE, so a pattern copied from elsewhere may
  use a construct that is not supported.
