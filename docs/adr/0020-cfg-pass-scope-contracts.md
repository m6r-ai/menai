# ADR-0020: CFG pass scope contracts — per-function and whole-program

Date: 2026-09-17  
Status: Accepted

## Context

CFG optimisation passes originally shared a single contract: a pass was handed
one `MenaiCFGFunction`, transformed it, and returned it with a changed flag.
The base class recursed into nested lambdas automatically, so a pass author
wrote `_optimize_function` and never dealt with the function tree.

This contract is correct for intraprocedural passes — the large majority —
whose analyses are defined over a single control-flow graph. It also acts as a
guardrail: because a pass can only see one function, it cannot accidentally
grow a cross-function dependency that its analysis does not support.

Some optimisations, however, are inherently interprocedural. Function inlining
and interprocedural type analysis both need a view of the whole call graph.
Under the per-function contract such a pass has to take the root function and
perform its own traversal internally — it is secretly a whole-program pass
wearing a per-function signature, which is misleading to readers and hides an
important property (its scope) from the type system.

The question was how to accommodate whole-program passes without weakening the
guardrail that keeps intraprocedural passes honest.

## Decision

Passes operate at module scope. There is no separate module object: the module
is the root `MenaiCFGFunction` plus every function reachable from it through
`MenaiCFGMakeClosureInstr` instructions, enumerated by `collect_functions`.

Two pass contracts are provided as subclasses of a common base:

- `MenaiCFGPerFunctionPass` — the transformation is defined per function.
  Subclasses implement `_optimize_function`; the base class performs the
  traversal of the function tree and the write-back of any replaced nested
  functions. This preserves the original authoring ergonomics and the
  original guardrail.

- `MenaiCFGWholeProgramPass` — the transformation needs a whole-program view.
  Subclasses implement `_optimize_module`, which is handed the root function;
  the subclass owns its own traversal and write-back.

The pass manager is unchanged: it holds a list of passes and calls
`optimize(root)` on each, so the pipeline remains uniform. The distinction
between the two kinds of pass lives in the class hierarchy, where a pass's
scope is visible at its declaration site, rather than in the pipeline.

## Alternatives considered

### Keep the single per-function contract and let whole-program passes fake it

Whole-program passes would continue to take the root function and traverse the
tree themselves. This avoids a framework change, but it makes a pass's scope
invisible: a reader cannot tell from `optimize(func)` whether the pass is
intraprocedural or spans the whole call graph. It also removes the guardrail
for intraprocedural passes, since every pass is handed something it could
reach across.

### Introduce a `MenaiCFGModule` object

A first-class module object owning a function registry would give whole-program
passes a natural place to hang call-graph structures. It was rejected for now
under YAGNI: no pass currently needs shared cross-function structures beyond
the function tree itself, and the root function already provides the entry
point. A module object can be introduced later if a second whole-program pass
demonstrates the need.

### A second pipeline slot for whole-program passes

The pipeline could distinguish an intraprocedural phase from an
interprocedural phase, with a separate pass list for each. This makes the
ordering explicit but complicates the manager and the compiler. Encoding the
distinction in the class hierarchy achieves the same clarity with a uniform
manager.

## Consequences

### Positive

- A pass's scope is explicit at its declaration site: subclassing
  `MenaiCFGPerFunctionPass` or `MenaiCFGWholeProgramPass` states whether it is
  intraprocedural or whole-program.
- Intraprocedural passes keep their simple authoring model and their
  guardrail: they implement `_optimize_function` and see one function at a
  time.
- Whole-program passes are first-class rather than smuggled through a
  per-function signature.
- The pass manager and the compiler pipeline stay uniform.

### Negative

- A per-function pass is now handed the root function rather than a single
  function, so the guardrail is a convention (implement `_optimize_function`)
  rather than a structural impossibility. A pass author who wants cross-
  function information can reach for it even in a per-function pass.
- Whole-program passes must implement their own traversal and write-back,
  duplicating some of the machinery the per-function base class provides.
