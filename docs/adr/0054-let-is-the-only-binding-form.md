# ADR-0054: `let` is the only binding form

Date: 2026-10-19  
Status: Accepted

## Context

Menai previously followed Scheme in offering three local binding forms: `let`
(parallel), `let*` (sequential), and `letrec` (recursive). The three differed only
in which binding names were in scope for which binding values:

| Form | Sibling visibility | Duplicate names |
|---|---|---|
| `let` | none | rejected |
| `let*` | earlier siblings | allowed (shadowing) |
| `letrec` | all siblings | rejected |

Maintaining three forms costs more than the surface convenience is worth. The
compiler already treated `letrec` as the general form: the desugarer split each
`letrec` into strongly-connected components and hoisted every group that turned
out not to be recursive into a `let`. So `letrec` already subsumed both other
forms internally, and the parallel/sequential distinction was recomputed rather
than declared.

Two further pressures pointed the same way:

- A language used by AI agents benefits from a small core. Three binding forms
  that differ only in scoping subtlety are a source of confusion, and the
  distinction is invisible at the call site.
- `let*` existed to express sequential dependency and shadowing. Sequential
  dependency is expressible in `letrec` directly. Shadowing — the one thing
  `letrec` cannot express — was used nowhere in the codebase, the standard
  library, the benchmark suites, or the test corpus.

### The change was made in two steps

The removal happened in two stages, and the shape of the result follows from that
sequence:

1. **`let*` was removed first.** It was the form with no remaining justification:
   sequential dependency is expressible in `letrec`, and shadowing — its only
   unique capability — appeared nowhere in the repository. Every `let*` was
   rewritten as `letrec`.

2. **`let` was then removed, and `letrec` renamed to `let`.** With `let*` gone,
   `let` and `letrec` differed only in whether sibling references were permitted.
   That restriction bought nothing, since the desugarer ordered the bindings
   either way. The general form was therefore promoted to the only form, and given
   the name users expect for local bindings.

This is why the internal vocabulary distinguishes `$let` from `$letrec` rather
than having a single node: the second step collapsed two *surface* forms into one,
but the two *internal* shapes they produced — a parallel group and a recursive
group — remain genuinely different, and the compiler still needs both.

## Decision

### Surface language

`let` is the only binding form. Every binding name is in scope for every binding
value and for the body.

`let*` is removed. `letrec` is removed as a surface form. Both are gone from the
parser, the semantic analyser, the desugarer, the reserved-name set, the
formatter, and the paren diagnostics. A `(letrec ...)` form is no longer a
binding form and is rejected as an unbound head.

`let` rejects duplicate binding names. A recursive group defines each name once,
so a repeated name is ambiguous rather than a shadow; to shadow, nest a `let`.

A non-lambda binding may not refer to itself, since its value would have nothing
to read. The reference may be deferred inside a lambda.

### Internal nodes

The compiler keeps two distinct binding nodes, both `$`-prefixed so that neither
can be written by a user and mistaken for the surface form:

- **`$let`** — a binding group the desugarer has proven *acyclic*: no binding
  reads a later sibling. Lowered to `MenaiIRLet`.
- **`$letrec`** — a genuine mutually-recursive group: a single strongly-connected
  component. Lowered to `MenaiIRLetrec`.

The IR builder dispatches on these internal names only. A surface `let` is
normalised to one or the other before the IR builder sees it.

`$let` groups may still contain *sequential* references — one binding reading an
earlier sibling — because the match desugarer emits extraction temps that way.
`$let` therefore means "acyclic and ordered", not "independent".

### The core language

The desugarer still lowers the full AST to a small core before the IR builder
sees it, as [ADR-0015](0015-desugar-to-small-core.md) decided. The core now
consists of:

- Literals (numbers, strings, booleans, `#none`)
- Variables (symbols)
- `if` expressions
- `$let` and `$letrec` bindings
- `lambda` expressions
- Function calls

The only change from ADR-0015's list is the binding entry: `let` / `let*` /
`letrec` became `$let` / `$letrec`. Everything else is unchanged, and the
reasoning for desugaring to a small core — that the IR, every optimisation pass,
and both code generators should handle one small set of forms — still holds.

### `MenaiIRLet` is retained

The parallel IR node survives. It is the hoisting target for every group the
desugarer proves non-recursive, which is the common case: most `let` forms in
real code contain no recursion at all. Removing it would force every
non-recursive group through the recursive path, regressing the invariant recorded
in [ADR-0008](0008-letrec-is-genuine-mutual-recursion.md) that a `$letrec`
reaching the IR builder is always genuine mutual recursion.

### Sequential visibility within `$let`

Both the IR builder and the CFG builder bind each name into the child scope as
they go, rather than analysing all values in the enclosing scope first. This is
what lets a `$let` group contain sequential references while remaining acyclic.

The desugarer's classification guarantees no value reads a *later* sibling, so
the group cannot deadlock; the sequential binding guarantees an *earlier* sibling
is available.

### Dependency analysis must not scan namespace members

`(:: namespace member)` names a module export, not a variable. The dependency
analyser scans only the namespace position; the member position is a key into the
module's export map.

Scanning the member as a reference makes a binding whose value reads a module
member appear to depend on a same-named local binding, inventing a cycle that
does not exist. This is not hypothetical: it caused a false cycle in the calendar
benchmark, where a binding read `(:: calendar add-working-days)` while a sibling
was named `add-working-days`.

### Children of internal nodes are desugared in place

`$let` and `$letrec` are emitted by the desugarer for groups it has already
classified, so the split must not be recomputed. Their children, however, may
still be raw — the match-clause builders emit raw clause bodies and binding
values. The desugarer therefore desugars the values and body of an internal node
in place, pushing each binding name into scope before the next value.

### Every emitter and collector must agree on the `$` convention

Three passes produce or consume internal binding nodes, and all three must use the
`$` names:

- The **desugarer** emits them for hoisted groups, module wrappers, match temps,
  and comparison-chain temps.
- The **prelude injector** collects the desugared prelude's wrapper chain, which
  interleaves `$let` and `$letrec`, and splices it into every program.
- The **binding injector** wraps the user program in a `$let` after desugaring.

A pass that recognises only one of the two kinds will silently drop bindings.

## Alternatives considered

### Keep `letrec` as the surface name and drop `let`

Rejected. `let` is the name users expect for local bindings, and the recursive
semantics are an implementation detail of how bindings are ordered. Naming the
only form `letrec` would foreground a distinction the language no longer makes.

### Keep `let` and `letrec`, drop only `let*`

Rejected. Once `let*` is gone, `let` and `letrec` differ only in whether sibling
references are permitted — a restriction with no benefit, since the desugarer
orders the bindings either way. Two forms would remain purely as a declaration of
intent, at the cost of a second code path through every compiler stage.

### Allow duplicate names in `let` to preserve shadowing

Rejected. A repeated name inside one recursive group is genuinely ambiguous: both
bindings define the same variable, and there is no rule for which one a sibling
reference denotes. Shadowing is still available by nesting.

### Use a distinct internal keyword per node without the `$` prefix

Rejected. A bare internal name such as `letrec` is writable by users, so a
user-written form can reach the IR builder unnormalised and be misinterpreted as
an internal node. The `$` prefix removes that class of bug: `$`-prefixed heads
are validated against the primitive registry, so they cannot appear as user
forms.

### Remove `MenaiIRLet` and route every group through `MenaiIRLetrec`

Rejected. Every non-recursive group would then reach the IR builder as a
recursive group, breaking the ADR-0008 invariant and requiring every downstream
pass to re-derive whether a group is actually recursive.

### Emit `$letrec` for the match desugarer's extraction temps

Rejected. The extraction temps are sequential but acyclic: a nested pattern's
temp reads an enclosing element's temp, never the reverse. `$letrec` requires a
genuine cycle-free strongly-connected component and rejects a group where one
non-lambda binding reads another. Sequential visibility within `$let` is the
correct model.

## Consequences

### Positive

- One binding form to learn, document, and implement. The parser, analyser,
  desugarer, formatter, and diagnostics each lose a branch.
- Sequential bindings need no special form: a chain of dependent bindings is
  written as a single `let`, and the dependency analyser orders it.
- The internal `$let`/`$letrec` split makes the desugarer's classification
  explicit and impossible to confuse with surface syntax.
- Non-recursive groups are still hoisted to `MenaiIRLet`, so the parallel node
  remains the common case and pays no recursive-group cost.
- The ADR-0008 invariant is preserved: `$letrec` at the IR builder always means
  genuine mutual recursion.

### Negative

- Shadowing within one binding list is no longer expressible; it requires nesting
  a `let`. No code in the repository relied on it.
- `let` now rejects duplicate names where `let*` permitted them, so code written
  against the old `let*` must nest instead.
- The `$let`/`$letrec` distinction is a compiler-internal concept that every pass
  touching binding nodes must respect. Passes that inspect a binding node must
  handle both, and the desugarer must emit the correct one for each group it
  produces. Three passes already had to be updated for this.
- `$let` carries two meanings that must be kept distinct: acyclic, and
  sequentially ordered. A pass that assumes `$let` values are mutually
  independent will mis-handle the match desugarer's extraction temps.
- Existing code using `letrec` must be rewritten to `let`. This is a breaking
  change to the surface language.

## Supersedes

This ADR supersedes the *core-language contents* in
[ADR-0015](0015-desugar-to-small-core.md), which names `let` / `let*` / `letrec`
as the core binding forms. The core now contains `$let` / `$letrec` instead, and
the list is restated under "The core language" above so that the definition
exists in a current ADR.

ADR-0015's *strategy* — desugar the full AST to a small core before the IR
builder sees it, so that the IR, every optimisation pass, and both code
generators handle one small set of forms — is **not** superseded and remains in
force. Its alternatives and consequences are unaffected.

The index marks ADR-0015 as "Superseded by ADR-0054" for the convenience of a
reader looking up the core language; read it as superseding the enumeration, not
the strategy.
