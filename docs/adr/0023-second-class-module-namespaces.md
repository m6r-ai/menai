# ADR-0023: Second-class module namespaces

Date: 2026-09-19  
Status: Accepted

## Context

A module is a `.menai` file whose expression evaluates to a value, conventionally a
`(dict ...)` of exports. `(import "name")` is a compile-time operation that inlines the
module's AST, and importers reach exports through `(dict-get <module-value> "key")`.

Because exports are opaque runtime values with no compile-time identity, the compiler
cannot connect an imported value back to the declaration that produced it. Three
consequences follow (see `docs/module_system_exploration.md` for the full analysis):

- A struct type exported by a module cannot be used as a destructuring pattern head,
  because struct recognition is lexically scoped and the `dict-get` severs the static
  link to the declaration.
- The interprocedural type analysis must simulate dict construction and lookup
  (`_dict_get_callee`) purely to recover the identity of an exported function.
- Values fetched from a module dict are typed `ANY`, so guard insertion cannot suppress
  guards on them, and function result types do not flow to the importer through the fetch.

The prelude faced the same problem and was solved lexically (ADR-0022): its bindings are
spliced into every program as ordinary `letrec` bindings, so the compiler keeps full
static knowledge of them. Modules were deliberately left as dicts.

Menai is early and the module system is used only within this repository and its standard
library, so there is no external compatibility burden. If the module system is to be
reworked, now is the cheapest time.

## Decision

Replace the dict-fetch module idiom with **second-class namespaces**.

`(import "name")` continues to be an expression, but it now denotes a *namespace* — a
compile-time construct, not an ordinary runtime value. A namespace may only be bound
directly by a `let`/`let*`/`letrec` binding, and is accessed through member access on the
bound name:

```menai
(let ((shapes (import "shapes")))
  (:: shapes point-distance))
```

`(:: shapes point-distance)` is a resolved reference to the declaration that produced the
member, not a runtime dict lookup. The compiler therefore knows which declaration each
member denotes, so struct types, function identities, and result types all cross the
module boundary intact.

A namespace is **second-class**: it may not be passed to a function, stored in a
container, or returned from a function. Any use other than a direct binding followed by
member access is a compile error. This restriction is what guarantees that every
member-access site is statically resolvable — the property the whole design depends on.

A module's exports remain its dict keys, so privacy is unchanged: a non-exported binding
is simply absent from the dict and therefore absent from the namespace.

`dict-get` remains a general builtin for ordinary dicts. Only its role in reaching module
exports is removed, along with the module-specific special-casing in the interprocedural
type analysis.

## Alternatives considered

### First-class namespaces (the Clojure model)

A namespace could be an ordinary value that may be passed, stored, and returned, with
compile-time resolution for direct access and a runtime lookup fallback for indirect
access. This is coherent and is what Clojure does, but it does not achieve the goal: the
indirect path still requires runtime member resolution, so the `_dict_get_callee`
machinery (or an equivalent) survives, and the acceptance criterion of deleting it is not
met. It is strictly more work and more surface area for a capability nothing currently
needs.

### First-class namespaces with a static type system (the Scala `object` model)

Scala's first-class modules work because the type system names the object at every use
site, so members resolve statically even through a parameter. Menai has no static type
system — the interprocedural analysis is explicitly a best-effort optimisation, not a type
system (ADR-0021) — so this route is unavailable without introducing a type system, which
is a language-design decision far beyond the module system.

### Lexical splice of module bindings (the prelude model, Option B)

`(import "name")` could introduce the module's exports as lexical bindings in the
importing scope, exactly as the prelude injector does. This achieves the same resolution
benefit, but it has no answer for name collisions between modules (the prelude avoids this
only by there being exactly one prelude) and it makes private bindings hard to keep
private. Namespaces with member access avoid both problems.

### Keep dicts, add provenance tracking (Option D)

Teach the compiler that certain dict values are module export dicts and that a given key
denotes a given declaration. Rejected: it depends on runtime value identity and is against
the lexical grain the language has moved firmly toward.

## Consequences

### Positive

- An imported struct type can be used as a destructuring pattern head, soundly, once bound
  to a local name (the existing lexical struct machinery recognises it).
- The dict-simulation in the interprocedural type analysis (`_dict_get_callee`) is deleted.
- Module exports are typed directly, so `dict-get`-typed-`ANY` no longer stands between a
  module and its importer, and function result types flow across the boundary.
- The design is lexical, consistent with the prelude (ADR-0022) and with the shadowing and
  struct-scoping decisions, and introduces no global state.

### Negative

- `(import "name")` can no longer be used as a value. Code that passed a module around,
  stored it in a container, or returned it must be rewritten to bind the members it needs.
- A module that returns a non-dict value has no namespace members; only dict exports are
  addressable.
- Member access is a new form, `(:: namespace member)`, that the semantic analyser and
  desugarer must recognise, and both must enforce the second-class restriction
  (rejecting a namespace used anywhere other than a direct binding or the first
  argument of `::`).
