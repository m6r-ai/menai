# ADR-0033: The CFG is an immutable value

Date: 2026-09-28  
Status: Accepted

## Context

ADR-0005 requires the IR tree to be immutable: a pass returns a new tree rather
than mutating its input. The CFG layer does not follow this. Its passes mutate
blocks, instruction lists, terminators, and the function's block list in place,
and they return the same function object they were given.

This asymmetry was not a decision. It grew because the CFG is a graph of
mutable objects and mutating it was the path of least resistance.

Mutability has already cost us. Two passes were found to re-apply their own
transformation on every invocation — guard insertion re-inserted guards it had
already inserted, and loop rotation re-rotated a loop it had already rotated.
Both bugs existed because a mutating pass cannot tell, on its next invocation,
what it did on its last one. With the pass manager now running each pass to its
own fixed point, that failure mode hangs the compiler.

There is a further, decisive constraint. Menai optimisation passes should one
day be writable **in Menai itself**. Menai is pure: it has no mutation and no
side effects. A Menai pass can only receive a value and return a new value. A
pass that mutates a Python object graph can never be written in Menai. If the
CFG is to be the interface to Menai-written passes, it must be a value.

## Decision

The CFG is an immutable value. No CFG pass may mutate its input.

- `MenaiCFGBlock` and `MenaiCFGFunction` become frozen. A pass that changes a
  block constructs a new block; a pass that changes a function constructs a new
  function. Instruction lists are held as immutable sequences.

- **Terminators reference blocks by id, not by object.** A terminator holds the
  integer id of its target block. This removes the cycle from the object graph
  (blocks no longer reference blocks), so frozen dataclasses need no two-phase
  freeze step. Block ids are plain integers, consistent with `MenaiCFGValue.id`.

- **Block ids are never reused within a function.** Because a terminator names
  its target by id, a dangling reference is an integer rather than a live
  object, and it would silently resolve to a different block if an id were
  reused. Every pass that creates a block must allocate an id above the
  function's current maximum. This is an invariant the type system does not
  enforce, and it is what makes id-based references safe.

- **Predecessors are derived, not stored.** The `predecessors` field and
  `relink_predecessors` are removed. Predecessors are computed from the
  terminators by a helper. Storing derived data on the node forced in-place
  updates and made stale predecessors possible; computing it removes both the
  mutation and the staleness.

- **Analysis results are not part of the tree.** Per-value type facts are
  analysis output, not program structure, so they cannot be a field on the
  function. They are carried in a separate results value that is threaded
  through the pass pipeline alongside the CFG (see below).

- **Passes receive a context.** The CFG pass signature becomes
  `optimize(root, context)`, where `context` carries cross-pass data — the
  analysis results, and any future state that is not part of the program. This
  is the home for shared cross-function structures that ADR-0020 deferred.

- **A pass that rebuilds a function must carry its facts over.** The context
  keys a function's facts by the function object's identity. A pass that
  changes a function returns a new object, so the facts recorded against the
  original would be orphaned and read as absent by the next pass. The
  per-function base class re-keys the facts from the original to its
  replacement whenever a function is rebuilt. Without this, a consumer such as
  guard insertion silently loses the analysis results and re-inserts guards the
  analysis had proven unnecessary.

## Relationship to ADR-0020

ADR-0020 rejected, "for now", introducing a first-class module object as a home
for shared cross-function structures, on the grounds that no pass needed one.
This ADR introduces that home — the context — for a reason ADR-0020 did not
anticipate: analysis results must outlive the tree they were computed from, and
an immutable tree has nowhere to put them.

ADR-0020's actual decision — that a pass's scope is declared by its base class,
`MenaiCFGPerFunctionPass` or `MenaiCFGWholeProgramPass` — is unchanged and
remains in force. Only its YAGNI deferral of a shared-structures home is lifted.

## Relationship to ADR-0034

This ADR is an instance of the general standard stated in ADR-0034: every
compiler phase is a pure function over immutable values. The CFG was the first
layer brought into conformance; the IR, VCode, and bytecode models, and the
front end, are the remaining work.

## Alternatives considered

### Keep the CFG mutable and rely on discipline

Require every pass to be idempotent by convention. This is the status quo, and
it is what produced the two bugs above. It also permanently forecloses
Menai-written passes. Rejected.

### Store predecessors on the new blocks as they are constructed

Every pass that builds blocks would relink predecessors. This preserves the
current cost profile, but it keeps derived data on the node, keeps the
stale-predecessor hazard, and adds a construction obligation to every pass. The
computation is cheap and the readers are few, so deriving it is simpler.
Rejected.

### Terminators reference blocks by object

Keeping object references in terminators avoids changing every dereference
site, but it leaves a cycle in the object graph. Constructing a frozen cyclic
graph requires either a two-phase freeze (build blocks, build terminators,
re-create blocks, patch terminators) or a mutable construction window. Both are
fiddly and easy to get subtly wrong, and neither removes the cycle. Ids trade a
mechanical change at each dereference for a graph with no cycles. Rejected.

### A distinct block-reference type instead of a plain int

A wrapper type would keep block ids and value ids from being confused, but it
does not prevent a reference to a removed block, which is the failure mode that
actually matters. Plain ints match the existing `MenaiCFGValue.id` convention.
Rejected in favour of plain ints plus the never-reuse invariant.

### Thread analysis results as extra return values from the pass

The analysis pass would return `(tree, facts, changed)`. This changes the pass
contract for every pass to accommodate one, and gives no home to any future
cross-pass data. Rejected in favour of the context.

### Recompute the type facts independently in each consumer

`PredicateFold` and `GuardInsertion` would each run their own fact computation.
This avoids a shared channel but duplicates an expensive whole-program analysis,
and the two consumers must agree on the facts or the compiler is inconsistent.
Rejected.

## Consequences

### Positive

- The CFG is a value, so a pass cannot corrupt its input, and a pass that is
  run twice cannot see the effects of its own previous run. Idempotency becomes
  structural rather than a discipline.
- The CFG becomes a viable interface for passes written in Menai.
- Derived data cannot go stale, because it is not stored.
- The context gives cross-pass data a defined home instead of a field smuggled
  onto the tree.

### Negative

- Every CFG pass must be rewritten to construct new blocks and functions rather
  than mutate. This is a large, mechanical change across the whole CFG layer.
- Rebuilding blocks allocates more than mutating them. The CFG is traversed far
  less often than the IR, so the cost is expected to be acceptable, but it must
  be measured.
- Predecessors are recomputed on each query. A pass that queries them
  repeatedly should compute them once and hold the result locally.
