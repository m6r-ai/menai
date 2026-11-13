# ADR-0062: Name-based lowering and validation must respect lexical shadowing

Date: 2026-11-13  
Status: Accepted

## Context

Menai resolves several things by name before any binding has been resolved. The
desugarer lowers a call to a builtin by name — `(integer+ a b)` becomes
`($integer+ a b)`, `(list-length x)` becomes `($list-length x)` — and the
semantic analyser validates call arity against the builtin registry by name.

Any non-reserved name can be bound by a `let`, a `lambda` or a `match`, so a
program may legally bind `integer+`, `list` or `vector`. A name-based rewrite that
ignores such a binding silently changes what the program means.

Pattern heads have the same hazard from the other direction. `(: list a b c)`
names a builtin sequence type that has no type binding in scope, so the head is
looked up by name against the builtin sequence types. But `list` and `vector` are
also ordinary prelude binding names, so an enclosing binder can shadow them with
a value that is not a type at all. Separately, struct and enum type names in head
position (`(Point 1 2)`, `(: state 'idle)`) resolve against the types the
enclosing lexical binders declare.

In each case the resolution is lexical, and the resolution decides the meaning of
the form.

## Decision

Every name-based lowering or validation resolves the name against the enclosing
lexical binders first, and treats it as a builtin only if no binder binds it.

- The desugarer applies this to every name-based rewrite: the `$`-prefix call
  rewrite, and the variadic arithmetic, fold, comparison-chain and strict
  equality/inequality special cases.
- The semantic analyser applies the same test to the arity check, so the two
  passes agree on what a call denotes.
- The builtin sequence pattern head lookup is gated the same way.
- A `:`-headed pattern head that resolves to a binding that is not a type is a
  compile-time error. It is never resolved to the builtin instead: that would
  match against a type the program never mentioned, which is a miscompile rather
  than a missed error.
- The prelude is desugared as an ordinary program, so its own top-level `let`
  bindings shadow the builtins. It must therefore write every call to an
  opcode-backed builtin in explicit `$`-prefixed form and must not rely on the
  name-based rewrite. Prelude-only functions, which have no primitive form, are
  called unprefixed.
- Struct and enum type recognition resolves against the lexical type scopes, which
  are tracked separately from value bindings. A struct type is not visible outside
  the binder that declares it, and two types with the same name in different
  scopes are distinct.

## Alternatives considered

### Resolve the builtin first, and let a user binding be an error

Rejects programs the semantics allow: `let` binds any non-reserved name, and
`(let ((list (lambda ...))) ...)` must mean the user's `list`. Rejecting it makes
the builtin vocabulary reserved by accident.

### Resolve the builtin first, silently

This is the failure the decision exists to prevent. Resolving a shadowed head to
the builtin silently ignores the user's binding and matches against a type the
program never mentioned. For a list pattern the degradation is also expensive: the
pattern compiles to a `list?`/`list-length`/`list-nth` chain, so a `match` over an
enum drops its jump table and can explode the CFG of every function it is inlined
into.

### Require the `$` form in user code

Leaks a lowering detail into the surface language and changes every existing
program. The prefix means "not a binding" — it is exactly the escape hatch the
prelude needs, and exactly what user code should not have to know about.

### Apply the check in one pass only

The desugarer and the semantic analyser both make name-based decisions, and the
desugarer runs first. A rewrite applied to a user binding cannot be undone by a
later arity check, so a single site is not enough; the two must apply the same
test.

## Consequences

### Positive

- Shadowing behaves the same way here as everywhere else in the language, so a
  user binding of a builtin name is an ordinary binding.
- A pattern head that names no type is rejected loudly instead of degrading into
  a list pattern.
- The semantics of a form never depend on which builtins happen to exist.

### Negative

- Two passes must each maintain a scope stack, and every new name-based decision
  must be gated on it. Forgetting the gate is silent: the form still compiles and
  still runs, with the wrong meaning.
- The prelude must be written in explicit `$` form throughout, which is noisier
  than the surface language and cannot use the variadic conveniences the rewrite
  provides.
- Struct and enum types need their own lexical scopes, separate from value
  bindings, because a type is used in head position before the form it heads can
  be desugared.

## Related

- [ADR-0026](0026-single-builtin-table.md) defines the registry a name is resolved
  against.
- [ADR-0044](0044-type-pattern-heads-are-syntactically-distinct.md) requires the
  `:` head on a type pattern, so that the form rather than the resolution of the
  name decides that a pattern is a type pattern.
- [ADR-0054](0054-let-is-the-only-binding-form.md) establishes `let` as the only
  binding form and its bindings as mutually visible.
- [ADR-0057](0057-typed-destructuring-patterns.md) covers the typed destructuring
  forms whose heads this decision resolves.
- [ADR-0023](0023-second-class-module-namespaces.md) covers how imported types
  reach the importer's lexical scope, which is what makes an imported struct type
  recognisable by name.
