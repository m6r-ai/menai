# Module system exploration

Status: **Resolved** — the design session took place and the decision is recorded
in [ADR-0023](adr/0023-second-class-module-namespaces.md). This document is kept as
the problem statement that motivated the change: it records the problem, the
evidence, and the design space that was explored. The chosen approach was Option A
(a namespace with resolved members), made second-class.

The rest of this document is the original brief, unchanged, except where noted.

## Why this is worth doing now

Menai is early. Only Humbug and this repository use it, and Humbug does not use
modules at all. That means the module system can be changed freely, with no
external compatibility burden. If the module system is going to be reworked, now
is the cheapest time.

## The problem in one sentence

A module is an expression that evaluates to a dictionary, and importers reach its
exports through `dict-get` with string keys. Because exports are opaque runtime
values with no compile-time identity, the compiler cannot connect an imported
value back to the declaration that produced it — and several passes pay for that
loss with fragile, special-case analyses.

## How the module system works today

- A module is a `.menai` file whose single top-level expression evaluates to a
  value, conventionally a `(dict ...)` of exports.
- `(import "name")` is a compile-time operation. The module resolver loads the
  module, compiles it (lex/parse/analyse/resolve), and **inlines its AST** in
  place of the import expression.
- The importer then reads exports with `(dict-get <module-value> "key")`.
- Module names are strings; export keys are strings; the only relationship
  between a module's `(dict "point" ...)` and an importer's
  `(dict-get shapes "point")` is string equality, which no static analysis sees.

See `docs/modules.md` for the user-facing description.

## Symptom 1: struct types cannot cross a module boundary as pattern heads

Struct destructuring patterns `(Point x y)` are resolved at compile time: the
desugarer must know statically that `Point` names a struct type (and its field
count). The compiler *does* see the struct declaration — module resolution inlines
the module AST, so `(letrec ((Point (struct (x y)))) ...)` is physically present
in the importer's program.

But the importer reaches the value through `(dict-get shapes "Point")`, which the
compiler sees only as "a dict lookup with a string key". The static link between
the declaration and the imported value is severed at the `dict-get`, so the type
is not available as a pattern head.

This was recently made *sound* (struct recognition is now lexically scoped), at
the cost of the capability: an imported struct type can no longer be used as a
destructuring pattern head. Importers must read fields with `struct-get` /
`struct-ref`. See AGENTS.md, "Struct type recognition is lexically scoped".

The capability is not inherently impossible — it is lost only because exports
carry no compile-time identity. If `(import "shapes")` bound a namespace whose
members were resolved references, `shapes.point` would denote the declaration
`point` and the type would cross the boundary intact.

## Symptom 2: the type analysis has to simulate dict semantics

The interprocedural type analysis (`src/menai/cfg/menai_cfg_interproc_type_analysis.py`)
resolves which function an SSA value denotes so it can follow calls. Because a
module is a dict of functions and importers fetch them with `dict-get`, the pass
must resolve `(dict-get <dict> <key>)` by *simulating dict lookup semantics*.

The relevant code is `_dict_get_callee` (line 297). To resolve a
`dict-get` it must:

- confirm the dict operand is a `make_dict` it can see;
- confirm the key is a constant string;
- walk the dict's pairs and, because dicts are **last-wins** for duplicate keys,
  take the *last* pair whose key could equal the lookup key;
- **bail out** if any later pair has a non-constant key, because at runtime that
  key could equal the lookup key and override the match.

That is a miniature dataflow analysis of dict construction and lookup, existing
solely to recover the identity of a module export. With a real module construct
whose members are resolved references, this whole path disappears: the callee of
a call to `shapes.point` would be known directly, exactly as a call to a local
`letrec` sibling is.

The same pass also documents (module docstring, "Callee resolution") that the
dict-get path is "how a module's exported functions are reached" — i.e. the
special case is load-bearing for the module idiom, not incidental.

## Symptom 3: guard elimination works around container opacity

The guard insertion pass (`src/menai/cfg/menai_cfg_guard_insertion.py`) inserts
runtime type guards where a type-specific opcode receives an operand whose type is
not statically known. It consumes the type facts from the interprocedural
analysis. Two consequences of dict-based modules show up here:

- **`dict-get` results are typed `ANY`.** In `_builtin_fact`, `dict-get` is one of
  the operations in the type analysis whose result "depends on the input", so it is typed `ANY` (not
  `BOTTOM`). Every value fetched from a module dict therefore starts life with no
  usable type, and guards are inserted on it until something refines it. A
  resolved reference to a module member would carry its real type (e.g. a function
  type, or a struct type) directly.

- **Function-valued exports are invisible to type propagation.** Because a
  module's functions are reached through `dict-get`, the type facts for their
  results do not flow to the importer the way they do for a lexically-bound
  function. The interprocedural analysis compensates by resolving the callee
  through the dict (symptom 2), but the *result type* of an exported function is
  still not propagated to the importer through the dict-fetch; it has to be
  re-derived per call site.

The net effect: the guard-elimination machinery carries special handling whose only
purpose is to see through the dict that stands between a module and its importer.

## Why not just make the prelude's approach universal?

ADR-0022 ("No runtime globals") made the prelude lexical: its bindings are spliced
into every program as ordinary `letrec` bindings, so the compiler keeps full static
knowledge of them. Modules were deliberately left as dicts. The two are the same
problem addressed two different ways, and the module case is the one that kept the
opaque-value approach.

A natural direction is therefore to give modules the same treatment the prelude
gets: make import introduce *lexical bindings* rather than a dict value.

## Design space to explore

These are sketches, not proposals. Each needs its costs worked out.

### Option A: import binds a namespace with resolved members

`(import "shapes")` binds a namespace value; member access `(shapes point)` (or
`shapes.point`) is a resolved reference, not a runtime dict lookup. The compiler
knows which declaration each member denotes, so struct types, function types, and
callee identities all cross the boundary intact.

Open questions:
- Syntax for member access, and how it interacts with the S-expression grammar.
- Is a namespace a first-class value (can it be passed around, stored in a list)?
  If yes, the resolution problem comes back for indirect access. If no, what are
  the restrictions?
- How does this interact with `dict-get`? Are namespaces distinct from dicts, or a
  special case of them?

### Option B: import splices the module's bindings lexically (prelude-style)

`(import "shapes")` introduces the module's exports as lexical bindings in the
importing scope, exactly as the prelude injector does. No dict, no `dict-get`.
The compiler sees every export as an ordinary binding.

Open questions:
- Name collisions: two modules exporting the same name. The prelude avoids this by
  there being only one prelude; modules do not have that luxury.
- How are private (non-exported) module bindings kept private if the whole module
  body is spliced?
- Does this scale to many imports without namespace pollution?

### Option C: typed export records

Keep modules returning a value, but make the export record a distinct type whose
members are statically resolvable, rather than a general dict. Closer to today's
shape; may preserve more of the existing code.

Open questions:
- Is this meaningfully different from Option A, or the same idea with different
  surface syntax?
- Does it actually solve symptom 1, or only symptoms 2 and 3?

### Option D: keep dicts, add provenance tracking

Teach the compiler that certain dict values are module export dicts and that key
`"point"` denotes declaration `point`. Rejected on first inspection as fragile and
against the language's lexical grain, but worth recording as considered.

## Constraints and existing decisions to respect

- **ADR-0022 (no runtime globals).** Whatever we do must not reintroduce global
  state. The prelude solution is the model to study.
- **ADR-0021 (interprocedural type analysis).** A module construct that exposes
  member identities should *reduce* the special-casing this pass needs, not add to
  it. This is a good acceptance criterion: does the change let us delete
  `_dict_get_callee`?
- **Lexical scoping everywhere.** The recent shadowing and struct-scoping fixes
  moved the language firmly toward lexical semantics. A module design that is
  lexical will fit; one that depends on runtime value identity will not.
- **Purity / no side effects.** Modules are compile-time; nothing here changes that.
- **YAGNI.** Do not build a general namespace/record system beyond what modules
  actually need.

## Acceptance criteria to aim for

A good outcome would let us:

1. Use an imported struct type as a destructuring pattern head, soundly.
2. Delete the dict-simulation in `_dict_get_callee` (or reduce it to nothing).
3. Type module exports directly, so `dict-get`-typed-`ANY` no longer stands between
   a module and its importer.
4. Keep the prelude's lexical treatment as the model, with no new global state.

## Files to read before the session

- `docs/modules.md` — current user-facing module semantics.
- `src/menai/ast/menai_ast_module_resolver.py` — how import inlines module AST.
- `src/menai/ast/menai_ast_prelude_injector.py` — the lexical alternative, already
  in use for the prelude.
- `src/menai/cfg/menai_cfg_interproc_type_analysis.py` — `_dict_get_callee` and the
  "Callee resolution" docstring, and `_builtin_fact` (which types `dict-get` as
  `ANY`).
- `src/menai/cfg/menai_cfg_guard_insertion.py` — guard insertion, which consumes
  the type facts.
- `docs/adr/0022-no-runtime-globals.md` — the prelude decision and its reasoning.
- `docs/adr/0021-interprocedural-type-analysis.md` — the type analysis design.

## What a session should produce

Either a decision (recorded as an ADR, superseding or extending ADR-0022 as
needed) or a narrowed set of options with their costs. Given the project's stage,
a decision is preferable if the options can be evaluated cleanly.
