# ADR-0044: Type pattern heads are syntactically distinct

Date: 2026-10-07  
Status: Accepted

## Context

A `match` pattern is resolved by name against the enclosing lexical scope. When a
pattern head names a struct or enum type, the pattern is a destructuring pattern for
that type; otherwise the same shape is an ordinary list pattern. The meaning of a
pattern therefore depends on what types happen to be in scope, and introducing an
unrelated type binding can silently change the meaning of existing code.

This is the hazard ADR-0043 rejected when it turned down constant-reference patterns:

> It would make pattern meaning depend on what happens to be in scope, so that
> introducing an unrelated binding could silently change whether a pattern binds or
> compares.

The struct case is that hazard, live. `(Name p1 ... pn)` is both a list pattern and a
struct pattern, and there is no shape that distinguishes them. When `Name` names a
struct type with `n` fields, the desugarer compiles the pattern as a struct pattern.
This happens with no modules, no imports, and no shadowing:

```menai
(letrec
  ((a (struct (x)))
   (f (lambda (v) (match v ((a b) "matched") (_ "other")))))
  (list (f (list 1 2)) (f (a 3))))
→ ("other" "matched")
```

`(f (list 1 2))` does not match, because `(a b)` was compiled as a struct pattern for
the one-field struct `a`. There is no way to write a two-variable list pattern when
the first variable collides with a matching struct type.

The failure is not confined to a silent wrong answer. In a nested pattern the same
misresolution produces a runtime type error, because the struct pattern's field
extraction is applied to a list:

```menai
(letrec
  ((a (struct (x)))
   (f (lambda (v) (match v ((p (a b) c) "nested") (_ "other")))))
  (f (list 1 (list 2 3) 4)))
→ runtime error: type mismatch
```

The enum case is a variant of the same defect. `(Name 'variant)` is unambiguous — a
quoted symbol is not a valid list sub-pattern, so that shape can only be an enum
pattern — but when the head does not resolve to an enum type in scope the desugarer
silently falls back to treating the pattern as a list pattern, which cannot match an
enum value. Every arm then falls through to `_`, and the program compiles and runs and
produces wrong results with no diagnostic. This is the failure mode AGENTS.md already
records under "Modules export named bindings; namespaces are second-class":

> If a type pattern head is not renamed, the desugarer can no longer resolve it to the
> type binding and silently falls back to treating the pattern as a list destructuring
> pattern. That is not a local failure: a list pattern compiles to a
> `list?`/`list-length`/`list-nth` test chain, so a `match` over an enum degrades from a
> jump table into a long comparison chain.

The invariant is documented, but the failure it describes is silently accepted rather
than rejected.

The common cause is that a type pattern head is written as a bare symbol and is
resolved by name. Menai already applies the opposite principle elsewhere. AGENTS.md
records, of member access:

> Member access is a disjoint form: the shape of the form decides its meaning.

Pattern heads are the one place that principle is not applied.

## Decision

A pattern head that names a type is written with a leading `:` head symbol, following
the shape Menai already uses for predicate patterns:

```menai
(match p
  ((: Point x y) (integer+ x y))
  (_ 0))

(match s
  ((: state 'idle) "idle")
  ((: state 'running) "running")
  (_ "other"))
```

The pattern is a list whose first element is the symbol `:` followed by the type name.
This mirrors `(? pred var)`, where `?` is a reserved head symbol followed by operands.
There is then one rule for a pattern whose head is a reserved symbol, rather than a
second mechanism based on a character embedded in a symbol name.

The `:` is a separate token from the type name. `(: Point x y)` is four elements; it is
not the single symbol `:Point`. The type name remains an ordinary symbol, so it is
renamed by the module resolver exactly as any other reference to a module binding.

### What changes and what does not

Only the **pattern head** gains the `:` marker. Declaration and construction are
unchanged:

```menai
(let ((point (struct (x y))))       ; declaration unchanged
  (let ((p (point 3 4)))            ; construction unchanged
    (match p
      ((: Point x y) ...))))        ; pattern head marked
```

The predicate form for matching a struct type without destructuring is unchanged; it
does not name a type in head position.

### Resolution and errors

A `(: TypeName ...)` pattern head is a type reference. It is resolved against the
lexical scope for a struct or enum type binding, and is not subject to shadowing by a
variable of the same name in the way a bare symbol is — the `:` makes the intent
explicit, so the head is looked up as a type.

A `(: TypeName ...)` whose head does not resolve to a struct or enum type in scope is a
compile-time error. It is never interpreted as a list pattern.

A bare `(Name p1 ... pn)` is now always a list pattern, whatever `Name` denotes. It is
never interpreted as a struct pattern.

The arity of a struct pattern must match the type's field count, and an enum pattern
must name a declared variant; both are existing checks and are unchanged.

### Exhaustiveness

The exhaustiveness obligation recorded in ADR-0043 is unaffected by this decision. It
remains unimplemented, and when implemented it must be an error rather than a warning,
computed as a variant set.

## Alternatives considered

### Keep the bare-symbol head and reject the ambiguity

Reject a list pattern whose head names a struct type of matching arity. Rejected: there
is no syntactic way to tell a list pattern from a struct pattern, so this would ban
list patterns whose head happens to collide with a struct name, which is surprising and
still leaves the meaning of a pattern dependent on scope.

### Keep the bare-symbol head and document the ambiguity

Rejected. The defect is a silent miscompile, and in nested patterns a runtime type
error. Documenting it does not remove it, and the hazard is exactly the one ADR-0043
rejected for constants.

### Mark only struct patterns

Rejected. The enum case is the same defect with the same cause, currently masked only
because an enum pattern's shape happens to be unambiguous. Fixing structs alone leaves
the enum case latent, and ADR-0043 records that the surface syntax is forward-compatible
with payloads — the moment a variant carries a payload, `(: state 'running task)` would
otherwise become ambiguous with a list pattern in precisely the same way.

### A sigil embedded in the type name

Use a single symbol such as `:Point` rather than a separate `:` head. Rejected in favour
of the separate head, which reuses the `(? pred var)` mechanism and keeps the type name
an ordinary symbol that the module resolver renames unchanged.

### A currently-unused character

Characters rejected by the lexer today are `@`, `[`, `]`, `{`, `}`, `\`, and `` ` ``.
Rejected: each has a misleading conventional meaning, and `{` and `}` conflict with
Menai's own dict display syntax.

### Change only the pattern, not the type name

This is the decision: the marker is on the pattern head, and the type name is unchanged
in declaration and construction.

## Consequences

### Positive

- A pattern's meaning is decided by its form, not by what types are in scope. This is
  the principle AGENTS.md already states for member access, applied to pattern heads.
- The struct/list ambiguity is removed, including the nested case that currently raises
  a runtime type error.
- The enum silent fallback is removed: an unresolvable type head is a compile-time error
  rather than a list pattern.
- The change extends to payload-carrying variants without further ambiguity, preserving
  ADR-0043's forward-compatibility.
- No lexer change is required. `:` is already a valid symbol character, and `(: ...)` is
  a list whose head is the symbol `:`.

### Negative

- This is a breaking change to every existing struct and enum pattern in the standard
  library, the test suites, the benchmarks, and the documentation. The change is a
  repo-wide text sweep, not a local edit; see the module-rename invariant in AGENTS.md
  for the sweep discipline.
- A pattern head is now one element longer, and the reader must know that `:` is a
  reserved head symbol.
- ADR-0043's statement that the surface syntax is forward-compatible with payloads
  should be read together with this ADR: the forward-compatibility is preserved, but
  the pattern head now carries the `:` marker.
