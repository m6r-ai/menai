# ADR-0043: Enum type

Date: 2026-10-06  
Status: Accepted

## Context

Menai has no way to name a small, closed set of values. The idiom today is to bind
integer constants and dispatch on them, or to use strings, and both are wrong in the
same way: the set is not closed, the type is not distinct, and the operations that
should be errors are legal.

The standard library demonstrates the problem in three shapes.

**A closed option set passed as an argument.** `deflate-compress.menai` takes a block
encoding as a string. The module documents four values, and enforces them by hand:

```menai
(if (or (string=? mode "auto")
        (string=? mode "stored")
        (string=? mode "fixed")
        (string=? mode "dynamic"))
    ...
    (error (string-concat "deflate-compress: unknown mode \"" mode ...)))
```

That is a closed set of four values, enforced by four string comparisons and a
hand-written error message, at runtime, on every call. The same set is threaded
through `zlib-compress` and `gzip-compress`.

**A closed set of record tags.** `csv-decode.menai` returns a tagged list whose tag is
`"field"` or `"row"`, dispatched by string comparison.

**A closed set of chunk types.** `png-decode.menai` dispatches on `"IEND"`, `"IHDR"`,
`"IDAT"`, `"PLTE"`, and `"tRNS"`, by string comparison.

**A hand-rolled integer enum.** `regexp.menai` binds ten integer constants
(`regexp-op-empty` through `regexp-op-star`) to name the variants of its node tree.
The constants exist only to give names to the arms of a `match`, and the arms cannot
use them: a bare symbol in pattern position is a variable binding, not a reference to
the binding of that name, so the dispatch must be written with the literal integers
`0` through `9` and the constants drift from the arms they name.

The common thread is a small, finite, mutually exclusive set of tags, compared for
identity and never for order or arithmetic. This is the shape of every state machine,
every option argument, and every discriminated union encoded as a tagged list.

Two of Menai's existing principles bear directly on the decision. ADR-0002 states that
symbols exist solely to support homoiconicity — code structure, not domain modelling —
so using `'idle`/`'running` as domain tags is a category error under Menai's own
design. ADR-0004 makes numeric typing strict so that operations are type-specific and
misuse is caught; an enum extends that principle to tags, where `regexp-op-empty + 1`
is currently legal and meaningless.

## Decision

### The type

Add an `enum` type: a nominal, closed set of named variants, compared for identity
only. Two enum types with identically named variants are distinct types, exactly as
two struct types with identically named fields are distinct (ADR-0018's nominal
structs).

An enum value is immutable, and is hashable unconditionally. It is a degenerate struct:
a struct whose fields are all zero-width. Hashability follows from the rule that a
value is hashable when it is immutable and has a total structural equality, which an
enum satisfies trivially. This is distinct from the vector type, which is excluded
from hashability because no use case has arisen (ADR-0018), not because of any
principle that would also exclude enums.

### Declaration

An enum type is declared with an `enum` form whose RHS lists the variant names:

```menai
(enum (idle running stopped))
```

An `enum` form is valid as the RHS of a `let`, `let*`, or `letrec` binding, and is
hoisted out of `letrec` exactly as a struct definition is, so a module can declare an
enum type alongside the functions that operate on it:

```menai
(letrec ((state (enum (idle running stopped)))
         (advance (lambda (s) ...)))
  (export state advance))
```

The binding name becomes the type name. The form produces an **enumtype** value — the
type descriptor — which is the exact analogue of a structtype. It is not an enum
instance.

### Construction

The enumtype is callable. Calling it with a quoted variant name constructs a value of
that variant:

```menai
(state 'idle)
(mode 'stored)
```

The variant name is a quoted symbol, not a bare name, for two reasons. It keeps the
variant namespaced by its enumtype, so that many enum types may each have a variant
called `done` without collision. And it is consistent with the existing convention
that names in a data position are quoted symbols, as in `(struct-get p 'x)`.

A variant name that is not declared by the enumtype is a compile-time error.

### Pattern matching

An enum is matched by naming the enum type and the variant to select. The type name
is resolved against the enclosing lexical binders, exactly as a struct pattern's type
name is. The variant is written as a quoted symbol, which makes it unmistakably a
name rather than a binding, and is what allows the pattern to name the variant that
the integer constants in `regexp.menai` cannot.

Matching an enum value against a pattern for a different enum type does not match, and
does not raise.

### Exhaustiveness

A `match` whose scrutinee is an enum value and which has no `_` arm must name every
variant of that enum type. Omitting a variant is a compile-time error.

A `_` arm disables the exhaustiveness check for that `match`, as it does for every
other pattern form.

Exhaustiveness is an error rather than a warning, deliberately, so that the check
cannot be tightened later without breaking programs that ignored the warning.

The check is defined as "which variants can this pattern match", compared against the
declared variant set, rather than "which tags appear in the arms". The former
generalises if variants later acquire payloads; the latter does not.

### Introspection

The enumtype operations mirror the structtype operations:

| Operation | Result |
|---|---|
| `(enumtype? x)` | `#t` if `x` is an enumtype value |
| `(enumtype=? a b)` | `#t` if `a` and `b` are the same enum type |
| `(enumtype!=? a b)` | negation of `enumtype=?` |
| `(enumtype-name state)` | `"state"` |
| `(enumtype-variants state)` | `('idle 'running 'stopped)` |

The enum instance operations mirror the struct operations:

| Operation | Result |
|---|---|
| `(enum? x)` | `#t` if `x` is an enum value |
| `(enum-variant (state 'idle))` | `'idle` |
| `(enum=? (state 'idle) (state 'idle))` | `#t` |
| `(enum!=? a b)` | negation of `enum=?` |

`enum=?` is `#f` for two values of different enum types, and for an enum compared with
a non-enum.

### No arithmetic, no ordering, no bitwise operations

An enum value supports identity comparison and nothing else. It has no arithmetic
operators, no ordering operators, and no bitwise operators. `(integer+ (state 'idle) 1)`
is a type error.

### Fixed-width representation

An enum value is represented by its enumtype's tag and its variant's index, both
integers assigned at compile time. The representation is fixed-width and does not grow
with the number of variants or the size of the program.

The C VM allocates an enum value as a fixed-size object holding the enumtype pointer
and the variant index. It does not use the inline trailing-array layout that
`MenaiStruct` uses, because a bare-tag variant carries no trailing entries to store.
See the first constraint under Scope below for why this was decided.

## Scope: bare tags now

This ADR decides the **bare-tag** enum: a variant is a name and carries no data.

A tagged union, in which a variant may carry a payload, is a larger feature — it
requires a pattern-matrix exhaustiveness algorithm rather than a variant-set
comparison, and it changes the value layout. It is not decided here.

Two constraints are recorded so that the bare-tag enum does not foreclose it:

1. **The value layout does not pre-reserve payload space.** An earlier draft of this
   ADR required the C VM to allocate an enum with the struct's inline trailing-array
   machinery, always with zero entries, so that adding payloads later would be
   "allow a non-zero count" rather than a re-layout. That requirement was dropped:
   it is speculative work for a feature that may never be built, and the project's
   YAGNI principle rejects speculative structure. A bare-tag enum is allocated as a
   fixed-size object. If payloads are added later, the value layout changes then, and
   that change touches the constructor, finalizer, equality, and hash paths — a cost
   accepted deliberately rather than pre-paid.

2. **Exhaustiveness must be computed as a variant set.** The checker asks which
   variants a pattern can match, and compares that set against the declared variants.
   It must not be written as a comparison of arm tags against the variant list, because
   that formulation does not extend to nested patterns.

2. **Non-exhaustiveness must be an error, not a warning.** If it were a warning, a
   later payload-carrying enum could not tighten it without breaking programs that
   ignored the warning.

The form is forward-compatible with payloads. A variant that carries data extends to
one that carries trailing elements, and a pattern that selects a variant extends to
one that binds those elements, exactly as a struct's positional fields extend. The
bare-tag enum is a restriction of the tagged union, not a different design, so
deciding it now does not foreclose the larger feature.

## Alternatives considered

### Constant-reference patterns

Teach `match` to resolve a bare symbol in pattern position against a binding whose
value is a compile-time constant, so that `(regexp-op-empty (k pos))` compares against
the constant rather than binding a variable.

Rejected. It would require the desugarer — which runs before the constant folder — to
carry a constant-propagation environment and to know the lexical scope at every
pattern, and it would make pattern meaning depend on what happens to be in scope, so
that introducing an unrelated binding could silently change whether a pattern binds or
compares. It also leaves the tag an `integer`, indistinguishable from any other
integer, so `(regexp-op-empty + 1)` remains legal. It buys the naming without the type.

### Symbols as tags

Use `'idle`/`'running` as tags, dispatched by symbol pattern and compared with
`symbol=?`.

Rejected. The set is not closed: a misspelled `'runnning` is a valid symbol, and a
`match` naming it silently never matches. There is no exhaustiveness check. And it
conflicts with ADR-0002, which reserves symbols for homoiconicity rather than domain
modelling.

### Strings as tags

The status quo, as seen in `deflate-compress`, `png-decode`, and `csv-decode`.

Rejected. A string tag is not a closed set, is not a distinct type, requires a
hand-written membership test and error message at every entry point, and is compared
with `string=?` rather than for identity.

### An enum type carrying payloads (tagged union)

Rejected for this decision, not in principle. It is the larger feature and it is the
one that would subsume `regexp`'s tagged-list node tree, but it requires a
pattern-matrix exhaustiveness algorithm and a different value layout. Deciding it here
would conflate two decisions of very different sizes. The Scope section records the
constraints that keep it reachable.

### A global variant namespace

Declare variants as bare names, so that `idle` is a value of type `state` without
qualification.

Rejected. With many enum types, two enums may each have a variant called `done`, and a
global namespace makes that a compile error. Variants must be namespaced by their
enumtype, which is also what makes the type nominal.

### An enum as a struct with a single tag field

Encode an enum as a struct whose only field is an integer or symbol tag.

Rejected. It gives no closure, no distinct type, and no exhaustiveness check, and it
allows the tag to be read out and used arithmetically. It is the current tagged-list
idiom with more ceremony.

### Non-exhaustive matches as a warning

Rejected: see constraint 3 under Scope. A warning is a promise that cannot be kept.

## Consequences

### Positive

- A closed set of tags becomes a distinct type. A pattern naming a variant that does
  not exist is a compile-time error, and a `match` that omits a variant is a
  compile-time error.
- The hand-rolled membership tests and error messages at the entry points of
  `deflate-compress`, `zlib-compress`, and `gzip-compress` are replaced by the type
  checker.
- An enum value is hashable, so a dict may be keyed by variant, which is the natural
  shape for "map each mode to its encoder".
- The integer constants in `regexp.menai` can be replaced by a named type, removing
  the drift between the constants and the literal integers the `match` arms must use.
- An enum discriminant is the natural input to the existing `MenaiCFGSwitchDispatch`
  pass, so a dispatch on an enum compiles to a jump table rather than a comparison
  chain.
- No arithmetic, ordering, or bitwise operations, so a tag cannot be accidentally
  treated as a number — the ADR-0004 principle applied to tags.
- The representation is fixed-width and does not grow with the program.

### Negative

- A new type increases the language's surface area: new opcodes, new C VM functions,
  new prelude wrappers, new documentation.
- Exhaustiveness checking requires the semantic analyzer to know the type of the
  matched expression, which is stronger than anything `match` does today. This is the
  largest piece of work in the feature.
- Variants are namespaced by their enumtype and written as quoted symbols, so
  `(state 'idle)` is more verbose than a bare `idle` would be. The verbosity is what
  makes many enum types coexist.
- A variant carries no data, so the tagged-list idiom remains necessary for
  discriminated unions until a tagged union is decided. The `regexp` node tree is not
  fixed by this ADR.
- The three forward-compatibility constraints under Scope are obligations on the
  implementation, and are easy to violate accidentally: an implementation that
  allocates a fixed-layout enum, or writes the exhaustiveness check as a tag
  comparison, would make the later tagged union a migration rather than an addition.
