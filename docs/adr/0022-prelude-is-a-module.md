# ADR-0022: The prelude is a module

Date: 2026-09-18  
Status: Accepted

## Context

The prelude is the set of functions every Menai program can call without importing
anything — `integer+`, `map-list`, `string-slice`, and so on. It is written as a
Menai source file whose top-level form is a `letrec` whose body is a dict of exports,
which is the shape of a standard library module.

The interprocedural type analysis (ADR-0021) is whole-program: it propagates argument
facts into callee parameters, and it can only conclude anything about a parameter if
it can see every call site of the function. A compilation unit whose functions are
called from code it cannot see therefore cannot be analysed interprocedurally, and no
type fact can be established for its parameters.

That is the question this decision turns on: is the prelude part of the program, or is
it a separate unit the program calls into?

## Decision

The prelude is a module that is auto-imported. Its bindings are spliced around every
program as ordinary lexical bindings, so every prelude name is a lexical binding and
every prelude function is an ordinary closure in the compilation.

This makes the prelude part of the program rather than a separate unit, which is what
lets whole-program analysis apply to it: its call sites are all present in the
compilation.

Splicing happens once, at the top level, before module resolution. Module ASTs are
inlined into the parent by the module resolver, so resolving modules after splicing
places them inside the prelude's lexical scope and they see the prelude bindings
without carrying a copy of their own.

The prelude must not import anything, because its imports would be resolved before the
point at which its bindings are spliced in.

Shadowing follows from lexical scoping: a user binding that shadows a prelude name
wins because it is bound in an inner scope, as with every other shadowing relationship
in the language.

The `$`-prefixed primitive forms are unaffected. The `$` prefix resolves a call to the
underlying opcode before name resolution is involved, so it is orthogonal to how the
prelude's public wrappers are bound.

## Alternatives considered

### Import the prelude as a dict

The prelude could be imported as a dict like any standard library module, with each
name bound via `dict-get`. This is the most uniform treatment, but it puts a `dict-get`
on the path of every prelude call and requires binding several hundred names. Splicing
the bindings directly keeps prelude functions as plain lexical closures, which is what
the inliner and the type analysis want.

### Keep the prelude as a separate compilation unit

The prelude could remain a separate unit with its special handling consolidated behind
a single "external function" abstraction shared with modules. This reduces the scatter
of the special cases but not the underlying limitation: a separate unit still cannot
expose its call sites, so whole-program analysis remains blocked on the code that is
called most. It was rejected as tidying rather than fixing.

### Make modules runtime globals instead

Unifying in the other direction — making modules runtime globals like the prelude —
would worsen the problem, since no imported function's call sites would then be visible
to whole-program analysis.

## Consequences

### Positive

- Whole-program analysis applies to prelude functions, so struct field access inside
  them can be resolved to a constant index.
- No part of the compiler needs prelude-specific resolution: prelude names resolve
  through the same lexical scope machinery as any other binding.
- Shadowing is lexical, consistent with the rest of the language, rather than a
  property of global-table merge order.

### Negative

- The prelude is desugared, lowered, and optimised as part of every compilation. This
  is a compile-time cost, though not an output-size one: dead code elimination removes
  prelude functions the program does not call.
- Every compiled program contains the prelude functions it uses as nested code objects,
  so code that navigates a compiled program's code objects by position must locate the
  program's own functions rather than assuming an index.
- The prelude cannot use imports, since its bindings are spliced in before module
  resolution.
