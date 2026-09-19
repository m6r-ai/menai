# ADR-0022: No runtime globals

Date: 2026-09-18  
Status: Accepted

## Context

As a legacy of how Menai started we have carried an amount of "global" state into our
runtime.  This was in two forms, the "prelude" and injected "extra" bindings.

The prelude is the set of functions every Menai program can call without importing
anything — `integer+`, `map-list`, `string-slice`, and so on.  It is written as a
Menai source file whose top-level form is a `letrec` whose body is a dict of exports,
exactly like any other library module.

The injected extra bindings are used for inserting things like data from files, or
other I/O activities.

The global state hasn't posed a problem to the lexical nature of Menai, but it has
always felt a little "off".  We shouldn't actually need it.

The interprocedural type analysis (ADR-0021) recently added is whole-program operation.
It propagates argument facts into callee parameters, and it can only conclude anything
about a parameter if it can see every call site of the function.  A compilation unit
whose functions are called from code it cannot see therefore cannot be analysed
interprocedurally, so no type fact can be established for its parameters.  The prelude
is the sole example of such a compilation unit, but given its very common usage, the
loss of ability to do interprocudral optimizations involving it is a problem.

## Decision

Remove all global state from Menai and replace them with special binding operations
that inject them as a wrapper around the user's expression that will be evaluated.

The extra bindings wrap everything else and are introduced as a single dictionary in
the form of a `let` and then the prelude is introduced inside that `let`, retaining
it `letrec` form.  The user expression to be evaluated is then evaluated as the body
of that `letrec`.

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
- Optimizations such as dead code elimination can now operate over the prelude too.

### Negative

- The prelude can be partially pre-compiled but we want to expose it to optimization
  and this means there is a compile-time cost.
